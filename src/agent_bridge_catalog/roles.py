"""Durable work and coordinator-role persistence.

This module deliberately contains no model execution logic.  It is the durable
source of truth that a later coordinator runtime can lease and recover from.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from agent_bridge_protocol.models import (
    AutonomyMode,
    CoordinatorRole,
    EndpointKind,
    EndpointRef,
    Relationship,
    RoleCheckpoint,
    RoleEvent,
    RoleLease,
    RoleReport,
    RoleStatus,
    WorkItem,
)

from .db import (
    ConversationRow,
    CoordinatorRoleRow,
    Database,
    RelationshipRow,
    RoleCheckpointRow,
    RoleConversationRow,
    RoleEventRow,
    RoleLeaseRow,
    RoleReportRow,
    WorkItemRow,
)


class RoleStoreError(RuntimeError):
    """Base error for rejected durable-role operations."""


class NotFoundError(RoleStoreError, KeyError):
    pass


class ConflictError(RoleStoreError):
    def __init__(self, message: str) -> None:
        super().__init__(f"conflict: {message}")


class StaleFencingTokenError(ConflictError):
    pass


class RoleStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    # Work organization -------------------------------------------------
    def create_work(self, item: WorkItem) -> WorkItem:
        with self.database.session() as session:
            if session.get(WorkItemRow, item.work_id):
                raise ConflictError(f"work item already exists: {item.work_id}")
            session.add(_work_row(item))
            session.commit()
        return item

    def upsert_work(self, item: WorkItem) -> WorkItem:
        with self.database.session() as session:
            row = session.get(WorkItemRow, item.work_id)
            if row is None:
                row = _work_row(item)
                session.add(row)
            else:
                _assign_work(row, item)
            session.commit()
        return item

    def get_work(self, work_id: str) -> WorkItem | None:
        with self.database.session() as session:
            row = session.get(WorkItemRow, work_id)
            return _work_model(row) if row else None

    def list_work(
        self, *, status: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[WorkItem]:
        with self.database.session() as session:
            statement = select(WorkItemRow)
            if status:
                statement = statement.where(WorkItemRow.status == status)
            statement = (
                statement.order_by(WorkItemRow.updated_at.desc(), WorkItemRow.work_id)
                .limit(limit)
                .offset(offset)
            )
            return [_work_model(row) for row in session.scalars(statement)]

    def count_work(self, *, status: str | None = None) -> int:
        with self.database.session() as session:
            statement = select(func.count()).select_from(WorkItemRow)
            if status:
                statement = statement.where(WorkItemRow.status == status)
            return int(session.scalar(statement) or 0)

    def update_work(self, work_id: str, changes: dict[str, Any]) -> WorkItem:
        immutable = {"work_id", "schema_version", "created_at"}
        if immutable.intersection(changes):
            raise ValueError(
                f"immutable work fields: {', '.join(sorted(immutable & changes.keys()))}"
            )
        current = self.get_work(work_id)
        if current is None:
            raise NotFoundError(f"unknown work item: {work_id}")
        updated = WorkItem.model_validate(
            {**current.model_dump(mode="json"), **changes, "updated_at": _now().isoformat()}
        )
        return self.upsert_work(updated)

    def attach_work_conversation(
        self, work_id: str, conversation_id: str, *, relationship_id: str | None = None
    ) -> Relationship:
        if self.get_work(work_id) is None:
            raise NotFoundError(f"unknown work item: {work_id}")
        self._require_conversation(conversation_id)
        relationship = Relationship(
            relationship_id=relationship_id or f"rel-{uuid4()}",
            source=EndpointRef(kind=EndpointKind.ENDPOINT, id=work_id),
            target=EndpointRef(kind=EndpointKind.CONVERSATION, id=conversation_id),
            type="contains",
        )
        return self.create_relationship(relationship)

    def detach_work_conversation(self, work_id: str, conversation_id: str) -> bool:
        with self.database.session() as session:
            rows = list(
                session.scalars(
                    select(RelationshipRow).where(
                        RelationshipRow.source_kind == EndpointKind.ENDPOINT.value,
                        RelationshipRow.source_id == work_id,
                        RelationshipRow.target_kind == EndpointKind.CONVERSATION.value,
                        RelationshipRow.target_id == conversation_id,
                        RelationshipRow.type == "contains",
                    )
                )
            )
            for row in rows:
                session.delete(row)
            session.commit()
            return bool(rows)

    # Generic topology --------------------------------------------------
    def create_relationship(self, relationship: Relationship) -> Relationship:
        row = RelationshipRow(
            relationship_id=relationship.relationship_id,
            source_kind=str(relationship.source.kind),
            source_id=relationship.source.id,
            target_kind=str(relationship.target.kind),
            target_id=relationship.target.id,
            type=relationship.type,
            metadata_json=_json(relationship.metadata),
            extensions_json=_json(relationship.extensions),
            created_at=relationship.created_at,
        )
        with self.database.session() as session:
            if session.get(RelationshipRow, relationship.relationship_id):
                raise ConflictError(f"relationship already exists: {relationship.relationship_id}")
            session.add(row)
            session.commit()
        return relationship

    def list_relationships(
        self,
        *,
        work_item_id: str | None = None,
        endpoint_id: str | None = None,
        relationship_type: str | None = None,
        source_kind: str | None = None,
        source_id: str | None = None,
        target_kind: str | None = None,
        target_id: str | None = None,
    ) -> list[Relationship]:
        with self.database.session() as session:
            statement = select(RelationshipRow)
            if work_item_id:
                statement = statement.where(
                    or_(
                        RelationshipRow.source_id == work_item_id,
                        RelationshipRow.target_id == work_item_id,
                    )
                )
            if endpoint_id:
                statement = statement.where(
                    or_(
                        RelationshipRow.source_id == endpoint_id,
                        RelationshipRow.target_id == endpoint_id,
                    )
                )
            if relationship_type:
                statement = statement.where(RelationshipRow.type == relationship_type)
            if source_id:
                statement = statement.where(RelationshipRow.source_id == source_id)
            if source_kind:
                statement = statement.where(RelationshipRow.source_kind == source_kind)
            if target_id:
                statement = statement.where(RelationshipRow.target_id == target_id)
            if target_kind:
                statement = statement.where(RelationshipRow.target_kind == target_kind)
            statement = statement.order_by(
                RelationshipRow.created_at, RelationshipRow.relationship_id
            )
            return [_relationship_model(row) for row in session.scalars(statement)]

    def delete_relationship(self, relationship_id: str) -> bool:
        with self.database.session() as session:
            row = session.get(RelationshipRow, relationship_id)
            if row is not None:
                session.delete(row)
            session.commit()
            return row is not None

    # Roles -------------------------------------------------------------
    def create_role(self, role: CoordinatorRole) -> CoordinatorRole:
        with self.database.session() as session:
            if session.get(CoordinatorRoleRow, role.role_id):
                raise ConflictError(f"role already exists: {role.role_id}")
            self._validate_parent(session, role.role_id, role.parent_role_id)
            if role.current_conversation_id:
                self._require_conversation_in(session, role.current_conversation_id)
            row = _role_row(role)
            session.add(row)
            session.flush()
            if role.current_conversation_id:
                session.add(
                    RoleConversationRow(
                        role_id=role.role_id,
                        conversation_id=role.current_conversation_id,
                        attached_at=role.created_at,
                    )
                )
            self._append_event(session, role.role_id, "role.created", {"role_type": role.role_type})
            session.commit()
        return role

    def get_role(self, role_id: str) -> CoordinatorRole | None:
        with self.database.session() as session:
            row = session.get(CoordinatorRoleRow, role_id)
            return _role_model(row) if row else None

    def list_roles(
        self,
        *,
        parent_role_id: str | None = None,
        scope: str | None = None,
        work_item_id: str | None = None,
    ) -> list[CoordinatorRole]:
        with self.database.session() as session:
            statement = select(CoordinatorRoleRow)
            if parent_role_id is not None:
                statement = statement.where(CoordinatorRoleRow.parent_role_id == parent_role_id)
            if scope is not None:
                statement = statement.where(CoordinatorRoleRow.scope == scope)
            if work_item_id is not None:
                statement = statement.where(CoordinatorRoleRow.scope == f"work:{work_item_id}")
            statement = statement.order_by(
                CoordinatorRoleRow.created_at, CoordinatorRoleRow.role_id
            )
            return [_role_model(row) for row in session.scalars(statement)]

    def update_role(self, role_id: str, changes: dict[str, Any]) -> CoordinatorRole:
        immutable = {"role_id", "schema_version", "created_at", "checkpoint_version"}
        if immutable.intersection(changes):
            raise ValueError(
                f"immutable role fields: {', '.join(sorted(immutable & changes.keys()))}"
            )
        with self.database.session() as session:
            row = session.get(CoordinatorRoleRow, role_id)
            if row is None:
                raise NotFoundError(f"unknown role: {role_id}")
            current = _role_model(row)
            updated = CoordinatorRole.model_validate(
                {**current.model_dump(mode="json"), **changes, "updated_at": _now().isoformat()}
            )
            self._validate_parent(session, role_id, updated.parent_role_id)
            # Conversation changes must go through attach/rotate to retain history.
            if updated.current_conversation_id != current.current_conversation_id:
                raise ValueError("use attach_conversation or rotate_conversation")
            _assign_role(row, updated)
            event_type = (
                "authority.changed"
                if ({"authority_profile", "autonomy_mode"} & changes.keys())
                else "role.updated"
            )
            self._append_event(session, role_id, event_type, changes)
            session.commit()
            return _role_model(row)

    # Conversation incumbency ------------------------------------------
    def attach_conversation(
        self, role_id: str, conversation_id: str, handoff_summary: str | None = None
    ) -> CoordinatorRole:
        with self.database.session() as session:
            role = self._require_role(session, role_id)
            self._require_conversation_in(session, conversation_id)
            if role.current_conversation_id and role.current_conversation_id != conversation_id:
                raise ConflictError(
                    "role already has a current conversation; use rotate_conversation"
                )
            history = session.scalar(
                select(RoleConversationRow).where(
                    RoleConversationRow.role_id == role_id,
                    RoleConversationRow.conversation_id == conversation_id,
                )
            )
            if history is None:
                session.add(
                    RoleConversationRow(
                        role_id=role_id,
                        conversation_id=conversation_id,
                        attached_at=_now(),
                        handoff_summary=handoff_summary,
                    )
                )
            else:
                history.detached_at = None
                history.handoff_summary = handoff_summary
            role.current_conversation_id = conversation_id
            role.updated_at = _now()
            self._append_event(
                session, role_id, "conversation.attached", {"conversation_id": conversation_id}
            )
            session.commit()
            return _role_model(role)

    def rotate_conversation(
        self, role_id: str, conversation_id: str, handoff_summary: str | None = None
    ) -> CoordinatorRole:
        with self.database.session() as session:
            role = self._require_role(session, role_id)
            self._require_conversation_in(session, conversation_id)
            previous = role.current_conversation_id
            if previous == conversation_id:
                return _role_model(role)
            if previous:
                incumbent = session.scalar(
                    select(RoleConversationRow).where(
                        RoleConversationRow.role_id == role_id,
                        RoleConversationRow.conversation_id == previous,
                    )
                )
                if incumbent:
                    incumbent.detached_at = _now()
                    incumbent.handoff_summary = handoff_summary
            existing = session.scalar(
                select(RoleConversationRow).where(
                    RoleConversationRow.role_id == role_id,
                    RoleConversationRow.conversation_id == conversation_id,
                )
            )
            if existing:
                existing.attached_at = _now()
                existing.detached_at = None
            else:
                session.add(
                    RoleConversationRow(
                        role_id=role_id, conversation_id=conversation_id, attached_at=_now()
                    )
                )
            role.current_conversation_id = conversation_id
            role.updated_at = _now()
            self._append_event(
                session,
                role_id,
                "conversation.rotated",
                {"previous_conversation_id": previous, "conversation_id": conversation_id},
            )
            session.commit()
            return _role_model(role)

    def list_role_conversations(self, role_id: str) -> list[dict[str, Any]]:
        with self.database.session() as session:
            self._require_role(session, role_id)
            rows = session.scalars(
                select(RoleConversationRow)
                .where(RoleConversationRow.role_id == role_id)
                .order_by(RoleConversationRow.attached_at)
            )
            return [
                {
                    "conversation_id": row.conversation_id,
                    "attached_at": _aware(row.attached_at),
                    "detached_at": _aware(row.detached_at),
                    "handoff_summary": row.handoff_summary,
                }
                for row in rows
            ]

    # Leasing and checkpoints ------------------------------------------
    def acquire_role_lease(
        self, role_id: str, holder_id: str, ttl_seconds: float = 60.0
    ) -> RoleLease:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        now = _now()
        with self.database.session() as session:
            self._require_role(session, role_id)
            row = session.get(RoleLeaseRow, role_id)
            if (
                row is not None
                and _required_aware(row.expires_at) > now
                and row.holder_id != holder_id
            ):
                raise ConflictError(f"role is leased by {row.holder_id}")
            token = (row.fencing_token + 1) if row else 1
            if row is None:
                row = RoleLeaseRow(
                    role_id=role_id,
                    holder_id=holder_id,
                    fencing_token=token,
                    acquired_at=now,
                    expires_at=now + timedelta(seconds=ttl_seconds),
                )
                session.add(row)
            else:
                row.holder_id = holder_id
                row.fencing_token = token
                row.acquired_at = now
                row.expires_at = now + timedelta(seconds=ttl_seconds)
            session.commit()
            return _lease_model(row)

    def renew_role_lease(
        self, role_id: str, holder_id: str, fencing_token: int, ttl_seconds: float = 60.0
    ) -> RoleLease:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        now = _now()
        with self.database.session() as session:
            row = session.get(RoleLeaseRow, role_id)
            self._validate_lease(row, holder_id, fencing_token, now)
            assert row is not None
            row.expires_at = now + timedelta(seconds=ttl_seconds)
            session.commit()
            return _lease_model(row)

    def release_role_lease(
        self, role_id: str, holder_id: str, fencing_token: int
    ) -> dict[str, bool]:
        with self.database.session() as session:
            row = session.get(RoleLeaseRow, role_id)
            self._validate_lease(row, holder_id, fencing_token, _now(), allow_expired=True)
            # Retain the row so fencing tokens never reset after release.  An
            # ancient process must not become current merely because token 1
            # was reused by a later activation.
            assert row is not None
            row.expires_at = _now()
            session.commit()
            return {"released": True}

    def append_checkpoint(self, checkpoint: RoleCheckpoint) -> RoleCheckpoint:
        with self.database.session() as session:
            role = self._require_role(session, checkpoint.role_id)
            lease = session.get(RoleLeaseRow, checkpoint.role_id)
            self._validate_lease_token(lease, checkpoint.fencing_token, _now())
            expected = role.checkpoint_version + 1
            if checkpoint.version != expected:
                raise ConflictError(
                    f"checkpoint version must be {expected}, got {checkpoint.version}"
                )
            session.add(
                RoleCheckpointRow(
                    role_id=checkpoint.role_id,
                    version=checkpoint.version,
                    fencing_token=checkpoint.fencing_token,
                    payload_json=checkpoint.model_dump_json(),
                    created_at=checkpoint.created_at,
                )
            )
            role.checkpoint_version = checkpoint.version
            role.status = str(checkpoint.status)
            role.updated_at = _now()
            self._append_event(
                session,
                checkpoint.role_id,
                "checkpoint.published",
                {"version": checkpoint.version, "fencing_token": checkpoint.fencing_token},
            )
            session.commit()
        return checkpoint

    def get_latest_checkpoint(self, role_id: str) -> RoleCheckpoint | None:
        with self.database.session() as session:
            row = session.scalar(
                select(RoleCheckpointRow)
                .where(RoleCheckpointRow.role_id == role_id)
                .order_by(RoleCheckpointRow.version.desc())
                .limit(1)
            )
            return RoleCheckpoint.model_validate_json(row.payload_json) if row else None

    def list_checkpoints(self, role_id: str) -> list[RoleCheckpoint]:
        with self.database.session() as session:
            rows = session.scalars(
                select(RoleCheckpointRow)
                .where(RoleCheckpointRow.role_id == role_id)
                .order_by(RoleCheckpointRow.version)
            )
            return [RoleCheckpoint.model_validate_json(row.payload_json) for row in rows]

    # Reports and event history ----------------------------------------
    def append_report(self, report: RoleReport) -> RoleReport:
        with self.database.session() as session:
            reporting = self._require_role(session, report.reporting_role_id)
            self._require_role(session, report.recipient_role_id)
            if report.checkpoint_version > reporting.checkpoint_version:
                raise ConflictError("report references an unpublished checkpoint")
            if session.get(RoleReportRow, report.report_id):
                raise ConflictError(f"report already exists: {report.report_id}")
            session.add(
                RoleReportRow(
                    report_id=report.report_id,
                    reporting_role_id=report.reporting_role_id,
                    recipient_role_id=report.recipient_role_id,
                    checkpoint_version=report.checkpoint_version,
                    payload_json=report.model_dump_json(),
                    created_at=report.created_at,
                )
            )
            self._append_event(
                session,
                report.reporting_role_id,
                "report.sent",
                {"report_id": report.report_id, "recipient_role_id": report.recipient_role_id},
            )
            session.commit()
        return report

    def list_reports(
        self,
        role_id: str | None = None,
        *,
        reporting_role_id: str | None = None,
        recipient_role_id: str | None = None,
    ) -> list[RoleReport]:
        with self.database.session() as session:
            statement = select(RoleReportRow)
            if role_id:
                statement = statement.where(RoleReportRow.reporting_role_id == role_id)
            if reporting_role_id:
                statement = statement.where(RoleReportRow.reporting_role_id == reporting_role_id)
            if recipient_role_id:
                statement = statement.where(RoleReportRow.recipient_role_id == recipient_role_id)
            statement = statement.order_by(RoleReportRow.created_at, RoleReportRow.report_id)
            return [
                RoleReport.model_validate_json(row.payload_json)
                for row in session.scalars(statement)
            ]

    def list_events(self, role_id: str) -> list[RoleEvent]:
        with self.database.session() as session:
            rows = session.scalars(
                select(RoleEventRow)
                .where(RoleEventRow.role_id == role_id)
                .order_by(RoleEventRow.sequence)
            )
            return [RoleEvent.model_validate_json(row.payload_json) for row in rows]

    def generate_handoff(self, role_id: str) -> dict[str, Any]:
        role = self.get_role(role_id)
        if role is None:
            raise NotFoundError(f"unknown role: {role_id}")
        checkpoint = self.get_latest_checkpoint(role_id)
        reports = self.list_reports(recipient_role_id=role_id)
        markdown = _handoff_markdown(role, checkpoint, reports[-20:])
        return {
            "role_id": role_id,
            "markdown": markdown,
            "role": role.model_dump(mode="json"),
            "latest_checkpoint": checkpoint.model_dump(mode="json") if checkpoint else None,
            "conversation_history": self.list_role_conversations(role_id),
            "recent_reports": [report.model_dump(mode="json") for report in reports[-20:]],
        }

    # Internals ---------------------------------------------------------
    def _require_conversation(self, conversation_id: str) -> None:
        with self.database.session() as session:
            self._require_conversation_in(session, conversation_id)

    @staticmethod
    def _require_conversation_in(session: Session, conversation_id: str) -> ConversationRow:
        row = session.get(ConversationRow, conversation_id)
        if row is None:
            raise NotFoundError(f"unknown conversation: {conversation_id}")
        return row

    @staticmethod
    def _require_role(session: Session, role_id: str) -> CoordinatorRoleRow:
        row = session.get(CoordinatorRoleRow, role_id)
        if row is None:
            raise NotFoundError(f"unknown role: {role_id}")
        return row

    def _validate_parent(self, session: Session, role_id: str, parent_role_id: str | None) -> None:
        if parent_role_id is None:
            return
        if parent_role_id == role_id:
            raise ConflictError("role cannot be its own parent")
        parent: CoordinatorRoleRow | None = self._require_role(session, parent_role_id)
        seen = {role_id}
        while parent is not None:
            if parent.role_id in seen:
                raise ConflictError("role hierarchy cannot contain a cycle")
            seen.add(parent.role_id)
            parent = (
                session.get(CoordinatorRoleRow, parent.parent_role_id)
                if parent.parent_role_id
                else None
            )

    @staticmethod
    def _validate_lease(
        row: RoleLeaseRow | None,
        holder_id: str,
        fencing_token: int,
        now: datetime,
        *,
        allow_expired: bool = False,
    ) -> None:
        if row is None:
            raise StaleFencingTokenError("role has no active lease")
        if row.holder_id != holder_id or row.fencing_token != fencing_token:
            raise StaleFencingTokenError("lease holder or fencing token is stale")
        if not allow_expired and _required_aware(row.expires_at) <= now:
            raise StaleFencingTokenError("role lease has expired")

    @staticmethod
    def _validate_lease_token(row: RoleLeaseRow | None, fencing_token: int, now: datetime) -> None:
        if row is None or row.fencing_token != fencing_token:
            raise StaleFencingTokenError("checkpoint fencing token is stale")
        if _required_aware(row.expires_at) <= now:
            raise StaleFencingTokenError("role lease has expired")

    @staticmethod
    def _append_event(
        session: Session, role_id: str, event_type: str, data: dict[str, Any]
    ) -> RoleEvent:
        existing = session.scalar(
            select(func.max(RoleEventRow.sequence)).where(RoleEventRow.role_id == role_id)
        )
        sequence = 0 if existing is None else int(existing) + 1
        event = RoleEvent(
            event_id=f"event-{uuid4()}",
            role_id=role_id,
            type=event_type,
            sequence=sequence,
            data=data,
        )
        session.add(
            RoleEventRow(
                event_id=event.event_id,
                role_id=role_id,
                type=event.type,
                sequence=event.sequence,
                payload_json=event.model_dump_json(),
                occurred_at=event.occurred_at,
            )
        )
        return event


def _work_row(item: WorkItem) -> WorkItemRow:
    return WorkItemRow(
        work_id=item.work_id,
        title=item.title,
        objective=item.objective,
        status=item.status,
        repository_id=item.repository_id,
        branch=item.branch,
        pull_request=item.pull_request,
        tags_json=_json(item.tags),
        extensions_json=_json(item.extensions),
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _assign_work(row: WorkItemRow, item: WorkItem) -> None:
    for name in ("title", "objective", "status", "repository_id", "branch", "pull_request"):
        setattr(row, name, getattr(item, name))
    row.tags_json = _json(item.tags)
    row.extensions_json = _json(item.extensions)
    row.updated_at = item.updated_at


def _work_model(row: WorkItemRow) -> WorkItem:
    return WorkItem(
        work_id=row.work_id,
        title=row.title,
        objective=row.objective,
        status=row.status,
        repository_id=row.repository_id,
        branch=row.branch,
        pull_request=row.pull_request,
        tags=json.loads(row.tags_json),
        extensions=json.loads(row.extensions_json),
        created_at=_required_aware(row.created_at),
        updated_at=_required_aware(row.updated_at),
    )


def _relationship_model(row: RelationshipRow) -> Relationship:
    return Relationship(
        relationship_id=row.relationship_id,
        source=EndpointRef(kind=EndpointKind(row.source_kind), id=row.source_id),
        target=EndpointRef(kind=EndpointKind(row.target_kind), id=row.target_id),
        type=row.type,
        metadata=json.loads(row.metadata_json),
        extensions=json.loads(row.extensions_json),
        created_at=_required_aware(row.created_at),
    )


def _role_row(role: CoordinatorRole) -> CoordinatorRoleRow:
    return CoordinatorRoleRow(
        role_id=role.role_id,
        role_type=role.role_type,
        scope=role.scope,
        charter=role.charter,
        authority_profile=role.authority_profile,
        autonomy_mode=str(role.autonomy_mode),
        parent_role_id=role.parent_role_id,
        current_conversation_id=role.current_conversation_id,
        checkpoint_version=role.checkpoint_version,
        status=str(role.status),
        extensions_json=_json(role.extensions),
        created_at=role.created_at,
        updated_at=role.updated_at,
    )


def _assign_role(row: CoordinatorRoleRow, role: CoordinatorRole) -> None:
    for name in (
        "role_type",
        "scope",
        "charter",
        "authority_profile",
        "parent_role_id",
        "current_conversation_id",
        "checkpoint_version",
    ):
        setattr(row, name, getattr(role, name))
    row.autonomy_mode = str(role.autonomy_mode)
    row.status = str(role.status)
    row.extensions_json = _json(role.extensions)
    row.updated_at = role.updated_at


def _role_model(row: CoordinatorRoleRow) -> CoordinatorRole:
    return CoordinatorRole(
        role_id=row.role_id,
        role_type=row.role_type,
        scope=row.scope,
        charter=row.charter,
        authority_profile=row.authority_profile,
        autonomy_mode=AutonomyMode(row.autonomy_mode),
        parent_role_id=row.parent_role_id,
        current_conversation_id=row.current_conversation_id,
        checkpoint_version=row.checkpoint_version,
        status=RoleStatus(row.status),
        extensions=json.loads(row.extensions_json),
        created_at=_required_aware(row.created_at),
        updated_at=_required_aware(row.updated_at),
    )


def _lease_model(row: RoleLeaseRow) -> RoleLease:
    return RoleLease(
        role_id=row.role_id,
        holder_id=row.holder_id,
        fencing_token=row.fencing_token,
        acquired_at=_required_aware(row.acquired_at),
        expires_at=_required_aware(row.expires_at),
    )


def _json(value: Any) -> str:
    return json.dumps(value, default=str, separators=(",", ":"), sort_keys=True)


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _required_aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _handoff_markdown(
    role: CoordinatorRole,
    checkpoint: RoleCheckpoint | None,
    reports: list[RoleReport],
) -> str:
    lines = [f"# {role.charter}", "", f"Role: `{role.role_id}`", f"Status: {role.status}"]
    if checkpoint:
        lines.extend(
            [
                "",
                f"## Checkpoint {checkpoint.version}",
                "",
                checkpoint.parent_summary,
                "",
                "## Current plan",
                *[f"- {item}" for item in checkpoint.current_plan],
                "",
                "## Open questions",
                *[f"- {item}" for item in checkpoint.open_questions],
                "",
                "## Blockers",
                *[f"- {item}" for item in checkpoint.blockers],
            ]
        )
    if reports:
        lines.extend(["", "## Recent child reports"])
        lines.extend(f"- {report.reporting_role_id}: {report.summary}" for report in reports)
    return "\n".join(lines).rstrip() + "\n"
