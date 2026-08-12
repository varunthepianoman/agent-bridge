from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_bridge_catalog.db import Database
from agent_bridge_catalog.manual_bridge import ManualBridgeService
from agent_bridge_integrations.aiwk import AIWKExecutorAdapter, AIWKReference, AIWKRoleInvocation
from agent_bridge_protocol.models import BridgeEnvelope


@dataclass
class _Ack:
    stream: str = "BRIDGE_WORK_V1"
    sequence: int = 1
    duplicate: bool = False


class _Publisher:
    def __init__(self) -> None:
        self.envelopes: list[BridgeEnvelope] = []

    async def publish(self, envelope: BridgeEnvelope, *, subject: str | None = None) -> _Ack:
        del subject
        self.envelopes.append(envelope)
        return _Ack()


def _reference() -> AIWKReference:
    return AIWKReference(
        project="arci-v2",
        stage="build",
        step="PR17_SS0",
        role="redteam",
        cycle=1,
        attempt=2,
        workflow_fingerprint="sha256:abc123",
    )


async def test_aiwk_adapter_preserves_policy_extension_without_interpreting_it(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite:///{tmp_path / 'catalog.db'}")
    database.initialize()
    publisher = _Publisher()
    service = ManualBridgeService(database, publisher=publisher)
    result = await AIWKExecutorAdapter(service).submit(
        AIWKRoleInvocation(
            reference=_reference(),
            instruction="Run the AIWK-selected red-team role and return structured output",
            target={"kind": "capability", "id": "codex-role"},
            work_id="work-pr17",
            parameters={"output_schema": "aiwk-role-result-v1"},
            extensions={"example.trace": "kept"},
        )
    )

    assert result["execution"]["status"] == "queued"
    request = publisher.envelopes[0].body
    assert request["extensions"]["aiwk"] == _reference().model_dump(mode="json")
    assert request["extensions"]["example.trace"] == "kept"
    assert publisher.envelopes[0].extensions["policy_owner"] == "aiwk"
    # Completion means only that Bridge execution completed; no AIWK gate state is created.
    assert "gate" not in result["execution"]


def test_aiwk_reference_is_strict_and_requires_a_fingerprint_namespace() -> None:
    with pytest.raises(ValidationError):
        AIWKReference(
            project="p",
            stage="build",
            step="S",
            role="dev",
            cycle=0,
            attempt=1,
            workflow_fingerprint="not-a-namespaced-digest",
            unexpected=True,
        )
