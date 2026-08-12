from __future__ import annotations

from datetime import timedelta

import pytest

from agent_bridge_catalog.db import Database
from agent_bridge_catalog.repository import CatalogRepository
from agent_bridge_catalog.roles import ConflictError, RoleStore, StaleFencingTokenError
from agent_bridge_protocol import (
    CoordinatorRole,
    EndpointKind,
    EndpointRef,
    Relationship,
    RoleCheckpoint,
    RoleReport,
    RoleStatus,
    WorkItem,
)
from agent_bridge_protocol.models import utc_now


@pytest.fixture
def store(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'catalog.db'}")
    database.initialize()
    catalog = CatalogRepository(database)
    first = catalog.upsert_discovered(
        {"provider_thread_id": "thread-1", "title": "First", "cwd": "/repo"},
        node_id="node-1",
        environment_id="env-1",
    )
    second = catalog.upsert_discovered(
        {"provider_thread_id": "thread-2", "title": "Second", "cwd": "/repo"},
        node_id="node-1",
        environment_id="env-1",
    )
    return RoleStore(database), first.conversation_id, second.conversation_id


def test_work_items_and_relationships_are_persistent(store) -> None:
    roles, conversation_id, _ = store
    item = roles.create_work(
        WorkItem(
            work_id="pr-17",
            title="ARCI PR 17",
            objective="Finish reconnect support",
            tags=["arci"],
        )
    )
    assert roles.get_work(item.work_id) == item
    updated = roles.update_work(item.work_id, {"status": "blocked", "tags": ["arci", "robot"]})
    assert updated.status == "blocked"
    assert roles.list_work(status="blocked", limit=10, offset=0) == [updated]

    attached = roles.attach_work_conversation(item.work_id, conversation_id)
    assert attached.type == "contains"
    assert roles.list_relationships(work_item_id=item.work_id) == [attached]
    assert roles.detach_work_conversation(item.work_id, conversation_id)
    assert roles.list_relationships(work_item_id=item.work_id) == []

    explicit = Relationship(
        relationship_id="rel-audit",
        source=EndpointRef(kind=EndpointKind.CONVERSATION, id=conversation_id),
        target=EndpointRef(kind=EndpointKind.ROLE, id="role-auditor"),
        type="audited_by",
        metadata={"reason": "release gate"},
    )
    roles.create_relationship(explicit)
    assert roles.list_relationships(source_kind="conversation") == [explicit]
    assert roles.delete_relationship(explicit.relationship_id)
    assert not roles.delete_relationship(explicit.relationship_id)


def test_role_hierarchy_conversation_rotation_and_handoff(store) -> None:
    roles, first, second = store
    parent = roles.create_role(
        CoordinatorRole(
            role_id="portfolio",
            role_type="portfolio_coordinator",
            scope="portfolio:default",
            charter="Coordinate the portfolio",
            authority_profile="delegate-bounded",
        )
    )
    child = roles.create_role(
        CoordinatorRole(
            role_id="pr17-coordinator",
            role_type="work_coordinator",
            scope="work:pr-17",
            charter="Coordinate PR 17",
            authority_profile="delegate-bounded",
            parent_role_id=parent.role_id,
        )
    )
    assert roles.list_roles(work_item_id="pr-17") == [child]
    assert roles.list_events(child.role_id)[0].type == "role.created"

    roles.attach_conversation(child.role_id, first)
    roles.rotate_conversation(child.role_id, second, "Continue after compaction")
    history = roles.list_role_conversations(child.role_id)
    assert [entry["conversation_id"] for entry in history] == [first, second]
    assert history[0]["detached_at"] is not None
    assert history[0]["handoff_summary"] == "Continue after compaction"
    assert roles.get_role(child.role_id).current_conversation_id == second

    with pytest.raises(ConflictError, match="cycle"):
        roles.update_role(parent.role_id, {"parent_role_id": child.role_id})
    handoff = roles.generate_handoff(child.role_id)
    assert handoff["role"]["role_id"] == child.role_id
    assert len(handoff["conversation_history"]) == 2


def test_checkpoint_requires_current_lease_and_monotonic_version(store) -> None:
    roles, _, _ = store
    role = roles.create_role(
        CoordinatorRole(
            role_id="coordinator",
            role_type="work_coordinator",
            scope="work:one",
            charter="Coordinate work",
            authority_profile="delegate-bounded",
        )
    )
    lease = roles.acquire_role_lease(role.role_id, "runner-a", ttl_seconds=30)
    checkpoint = RoleCheckpoint(
        role_id=role.role_id,
        version=1,
        fencing_token=lease.fencing_token,
        objective="Complete the work",
        charter=role.charter,
        authority_profile=role.authority_profile,
        status=RoleStatus.ACTIVE,
        decisions=["Use the durable store"],
        parent_summary="Work began",
    )
    roles.append_checkpoint(checkpoint)
    assert roles.get_latest_checkpoint(role.role_id) == checkpoint
    assert roles.get_role(role.role_id).checkpoint_version == 1

    with pytest.raises(ConflictError, match="must be 2"):
        roles.append_checkpoint(checkpoint)

    newer_lease = roles.acquire_role_lease(role.role_id, "runner-a", ttl_seconds=30)
    stale = checkpoint.model_copy(update={"version": 2})
    with pytest.raises(StaleFencingTokenError, match="stale"):
        roles.append_checkpoint(stale)
    renewed = roles.renew_role_lease(
        role.role_id, "runner-a", newer_lease.fencing_token, ttl_seconds=60
    )
    assert renewed.expires_at > newer_lease.expires_at
    with pytest.raises(StaleFencingTokenError):
        roles.release_role_lease(role.role_id, "runner-a", lease.fencing_token)
    roles.release_role_lease(role.role_id, "runner-a", newer_lease.fencing_token)
    after_release = roles.acquire_role_lease(role.role_id, "runner-b")
    assert after_release.fencing_token == newer_lease.fencing_token + 1


def test_reports_reference_published_checkpoints(store) -> None:
    roles, _, _ = store
    parent = roles.create_role(
        CoordinatorRole(
            role_id="portfolio",
            role_type="portfolio_coordinator",
            scope="portfolio:default",
            charter="Coordinate all work",
            authority_profile="delegate-bounded",
        )
    )
    child = roles.create_role(
        CoordinatorRole(
            role_id="child",
            role_type="work_coordinator",
            scope="work:child",
            charter="Coordinate child work",
            authority_profile="delegate-bounded",
            parent_role_id=parent.role_id,
        )
    )
    unpublished = RoleReport(
        report_id="report-1",
        reporting_role_id=child.role_id,
        recipient_role_id=parent.role_id,
        checkpoint_version=1,
        status=RoleStatus.ACTIVE,
        summary="Started",
    )
    with pytest.raises(ConflictError, match="unpublished"):
        roles.append_report(unpublished)

    lease = roles.acquire_role_lease(child.role_id, "runner")
    roles.append_checkpoint(
        RoleCheckpoint(
            role_id=child.role_id,
            version=1,
            fencing_token=lease.fencing_token,
            objective="Do child work",
            charter=child.charter,
            authority_profile=child.authority_profile,
            status=RoleStatus.ACTIVE,
            parent_summary="Started",
            created_at=utc_now() - timedelta(seconds=1),
        )
    )
    roles.append_report(unpublished)
    assert roles.list_reports(child.role_id) == [unpublished]
    assert roles.list_reports(recipient_role_id=parent.role_id) == [unpublished]
    assert [event.type for event in roles.list_events(child.role_id)][-2:] == [
        "checkpoint.published",
        "report.sent",
    ]
