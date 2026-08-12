"""Durable collaboration registry and high-level Bridge operations."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select

from agent_bridge_protocol.models import (
    BridgeEnvelope,
    CollaborationMessage,
    CollaborationOperation,
    CollaborationRoom,
    EndpointKind,
    EndpointRef,
    MessageKind,
    RegisteredEndpoint,
)

from .db import (
    CollaborationMessageRow,
    CollaborationRoomRow,
    ConversationRow,
    Database,
    RegisteredEndpointRow,
)
from .manual_bridge import ManualBridgeService
from .roles import ConflictError, NotFoundError, RoleStore


class CollaborationStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def register_endpoint(self, endpoint: RegisteredEndpoint) -> RegisteredEndpoint:
        with self.database.session() as session:
            if session.get(RegisteredEndpointRow, endpoint.endpoint_id):
                raise ConflictError(f"endpoint already exists: {endpoint.endpoint_id}")
            session.add(_endpoint_row(endpoint))
            session.commit()
        return endpoint

    def get_endpoint(self, endpoint_id: str) -> RegisteredEndpoint | None:
        with self.database.session() as session:
            row = session.get(RegisteredEndpointRow, endpoint_id)
            return _endpoint_model(row) if row else None

    def list_endpoints(
        self,
        *,
        capability: str | None = None,
        work_id: str | None = None,
        status: str | None = None,
    ) -> list[RegisteredEndpoint]:
        with self.database.session() as session:
            statement = select(RegisteredEndpointRow)
            if status:
                statement = statement.where(RegisteredEndpointRow.status == status)
            rows = session.scalars(
                statement.order_by(
                    RegisteredEndpointRow.display_name, RegisteredEndpointRow.endpoint_id
                )
            ).all()
            items = [_endpoint_model(row) for row in rows]
        if capability:
            items = [item for item in items if capability in item.capabilities]
        if work_id:
            items = [item for item in items if work_id in item.work_ids]
        return items

    def update_endpoint(self, endpoint_id: str, changes: dict[str, Any]) -> RegisteredEndpoint:
        current = self.get_endpoint(endpoint_id)
        if current is None:
            raise NotFoundError(f"unknown endpoint: {endpoint_id}")
        forbidden = {"endpoint_id", "schema_version", "created_at"}.intersection(changes)
        if forbidden:
            raise ValueError(f"immutable endpoint fields: {', '.join(sorted(forbidden))}")
        updated = RegisteredEndpoint.model_validate(
            {
                **current.model_dump(mode="json"),
                **changes,
                "updated_at": _now().isoformat(),
            }
        )
        with self.database.session() as session:
            row = session.get(RegisteredEndpointRow, endpoint_id)
            assert row is not None
            _assign_endpoint(row, updated)
            session.commit()
        return updated

    def create_room(self, room: CollaborationRoom) -> CollaborationRoom:
        _unique_members(room.members)
        with self.database.session() as session:
            if session.get(CollaborationRoomRow, room.room_id):
                raise ConflictError(f"room already exists: {room.room_id}")
            session.add(_room_row(room))
            session.commit()
        return room

    def get_room(self, room_id: str) -> CollaborationRoom | None:
        with self.database.session() as session:
            row = session.get(CollaborationRoomRow, room_id)
            return _room_model(row) if row else None

    def list_rooms(self, *, work_id: str | None = None) -> list[CollaborationRoom]:
        with self.database.session() as session:
            statement = select(CollaborationRoomRow)
            if work_id:
                statement = statement.where(CollaborationRoomRow.work_id == work_id)
            rows = session.scalars(statement.order_by(CollaborationRoomRow.updated_at.desc())).all()
            return [_room_model(row) for row in rows]

    def update_room(self, room_id: str, changes: dict[str, Any]) -> CollaborationRoom:
        current = self.get_room(room_id)
        if current is None:
            raise NotFoundError(f"unknown room: {room_id}")
        forbidden = {"room_id", "schema_version", "created_at"}.intersection(changes)
        if forbidden:
            raise ValueError(f"immutable room fields: {', '.join(sorted(forbidden))}")
        updated = CollaborationRoom.model_validate(
            {**current.model_dump(mode="json"), **changes, "updated_at": _now().isoformat()}
        )
        _unique_members(updated.members)
        with self.database.session() as session:
            row = session.get(CollaborationRoomRow, room_id)
            assert row is not None
            _assign_room(row, updated)
            session.commit()
        return updated

    def create_message(
        self, item: CollaborationMessage, *, validate_convention: bool = True
    ) -> CollaborationMessage:
        if validate_convention:
            self._validate_convention(item)
        with self.database.session() as session:
            if session.get(CollaborationMessageRow, item.collaboration_id):
                raise ConflictError(
                    f"collaboration message already exists: {item.collaboration_id}"
                )
            session.add(_message_row(item))
            session.commit()
        return item

    def ingest_envelope(self, envelope: BridgeEnvelope) -> CollaborationMessage:
        """Idempotently project an inbound envelope into collaboration history."""

        collaboration_id = str(
            envelope.extensions.get("agent_bridge.collaboration_id") or envelope.message_id
        )
        existing = self.get_message(collaboration_id)
        if existing is not None:
            return existing
        logical_sender = envelope.extensions.get("agent_bridge.logical_sender")
        sender = (
            EndpointRef.model_validate(logical_sender)
            if isinstance(logical_sender, dict)
            else envelope.sender
        )
        item = CollaborationMessage(
            collaboration_id=collaboration_id,
            operation=_operation_for_kind(envelope.kind),
            sender=sender,
            destinations=[envelope.destination],
            body=envelope.body,
            work_id=envelope.work_id,
            correlation_id=envelope.correlation_id or envelope.message_id,
            causation_id=envelope.causation_id,
            reply_to=envelope.reply_to,
            state="received",
            bridge_message_ids=[envelope.message_id],
            extensions=envelope.extensions,
            created_at=envelope.created_at,
            updated_at=envelope.created_at,
        )
        return self.create_message(item, validate_convention=False)

    def update_message_delivery(
        self, collaboration_id: str, *, bridge_message_ids: list[str], state: str, error: str | None
    ) -> CollaborationMessage:
        with self.database.session() as session:
            row = session.get(CollaborationMessageRow, collaboration_id)
            if row is None:
                raise NotFoundError(f"unknown collaboration message: {collaboration_id}")
            row.bridge_message_ids_json = _json(bridge_message_ids)
            row.state = state
            row.error = error
            row.updated_at = _now()
            session.commit()
            return _message_model(row)

    def get_message(self, collaboration_id: str) -> CollaborationMessage | None:
        with self.database.session() as session:
            row = session.get(CollaborationMessageRow, collaboration_id)
            return _message_model(row) if row else None

    def list_messages(
        self,
        *,
        work_id: str | None = None,
        correlation_id: str | None = None,
        operation: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[CollaborationMessage], int]:
        filters = []
        if work_id:
            filters.append(CollaborationMessageRow.work_id == work_id)
        if correlation_id:
            filters.append(CollaborationMessageRow.correlation_id == correlation_id)
        if operation:
            filters.append(CollaborationMessageRow.operation == operation)
        with self.database.session() as session:
            total = int(
                session.scalar(
                    select(func.count()).select_from(CollaborationMessageRow).where(*filters)
                )
                or 0
            )
            rows = session.scalars(
                select(CollaborationMessageRow)
                .where(*filters)
                .order_by(CollaborationMessageRow.created_at.desc())
                .limit(limit)
                .offset(offset)
            ).all()
            return [_message_model(row) for row in rows], total

    def resolve_destinations(
        self,
        *,
        operation: CollaborationOperation,
        destinations: list[EndpointRef],
        capability: str | None = None,
        room_id: str | None = None,
    ) -> list[EndpointRef]:
        resolved = list(destinations)
        if capability and operation == CollaborationOperation.CAPABILITY:
            resolved.append(EndpointRef(kind=EndpointKind.CAPABILITY, id=capability))
        elif capability and operation == CollaborationOperation.FANOUT:
            resolved.extend(
                item.address for item in self.list_endpoints(capability=capability, status="active")
            )
        if room_id:
            room = self.get_room(room_id)
            if room is None:
                raise NotFoundError(f"unknown room: {room_id}")
            if operation == CollaborationOperation.FANOUT:
                resolved.extend(room.members)
            else:
                resolved.append(EndpointRef(kind=EndpointKind.ROOM, id=room.room_id))
        return _unique_members(resolved)

    def list_native_subagents(self) -> list[dict[str, Any]]:
        """Expose provider-native families without projecting their internal traffic."""

        with self.database.session() as session:
            all_rows = list(session.scalars(select(ConversationRow)).all())
        by_id = {row.conversation_id: row for row in all_rows}
        items: list[dict[str, Any]] = []
        for row in sorted(
            (item for item in all_rows if item.parent_conversation_id),
            key=lambda item: item.last_activity_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        ):
            root_id = row.conversation_id
            seen: set[str] = set()
            current = row
            while current.parent_conversation_id and current.conversation_id not in seen:
                seen.add(current.conversation_id)
                root_id = current.parent_conversation_id
                parent = by_id.get(current.parent_conversation_id)
                if parent is None:
                    break
                current = parent
            metadata = json.loads(row.raw_metadata_json)
            addressable = metadata.get("agent_bridge_addressable") is True
            item: dict[str, Any] = {
                "conversation_id": row.conversation_id,
                "parent_conversation_id": row.parent_conversation_id,
                "root_conversation_id": root_id,
                "title": row.title,
                "provider": row.provider,
                "addressable": addressable,
            }
            if addressable:
                item["address"] = EndpointRef(
                    kind=EndpointKind.CONVERSATION, id=row.conversation_id
                ).model_dump(mode="json")
            items.append(item)
        return items

    def _validate_convention(self, item: CollaborationMessage) -> None:
        operation = CollaborationOperation(item.operation)
        parents = {
            CollaborationOperation.CRITIQUE: {
                CollaborationOperation.PROPOSAL,
                CollaborationOperation.REVISION,
            },
            CollaborationOperation.REVISION: {CollaborationOperation.CRITIQUE},
            CollaborationOperation.ACCEPTANCE: {
                CollaborationOperation.PROPOSAL,
                CollaborationOperation.REVISION,
            },
            CollaborationOperation.REPLY: {CollaborationOperation.REQUEST},
        }
        allowed = parents.get(operation)
        if allowed is None:
            return
        if not item.causation_id:
            raise ValueError(f"{operation} requires causation_id")
        parent = self.get_message(item.causation_id)
        if parent is None:
            raise NotFoundError(f"unknown causation message: {item.causation_id}")
        if CollaborationOperation(parent.operation) not in allowed:
            expected = ", ".join(sorted(str(value) for value in allowed))
            raise ValueError(f"{operation} must follow one of: {expected}")
        if item.correlation_id != parent.correlation_id:
            raise ValueError("reply and review messages must preserve the parent correlation_id")


class CollaborationService:
    def __init__(self, store: CollaborationStore, bridge: ManualBridgeService) -> None:
        self.store = store
        self.bridge = bridge

    async def submit(
        self,
        *,
        operation: CollaborationOperation,
        sender: EndpointRef,
        body: dict[str, Any],
        destinations: list[EndpointRef],
        capability: str | None = None,
        room_id: str | None = None,
        work_id: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        reply_to: EndpointRef | None = None,
        extensions: dict[str, Any] | None = None,
    ) -> CollaborationMessage:
        resolved = self.store.resolve_destinations(
            operation=operation,
            destinations=destinations,
            capability=capability,
            room_id=room_id,
        )
        if not resolved:
            raise ValueError("collaboration operation resolved no destinations")
        parent = self.store.get_message(causation_id) if causation_id else None
        effective_correlation = correlation_id or (
            parent.correlation_id if parent else f"corr-{uuid4().hex}"
        )
        now = _now()
        item = CollaborationMessage(
            collaboration_id=f"collab-{uuid4().hex}",
            operation=operation,
            sender=sender,
            destinations=resolved,
            body=body,
            work_id=work_id,
            correlation_id=effective_correlation,
            causation_id=causation_id,
            reply_to=reply_to,
            extensions=extensions or {},
            created_at=now,
            updated_at=now,
        )
        self.store.create_message(item)
        message_ids: list[str] = []
        errors: list[str] = []
        for destination in resolved:
            result = await self.bridge.submit_message(
                envelope_input={
                    "kind": _message_kind(operation),
                    "destination": destination.model_dump(mode="json"),
                    "body": body,
                    "reply_to": reply_to.model_dump(mode="json") if reply_to else None,
                    "work_id": work_id,
                    "correlation_id": effective_correlation,
                    "causation_id": causation_id,
                    "extensions": {
                        **(extensions or {}),
                        "agent_bridge.collaboration_id": item.collaboration_id,
                        "agent_bridge.logical_sender": sender.model_dump(mode="json"),
                        "agent_bridge.causation_id": causation_id,
                    },
                }
            )
            message = result["message"]
            message_ids.append(str(message["message_id"]))
            if message["status"] != "published":
                errors.append(str(message.get("error") or message["status"]))
        state = (
            "published"
            if not errors
            else "partial_failure"
            if len(errors) < len(resolved)
            else "failed"
        )
        return self.store.update_message_delivery(
            item.collaboration_id,
            bridge_message_ids=message_ids,
            state=state,
            error="; ".join(errors) if errors else None,
        )


class AsyncCollaborationEnvelopeSink:
    """Async adapter used by the persist-before-ACK transport projection worker."""

    def __init__(self, store: CollaborationStore) -> None:
        self.store = store

    async def ingest_envelope(self, envelope: BridgeEnvelope, *, subject: str) -> None:
        del subject
        await asyncio.to_thread(self.store.ingest_envelope, envelope)


def topology(store: CollaborationStore, roles: RoleStore) -> dict[str, Any]:
    endpoints = store.list_endpoints()
    rooms = store.list_rooms()
    relationships = roles.list_relationships()
    nodes = [
        {
            "id": item.endpoint_id,
            "kind": "registered_endpoint",
            "label": item.display_name,
            "data": item,
        }
        for item in endpoints
    ]
    nodes.extend(
        {"id": item.room_id, "kind": "room", "label": item.name, "data": item} for item in rooms
    )
    edges = [
        {
            "id": item.relationship_id,
            "source": item.source.id,
            "target": item.target.id,
            "type": item.type,
            "metadata": item.metadata,
            "extensions": item.extensions,
        }
        for item in relationships
    ]
    for room in rooms:
        edges.extend(
            {
                "id": f"{room.room_id}:member:{member.kind}:{member.id}",
                "source": room.room_id,
                "target": member.id,
                "type": "room_member",
                "metadata": {},
                "extensions": {},
            }
            for member in room.members
        )
    for item in store.list_native_subagents():
        nodes.append(
            {
                "id": item["conversation_id"],
                "kind": "native_subagent",
                "label": item["title"],
                "data": item,
            }
        )
        edges.append(
            {
                "id": f"native-parent:{item['parent_conversation_id']}:{item['conversation_id']}",
                "source": item["parent_conversation_id"],
                "target": item["conversation_id"],
                "type": "provider_native_child",
                "metadata": {"provider": item["provider"]},
                "extensions": {},
            }
        )
    return {"nodes": nodes, "edges": edges}


def _message_kind(operation: CollaborationOperation) -> MessageKind:
    mapping = {
        CollaborationOperation.REQUEST: MessageKind.REQUEST,
        CollaborationOperation.REPLY: MessageKind.RESPONSE,
        CollaborationOperation.PROPOSAL: MessageKind.PROPOSAL,
        CollaborationOperation.CRITIQUE: MessageKind.CRITIQUE,
        CollaborationOperation.REVISION: MessageKind.REVISION,
        CollaborationOperation.ACCEPTANCE: MessageKind.ACCEPTANCE,
    }
    return mapping.get(operation, MessageKind.MESSAGE)


def _operation_for_kind(kind: MessageKind) -> CollaborationOperation:
    mapping = {
        MessageKind.REQUEST: CollaborationOperation.REQUEST,
        MessageKind.RESPONSE: CollaborationOperation.REPLY,
        MessageKind.PROPOSAL: CollaborationOperation.PROPOSAL,
        MessageKind.CRITIQUE: CollaborationOperation.CRITIQUE,
        MessageKind.REVISION: CollaborationOperation.REVISION,
        MessageKind.ACCEPTANCE: CollaborationOperation.ACCEPTANCE,
    }
    return mapping.get(kind, CollaborationOperation.DIRECT)


def _endpoint_row(item: RegisteredEndpoint) -> RegisteredEndpointRow:
    return RegisteredEndpointRow(
        endpoint_id=item.endpoint_id,
        display_name=item.display_name,
        address_kind=str(item.address.kind),
        address_id=item.address.id,
        capabilities_json=_json(item.capabilities),
        work_ids_json=_json(item.work_ids),
        status=item.status,
        metadata_json=_json(item.metadata),
        extensions_json=_json(item.extensions),
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _assign_endpoint(row: RegisteredEndpointRow, item: RegisteredEndpoint) -> None:
    replacement = _endpoint_row(item)
    for key in (
        "display_name",
        "address_kind",
        "address_id",
        "capabilities_json",
        "work_ids_json",
        "status",
        "metadata_json",
        "extensions_json",
        "updated_at",
    ):
        setattr(row, key, getattr(replacement, key))


def _endpoint_model(row: RegisteredEndpointRow) -> RegisteredEndpoint:
    return RegisteredEndpoint(
        endpoint_id=row.endpoint_id,
        display_name=row.display_name,
        address=EndpointRef(kind=EndpointKind(row.address_kind), id=row.address_id),
        capabilities=json.loads(row.capabilities_json),
        work_ids=json.loads(row.work_ids_json),
        status=row.status,
        metadata=json.loads(row.metadata_json),
        extensions=json.loads(row.extensions_json),
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
    )


def _room_row(item: CollaborationRoom) -> CollaborationRoomRow:
    return CollaborationRoomRow(
        room_id=item.room_id,
        name=item.name,
        work_id=item.work_id,
        durable=item.durable,
        members_json=_json([member.model_dump(mode="json") for member in item.members]),
        metadata_json=_json(item.metadata),
        extensions_json=_json(item.extensions),
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _assign_room(row: CollaborationRoomRow, item: CollaborationRoom) -> None:
    replacement = _room_row(item)
    for key in (
        "name",
        "work_id",
        "durable",
        "members_json",
        "metadata_json",
        "extensions_json",
        "updated_at",
    ):
        setattr(row, key, getattr(replacement, key))


def _room_model(row: CollaborationRoomRow) -> CollaborationRoom:
    return CollaborationRoom(
        room_id=row.room_id,
        name=row.name,
        work_id=row.work_id,
        durable=row.durable,
        members=[EndpointRef.model_validate(item) for item in json.loads(row.members_json)],
        metadata=json.loads(row.metadata_json),
        extensions=json.loads(row.extensions_json),
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
    )


def _message_row(item: CollaborationMessage) -> CollaborationMessageRow:
    return CollaborationMessageRow(
        collaboration_id=item.collaboration_id,
        operation=str(item.operation),
        sender_kind=str(item.sender.kind),
        sender_id=item.sender.id,
        destinations_json=_json([value.model_dump(mode="json") for value in item.destinations]),
        body_json=_json(item.body),
        work_id=item.work_id,
        correlation_id=item.correlation_id,
        causation_id=item.causation_id,
        reply_to_json=_json(item.reply_to.model_dump(mode="json")) if item.reply_to else None,
        state=item.state,
        bridge_message_ids_json=_json(item.bridge_message_ids),
        error=item.error,
        extensions_json=_json(item.extensions),
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _message_model(row: CollaborationMessageRow) -> CollaborationMessage:
    return CollaborationMessage(
        collaboration_id=row.collaboration_id,
        operation=CollaborationOperation(row.operation),
        sender=EndpointRef(kind=EndpointKind(row.sender_kind), id=row.sender_id),
        destinations=[
            EndpointRef.model_validate(item) for item in json.loads(row.destinations_json)
        ],
        body=json.loads(row.body_json),
        work_id=row.work_id,
        correlation_id=row.correlation_id,
        causation_id=row.causation_id,
        reply_to=EndpointRef.model_validate_json(row.reply_to_json) if row.reply_to_json else None,
        state=row.state,
        bridge_message_ids=json.loads(row.bridge_message_ids_json),
        error=row.error,
        extensions=json.loads(row.extensions_json),
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
    )


def _unique_members(items: list[EndpointRef]) -> list[EndpointRef]:
    found: dict[tuple[str, str], EndpointRef] = {}
    for item in items:
        found[(str(item.kind), item.id)] = item
    return list(found.values())


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)
