import json
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_bridge_protocol import (
    SCHEMA_VERSION,
    ArtifactRef,
    AutonomyMode,
    BridgeEnvelope,
    ConversationHandoff,
    CoordinatorRole,
    EndpointKind,
    EndpointRef,
    ExecutionLease,
    ExecutionOperation,
    ExecutionRequest,
    MessageKind,
    Relationship,
    RoleCheckpoint,
    RoleLease,
    RoleStatus,
    WorkRequest,
    conversation_id,
    provider_session_key,
    stable_id,
)
from agent_bridge_protocol.models import utc_now


def endpoint(kind: EndpointKind, identity: str) -> EndpointRef:
    return EndpointRef(kind=kind, id=identity)


def test_stable_ids_are_deterministic_order_safe_and_scoped() -> None:
    first = stable_id("work", {"branch": "main", "repo": "org/repo"})
    reordered = stable_id("work", {"repo": "org/repo", "branch": "main"})
    assert first == reordered
    assert first.startswith("work-")
    assert first != stable_id("role", {"branch": "main", "repo": "org/repo"})


def test_conversation_dedup_key_normalizes_provider_only() -> None:
    args = dict(
        provider_thread_id="thr-123",
        node_id="laptop",
        environment_id="devcontainer",
    )
    assert conversation_id(provider=" Codex ", **args) == conversation_id(provider="codex", **args)
    assert provider_session_key(provider="CODEX", **args) == provider_session_key(
        provider="codex", **args
    )
    assert conversation_id(provider="codex", **args) != conversation_id(
        provider="codex", **{**args, "node_id": "desktop"}
    )


def test_bridge_envelope_round_trips_and_preserves_extensions() -> None:
    envelope = BridgeEnvelope(
        message_id="msg-1",
        kind=MessageKind.REQUEST,
        sender=endpoint(EndpointKind.CONVERSATION, "conv-1"),
        destination=endpoint(EndpointKind.CAPABILITY, "robot-test"),
        correlation_id="corr-1",
        body={"instruction": "Run reconnect E2E tests"},
        extensions={"aiwk": {"stage": "build"}},
        artifacts=[
            ArtifactRef(
                name="results.json",
                uri="nats-object://artifacts/sha256-example",
                media_type="application/json",
            )
        ],
    )
    encoded = envelope.model_dump_json()
    decoded = BridgeEnvelope.model_validate_json(encoded)
    assert decoded.schema_version == SCHEMA_VERSION
    assert decoded.extensions == {"aiwk": {"stage": "build"}}
    assert decoded.delivery.max_attempts == 3


def test_shared_contract_fixture_validates_in_python() -> None:
    fixture = Path(__file__).parents[1] / "schemas" / "examples" / "bridge-envelope.json"
    envelope = BridgeEnvelope.model_validate(json.loads(fixture.read_text(encoding="utf-8")))
    assert envelope.message_id == "msg-contract-example"


def test_unknown_core_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        EndpointRef(kind="node", id="node-1", typo="not-an-extension")


def test_unknown_schema_version_is_rejected() -> None:
    with pytest.raises(ValidationError):
        BridgeEnvelope(
            schema_version="agent-bridge/v2",
            message_id="msg-1",
            kind="message",
            sender=endpoint(EndpointKind.CONVERSATION, "conv-1"),
            destination=endpoint(EndpointKind.CONVERSATION, "conv-2"),
        )


def test_relationship_types_are_extensible_but_tokenized() -> None:
    relation = Relationship(
        relationship_id="rel-1",
        source=endpoint(EndpointKind.CONVERSATION, "conv-1"),
        target=endpoint(EndpointKind.CONVERSATION, "conv-2"),
        type="example.org/reviews",
    )
    assert relation.type == "example.org/reviews"
    with pytest.raises(ValidationError):
        Relationship(
            relationship_id="rel-2",
            source=endpoint(EndpointKind.CONVERSATION, "conv-1"),
            target=endpoint(EndpointKind.CONVERSATION, "conv-2"),
            type="has spaces",
        )


def test_execution_request_requires_operation_specific_locator() -> None:
    base = dict(
        execution_id="exec-1",
        instruction="Continue the task",
        target=endpoint(EndpointKind.NODE, "node-1"),
    )
    with pytest.raises(ValidationError, match="conversation_id"):
        ExecutionRequest(operation=ExecutionOperation.RESUME_CONVERSATION, **base)
    with pytest.raises(ValidationError, match="adapter"):
        ExecutionRequest(operation=ExecutionOperation.INVOKE_ADAPTER, **base)
    request = ExecutionRequest(
        operation=ExecutionOperation.RESUME_CONVERSATION,
        conversation_id="conv-1",
        cwd="/workspace/agent-bridge",
        **base,
    )
    assert request.conversation_id == "conv-1"
    assert request.cwd == "/workspace/agent-bridge"


@pytest.mark.parametrize(
    "lease_type,id_field", [(ExecutionLease, "execution_id"), (RoleLease, "role_id")]
)
def test_leases_require_positive_fencing_and_future_expiry(lease_type, id_field) -> None:
    now = utc_now()
    values = {
        id_field: "subject-1",
        "holder_id": "worker-1",
        "fencing_token": 2,
        "acquired_at": now,
        "expires_at": now + timedelta(seconds=30),
    }
    assert lease_type(**values).fencing_token == 2
    with pytest.raises(ValidationError, match="after acquired_at"):
        lease_type(**{**values, "expires_at": now})


def test_coordinator_defaults_to_delegate_and_checkpoint_is_versioned() -> None:
    role = CoordinatorRole(
        role_id="role-pr17",
        role_type="work_coordinator",
        scope="work:pr17",
        charter="Coordinate PR 17",
        authority_profile="delegate-bounded",
    )
    assert role.autonomy_mode == AutonomyMode.DELEGATE.value
    checkpoint = RoleCheckpoint(
        role_id=role.role_id,
        version=1,
        fencing_token=1,
        objective="Finish PR 17",
        charter=role.charter,
        authority_profile=role.authority_profile,
        status=RoleStatus.ACTIVE,
        decisions=["Use generation counters"],
        parent_summary="Implementation has started.",
    )
    assert checkpoint.version == 1
    assert checkpoint.schema_version == SCHEMA_VERSION


def test_autonomous_work_request_has_explicit_bounded_authority_defaults() -> None:
    request = WorkRequest(
        request_id="request-1",
        objective="Implement reconnect handling",
        mode=AutonomyMode.AUTONOMOUS,
        requested_by=endpoint(EndpointKind.CONVERSATION, "conv-user"),
    )
    assert request.authority.max_parallel_executions == 1
    assert request.authority.max_attempts == 3
    assert request.authority.may_expand_scope is False


def test_handoff_is_a_durable_reference_not_a_replacement_thread() -> None:
    handoff = ConversationHandoff(
        handoff_id="handoff-1",
        source_conversation_id="conv-source",
        target_environment_id="robot-devcontainer",
        work_id="work-pr17",
        summary="Reconnect generation handling is implemented.",
        decisions=["Invalidate stale RWS sessions after reconnect"],
        open_questions=["Run the robot validation suite"],
    )
    assert handoff.source_conversation_id == "conv-source"
    assert handoff.target_environment_id == "robot-devcontainer"
