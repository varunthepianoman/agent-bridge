from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from agent_bridge_coordinator.codex import CodexCoordinatorModel
from agent_bridge_coordinator.models import CheckpointDraft, CoordinatorModelOutput
from agent_bridge_protocol.models import CoordinatorRole, RoleStatus


def role() -> CoordinatorRole:
    return CoordinatorRole(
        role_id="role-1",
        role_type="work_coordinator",
        scope="work:one",
        charter="Coordinate one work item",
        authority_profile="delegate-bounded",
    )


def response() -> str:
    return CoordinatorModelOutput(
        checkpoint=CheckpointDraft(
            objective="Finish work",
            status=RoleStatus.ACTIVE,
            parent_summary="Work remains active",
        )
    ).model_dump_json()


class FakeThread:
    def __init__(self, thread_id: str, final_response: str) -> None:
        self.id = thread_id
        self.final_response = final_response
        self.run_options: dict[str, Any] = {}

    async def run(self, _prompt: str, **options: Any) -> Any:
        self.run_options = options
        usage = SimpleNamespace(total=SimpleNamespace(input_tokens=12, output_tokens=3))
        return SimpleNamespace(final_response=self.final_response, usage=usage)


class FakeCodex:
    instances: list[FakeCodex] = []
    final_response = response()

    def __init__(self) -> None:
        self.closed = False
        self.options: dict[str, Any] = {}
        self.thread: FakeThread | None = None
        self.instances.append(self)

    async def __aenter__(self) -> FakeCodex:
        return self

    async def __aexit__(self, *_args: object) -> None:
        self.closed = True

    async def thread_start(self, **options: Any) -> FakeThread:
        self.options = options
        self.thread = FakeThread("thread-new", self.final_response)
        return self.thread

    async def thread_resume(self, thread_id: str, **options: Any) -> FakeThread:
        self.options = options
        self.thread = FakeThread(thread_id, self.final_response)
        return self.thread


@pytest.fixture
def fake_sdk(monkeypatch: pytest.MonkeyPatch) -> Any:
    FakeCodex.instances.clear()
    FakeCodex.final_response = response()
    sdk = SimpleNamespace(
        AsyncCodex=FakeCodex,
        ApprovalMode=SimpleNamespace(deny_all="deny"),
        Sandbox=SimpleNamespace(workspace_write="workspace"),
    )
    monkeypatch.setattr("agent_bridge_coordinator.codex.importlib.import_module", lambda _name: sdk)
    return sdk


async def test_codex_coordinator_uses_schema_deny_all_and_tracks_usage(fake_sdk: Any) -> None:
    model = CodexCoordinatorModel(model="model-a")
    session = await model.prepare(
        role(),
        current_conversation_id="conversation-old",
        current_provider_thread_id="thread-old",
        cwd=None,
    )
    turn = await model.run(session, "context")
    instance = FakeCodex.instances[0]
    assert instance.options["approval_mode"] == "deny"
    assert instance.thread is not None
    assert "output_schema" in instance.thread.run_options
    assert turn.usage.total_tokens == 15
    assert instance.closed


async def test_codex_coordinator_rejects_prose_and_still_closes(fake_sdk: Any) -> None:
    FakeCodex.final_response = "Here is a plan, but not JSON"
    model = CodexCoordinatorModel()
    session = await model.prepare(
        role(), current_conversation_id=None, current_provider_thread_id=None, cwd=None
    )
    with pytest.raises(ValidationError):
        await model.run(session, "context")
    assert FakeCodex.instances[0].closed


async def test_aborting_prepared_session_closes_sdk_client(fake_sdk: Any) -> None:
    model = CodexCoordinatorModel()
    session = await model.prepare(
        role(), current_conversation_id=None, current_provider_thread_id=None, cwd=None
    )
    await model.abort(session)
    assert FakeCodex.instances[0].closed


async def test_same_provider_thread_preparations_have_independent_clients(fake_sdk: Any) -> None:
    model = CodexCoordinatorModel()
    first = await model.prepare(
        role(),
        current_conversation_id="conversation-shared",
        current_provider_thread_id="thread-shared",
        cwd=None,
    )
    second = await model.prepare(
        role(),
        current_conversation_id="conversation-shared",
        current_provider_thread_id="thread-shared",
        cwd=None,
    )
    assert first.state["codex_adapter_handle"] != second.state["codex_adapter_handle"]

    # Running in reverse order proves neither prepare overwrote the other's live
    # client, despite both SDK threads having the same provider identity.
    await model.run(second, "second")
    assert FakeCodex.instances[1].closed
    assert not FakeCodex.instances[0].closed
    await model.run(first, "first")
    assert all(instance.closed for instance in FakeCodex.instances)
