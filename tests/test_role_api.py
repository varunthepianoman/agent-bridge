from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_bridge_catalog.role_api import mount_role_api
from agent_bridge_protocol.models import (
    CoordinatorRole,
    EndpointKind,
    EndpointRef,
    Relationship,
    RoleCheckpoint,
    RoleEvent,
    RoleLease,
    RoleReport,
    WorkItem,
)


class FakeRoleStore:
    def __init__(self) -> None:
        self.work: dict[str, WorkItem] = {}
        self.relationships: dict[str, Relationship] = {}
        self.roles: dict[str, CoordinatorRole] = {}
        self.checkpoints: dict[str, list[RoleCheckpoint]] = {}
        self.reports: dict[str, list[RoleReport]] = {}
        self.events: dict[str, list[RoleEvent]] = {}
        self.conversations: dict[str, list[dict[str, Any]]] = {}
        self.leases: dict[str, RoleLease] = {}

    def create_work(self, item: WorkItem) -> WorkItem:
        self.work[item.work_id] = item
        return item

    def list_work(
        self, *, status: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[WorkItem]:
        items = list(self.work.values())
        if status is not None:
            items = [item for item in items if item.status == status]
        return items[offset : offset + limit]

    def count_work(self, *, status: str | None = None) -> int:
        return sum(1 for item in self.work.values() if status is None or item.status == status)

    def get_work(self, work_id: str) -> WorkItem | None:
        return self.work.get(work_id)

    def update_work(self, work_id: str, changes: dict[str, Any]) -> WorkItem | None:
        current = self.work.get(work_id)
        if current is None:
            return None
        updated = current.model_copy(update=changes)
        self.work[work_id] = updated
        return updated

    def create_relationship(self, relationship: Relationship) -> Relationship:
        self.relationships[relationship.relationship_id] = relationship
        return relationship

    def list_relationships(self, **filters: Any) -> list[Relationship]:
        items = list(self.relationships.values())
        if work_id := filters.get("work_item_id"):
            items = [item for item in items if item.source.id == work_id]
        if value := filters.get("source_id"):
            items = [item for item in items if item.source.id == value]
        if value := filters.get("target_id"):
            items = [item for item in items if item.target.id == value]
        if value := filters.get("relationship_type"):
            items = [item for item in items if item.type == value]
        return items

    def delete_relationship(self, relationship_id: str) -> bool:
        return self.relationships.pop(relationship_id, None) is not None

    def attach_work_conversation(
        self, work_id: str, conversation_id: str, *, relationship_id: str | None = None
    ) -> Relationship:
        return self.create_relationship(
            Relationship(
                relationship_id=relationship_id or "rel-generated",
                source=EndpointRef(kind=EndpointKind.ENDPOINT, id=work_id),
                target=EndpointRef(kind=EndpointKind.CONVERSATION, id=conversation_id),
                type="contains",
            )
        )

    def detach_work_conversation(self, work_id: str, conversation_id: str) -> bool:
        found = next(
            (
                item
                for item in self.relationships.values()
                if item.source.id == work_id
                and item.target.id == conversation_id
                and item.type == "contains"
            ),
            None,
        )
        return found is not None and self.delete_relationship(found.relationship_id)

    def create_role(self, role: CoordinatorRole) -> CoordinatorRole:
        self.roles[role.role_id] = role
        return role

    def list_roles(self, **filters: Any) -> list[CoordinatorRole]:
        items = list(self.roles.values())
        if work_id := filters.get("work_item_id"):
            items = [item for item in items if item.scope == f"work:{work_id}"]
        return items

    def get_role(self, role_id: str) -> CoordinatorRole | None:
        return self.roles.get(role_id)

    def update_role(self, role_id: str, changes: dict[str, Any]) -> CoordinatorRole | None:
        current = self.roles.get(role_id)
        if current is None:
            return None
        updated = current.model_copy(update=changes)
        self.roles[role_id] = updated
        return updated

    def append_checkpoint(self, checkpoint: RoleCheckpoint) -> RoleCheckpoint:
        lease = self.leases.get(checkpoint.role_id)
        if lease is not None and lease.fencing_token != checkpoint.fencing_token:
            raise ValueError("stale fencing token")
        self.checkpoints.setdefault(checkpoint.role_id, []).append(checkpoint)
        role = self.roles[checkpoint.role_id]
        self.roles[checkpoint.role_id] = role.model_copy(
            update={"checkpoint_version": checkpoint.version}
        )
        return checkpoint

    def list_checkpoints(self, role_id: str) -> list[RoleCheckpoint]:
        return self.checkpoints.get(role_id, [])

    def append_report(self, report: RoleReport) -> RoleReport:
        self.reports.setdefault(report.reporting_role_id, []).append(report)
        return report

    def list_reports(self, role_id: str, **filters: Any) -> list[RoleReport]:
        items = self.reports.get(role_id, [])
        if recipient := filters.get("recipient_role_id"):
            items = [item for item in items if item.recipient_role_id == recipient]
        return items

    def list_events(self, role_id: str) -> list[RoleEvent]:
        return self.events.get(role_id, [])

    def attach_conversation(
        self, role_id: str, conversation_id: str, handoff_summary: str | None = None
    ) -> dict[str, Any]:
        item = {"conversation_id": conversation_id, "handoff_summary": handoff_summary}
        self.conversations.setdefault(role_id, []).append(item)
        self.roles[role_id] = self.roles[role_id].model_copy(
            update={"current_conversation_id": conversation_id}
        )
        return item

    def rotate_conversation(
        self, role_id: str, new_id: str, handoff_summary: str | None = None
    ) -> dict[str, Any]:
        return self.attach_conversation(role_id, new_id, handoff_summary)

    def list_role_conversations(self, role_id: str) -> list[dict[str, Any]]:
        return self.conversations.get(role_id, [])

    def generate_handoff(self, role_id: str) -> dict[str, Any]:
        role = self.roles[role_id]
        return {"role_id": role_id, "markdown": f"# {role.charter}"}

    def acquire_role_lease(self, role_id: str, holder_id: str, ttl_seconds: float) -> RoleLease:
        previous = self.leases.get(role_id)
        token = 1 if previous is None else previous.fencing_token + 1
        now = datetime.now(UTC)
        lease = RoleLease(
            role_id=role_id,
            holder_id=holder_id,
            fencing_token=token,
            acquired_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
        self.leases[role_id] = lease
        return lease

    def renew_role_lease(
        self, role_id: str, holder_id: str, fencing_token: int, ttl_seconds: float
    ) -> RoleLease:
        lease = self.leases[role_id]
        if lease.holder_id != holder_id or lease.fencing_token != fencing_token:
            raise ValueError("stale lease")
        renewed = lease.model_copy(
            update={"expires_at": datetime.now(UTC) + timedelta(seconds=ttl_seconds)}
        )
        self.leases[role_id] = renewed
        return renewed

    def release_role_lease(
        self, role_id: str, holder_id: str, fencing_token: int
    ) -> dict[str, bool]:
        lease = self.leases[role_id]
        if lease.holder_id != holder_id or lease.fencing_token != fencing_token:
            raise ValueError("stale lease")
        del self.leases[role_id]
        return {"released": True}


def make_client() -> tuple[TestClient, FakeRoleStore]:
    app = FastAPI()
    store = FakeRoleStore()
    app.state.role_store = store
    mount_role_api(app)
    return TestClient(app), store


def test_work_items_and_conversation_membership() -> None:
    client, _store = make_client()
    created = client.post(
        "/api/v1/work-items",
        json={"work_id": "work-pr17", "title": "ARCI PR 17", "tags": ["arci"]},
    )
    assert created.status_code == 201

    updated = client.patch(
        "/api/v1/work-items/work-pr17", json={"objective": "Finish reconnect validation"}
    )
    assert updated.status_code == 200
    assert updated.json()["objective"] == "Finish reconnect validation"

    attached = client.post(
        "/api/v1/work-items/work-pr17/conversations",
        json={"conversation_id": "conv-root"},
    )
    assert attached.status_code == 201
    relationship_id = attached.json()["relationship_id"]
    assert client.get("/api/v1/relationships?work_item_id=work-pr17").json()["total"] == 1

    removed = client.delete("/api/v1/work-items/work-pr17/conversations/conv-root")
    assert removed.status_code == 204
    assert client.delete(f"/api/v1/relationships/{relationship_id}").status_code == 404


def test_role_checkpoint_report_rotation_and_handoff() -> None:
    client, _store = make_client()
    role = client.post(
        "/api/v1/roles",
        json={
            "role_id": "role-pr17",
            "role_type": "work_coordinator",
            "scope": "work:work-pr17",
            "charter": "Coordinate PR 17",
            "authority_profile": "delegate-bounded",
            "status": "active",
        },
    )
    assert role.status_code == 201

    lease = client.post(
        "/api/v1/roles/role-pr17/lease", json={"holder_id": "runner-a", "ttl_seconds": 60}
    )
    assert lease.status_code == 201
    token = lease.json()["fencing_token"]

    checkpoint = client.post(
        "/api/v1/roles/role-pr17/checkpoints",
        json={
            "fencing_token": token,
            "objective": "Finish PR 17",
            "charter": "Coordinate PR 17",
            "authority_profile": "delegate-bounded",
            "status": "active",
            "parent_summary": "Implementation is progressing.",
            "current_plan": ["Run reconnect tests"],
        },
    )
    assert checkpoint.status_code == 201
    assert checkpoint.json()["version"] == 1

    stale = client.post(
        "/api/v1/roles/role-pr17/checkpoints",
        json={
            "fencing_token": token + 1,
            "objective": "Overwrite",
            "charter": "Coordinate PR 17",
            "authority_profile": "delegate-bounded",
            "status": "active",
            "parent_summary": "stale",
        },
    )
    assert stale.status_code == 409

    report = client.post(
        "/api/v1/roles/role-pr17/reports",
        json={
            "recipient_role_id": "role-portfolio",
            "checkpoint_version": 1,
            "status": "active",
            "summary": "Ready for reconnect tests",
        },
    )
    assert report.status_code == 201
    assert client.get("/api/v1/roles/role-pr17/reports").json()["total"] == 1

    attached = client.post(
        "/api/v1/roles/role-pr17/conversations",
        json={"conversation_id": "conv-old"},
    )
    assert attached.status_code == 201
    rotated = client.post(
        "/api/v1/roles/role-pr17/conversations/rotate",
        json={"conversation_id": "conv-new", "handoff_summary": "Continue testing."},
    )
    assert rotated.status_code == 200
    assert client.get("/api/v1/roles/role-pr17").json()["current_conversation_id"] == "conv-new"
    assert client.get("/api/v1/roles/role-pr17/handoff").json()["role_id"] == "role-pr17"


def test_role_store_unavailable_and_validation() -> None:
    app = FastAPI()
    mount_role_api(app)
    client = TestClient(app)
    assert client.get("/api/v1/work-items").status_code == 503

    configured, _store = make_client()
    invalid = configured.post(
        "/api/v1/relationships",
        json={
            "source": {"kind": "role", "id": "role-a"},
            "target": {"kind": "role", "id": "role-b"},
            "type": "has whitespace",
        },
    )
    assert invalid.status_code == 422
