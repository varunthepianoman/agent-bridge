from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class ConversationRow(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint(
            "provider", "provider_thread_id", "node_id", "environment_id", name="uq_provider_thread"
        ),
    )

    conversation_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    conversation_number: Mapped[int | None] = mapped_column(Integer, unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    provider_thread_id: Mapped[str] = mapped_column(String(160), index=True)
    node_id: Mapped[str] = mapped_column(String(160), index=True)
    environment_id: Mapped[str] = mapped_column(String(160), index=True)
    title: Mapped[str] = mapped_column(Text, default="Untitled conversation")
    alias: Mapped[str] = mapped_column(Text, default="Untitled conversation")
    alias_updated_by: Mapped[str] = mapped_column(String(40), default="provider")
    alias_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_title: Mapped[str | None] = mapped_column(Text)
    preview: Mapped[str] = mapped_column(Text, default="")
    transcript_text: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(40), default="idle", index=True)
    source: Mapped[str | None] = mapped_column(String(80))
    cwd: Mapped[str | None] = mapped_column(Text)
    repository: Mapped[str | None] = mapped_column(Text)
    branch: Mapped[str | None] = mapped_column(Text)
    commit_hash: Mapped[str | None] = mapped_column(String(80))
    parent_provider_thread_id: Mapped[str | None] = mapped_column(String(160))
    parent_conversation_id: Mapped[str | None] = mapped_column(String(80), index=True)
    conversation_kind: Mapped[str] = mapped_column(String(40), default="full", index=True)
    delivery_mode: Mapped[str] = mapped_column(String(40), default="direct", index=True)
    selected: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    tags_json: Mapped[str] = mapped_column(Text, default="[]")
    resume_command: Mapped[str | None] = mapped_column(Text)
    raw_metadata_json: Mapped[str] = mapped_column(Text, default="{}")

    @property
    def tags(self) -> list[str]:
        value = json.loads(self.tags_json)
        return [str(item) for item in value] if isinstance(value, list) else []

    def as_dict(self, *, include_transcript: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "conversation_id": self.conversation_id,
            "conversation_number": self.conversation_number,
            "provider": self.provider,
            "provider_thread_id": self.provider_thread_id,
            "node_id": self.node_id,
            "environment_id": self.environment_id,
            "title": self.title,
            "alias": self.alias,
            "alias_updated_by": self.alias_updated_by,
            "alias_updated_at": _iso(self.alias_updated_at),
            "provider_title": self.provider_title,
            "preview": self.preview,
            "status": self.status,
            "source": self.source,
            "cwd": self.cwd,
            "repository": self.repository,
            "branch": self.branch,
            "commit_hash": self.commit_hash,
            "parent_conversation_id": self.parent_conversation_id,
            "conversation_kind": self.conversation_kind,
            "delivery_mode": self.delivery_mode,
            "selected": self.selected,
            "created_at": _iso(self.created_at),
            "last_activity_at": _iso(self.last_activity_at),
            "last_synced_at": _iso(self.last_synced_at),
            "pinned": self.pinned,
            "hidden": self.hidden,
            "archived": self.archived,
            "notes": self.notes,
            "tags": self.tags,
            "resume_command": self.resume_command,
        }
        if include_transcript:
            result["transcript_text"] = self.transcript_text
            result["raw_metadata"] = json.loads(self.raw_metadata_json)
        return result


class CollectionRow(Base):
    __tablename__ = "collections"

    collection_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(40), default="manual", index=True)
    filter_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class CollectionMemberRow(Base):
    __tablename__ = "collection_members"

    collection_id: Mapped[str] = mapped_column(
        ForeignKey("collections.collection_id", ondelete="CASCADE"), primary_key=True
    )
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"), primary_key=True
    )
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ConversationMessageRow(Base):
    __tablename__ = "conversation_messages"

    message_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    correlation_id: Mapped[str] = mapped_column(String(160), index=True)
    causation_id: Mapped[str | None] = mapped_column(String(160), index=True)
    source_conversation_id: Mapped[str | None] = mapped_column(String(80), index=True)
    target_conversation_id: Mapped[str | None] = mapped_column(String(80), index=True)
    room_id: Mapped[str | None] = mapped_column(String(160), index=True)
    actor_kind: Mapped[str] = mapped_column(String(40), default="human", index=True)
    operation: Mapped[str] = mapped_column(String(40), default="message", index=True)
    body: Mapped[str] = mapped_column(Text)
    delivery_strategy: Mapped[str] = mapped_column(String(40), default="queue", index=True)
    acknowledgement_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    delivery_route: Mapped[str | None] = mapped_column(String(40), index=True)
    state: Mapped[str] = mapped_column(String(40), default="queued", index=True)
    subject: Mapped[str | None] = mapped_column(String(320), index=True)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class MailboxDeliveryRow(Base):
    __tablename__ = "mailbox_deliveries"

    message_id: Mapped[str] = mapped_column(
        ForeignKey("conversation_messages.message_id", ondelete="CASCADE"), primary_key=True
    )
    recipient_conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    state: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    listener_id: Mapped[str | None] = mapped_column(String(160), index=True)
    fencing_token: Mapped[int | None] = mapped_column(BigInteger)
    detail: Mapped[str | None] = mapped_column(Text)
    reply_message_id: Mapped[str | None] = mapped_column(String(160), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledgement_detail: Mapped[str | None] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    claimed_revision: Mapped[int | None] = mapped_column(Integer)
    acknowledged_revision: Mapped[int | None] = mapped_column(Integer)
    terminal_revision: Mapped[int | None] = mapped_column(Integer)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    attention_emitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    acknowledgement_attention_emitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    terminal_attention_emitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )


class MailboxEventRow(Base):
    __tablename__ = "mailbox_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["message_id", "recipient_conversation_id"],
            ["mailbox_deliveries.message_id", "mailbox_deliveries.recipient_conversation_id"],
            ondelete="CASCADE",
        ),
    )

    event_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    message_id: Mapped[str] = mapped_column(String(160), index=True)
    recipient_conversation_id: Mapped[str] = mapped_column(String(80), index=True)
    event_kind: Mapped[str] = mapped_column(String(40), index=True)
    from_state: Mapped[str | None] = mapped_column(String(40))
    to_state: Mapped[str] = mapped_column(String(40), index=True)
    listener_id: Mapped[str | None] = mapped_column(String(160), index=True)
    fencing_token: Mapped[int | None] = mapped_column(BigInteger)
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class MailboxListenerRow(Base):
    __tablename__ = "mailbox_listeners"

    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"), primary_key=True
    )
    listener_id: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    fencing_token: Mapped[int] = mapped_column(BigInteger)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    stop_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class RoomRow(Base):
    __tablename__ = "rooms"

    room_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class RoomMemberRow(Base):
    __tablename__ = "room_members"

    room_id: Mapped[str] = mapped_column(
        ForeignKey("rooms.room_id", ondelete="CASCADE"), primary_key=True
    )
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"), primary_key=True
    )
    delivery_mode: Mapped[str] = mapped_column(String(40), default="mailbox")
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AttentionRow(Base):
    __tablename__ = "attention_items"

    attention_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    conversation_id: Mapped[str | None] = mapped_column(String(80), index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(160), index=True)
    category: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(Text)
    detail: Mapped[str] = mapped_column(Text, default="")
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NatsEventRow(Base):
    __tablename__ = "nats_events"

    event_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    category: Mapped[str] = mapped_column(String(40), index=True)
    direction: Mapped[str | None] = mapped_column(String(20), index=True)
    severity: Mapped[str] = mapped_column(String(20), default="info", index=True)
    subject: Mapped[str | None] = mapped_column(String(320), index=True)
    message_id: Mapped[str | None] = mapped_column(String(160), index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(160), index=True)
    node_id: Mapped[str | None] = mapped_column(String(160), index=True)
    detail_json: Mapped[str] = mapped_column(Text, default="{}")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class LegacyExportRow(Base):
    """One migration-time JSON snapshot of removed orchestration tables."""

    __tablename__ = "legacy_exports"

    export_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    source_revision: Mapped[str] = mapped_column(String(40))
    data_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class CatalogSettingRow(Base):
    __tablename__ = "catalog_settings"

    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class WorkItemRow(Base):
    __tablename__ = "work_items"

    work_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    objective: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="active", index=True)
    repository_id: Mapped[str | None] = mapped_column(String(320), index=True)
    branch: Mapped[str | None] = mapped_column(Text)
    pull_request: Mapped[str | None] = mapped_column(String(160), index=True)
    tags_json: Mapped[str] = mapped_column(Text, default="[]")
    extensions_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class RelationshipRow(Base):
    __tablename__ = "relationships"

    relationship_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    source_kind: Mapped[str] = mapped_column(String(40), index=True)
    source_id: Mapped[str] = mapped_column(String(160), index=True)
    target_kind: Mapped[str] = mapped_column(String(40), index=True)
    target_id: Mapped[str] = mapped_column(String(160), index=True)
    type: Mapped[str] = mapped_column(String(120), index=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    extensions_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class CoordinatorRoleRow(Base):
    __tablename__ = "coordinator_roles"

    role_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    role_type: Mapped[str] = mapped_column(String(80), index=True)
    scope: Mapped[str] = mapped_column(String(320), index=True)
    charter: Mapped[str] = mapped_column(Text)
    authority_profile: Mapped[str] = mapped_column(String(160))
    autonomy_mode: Mapped[str] = mapped_column(String(40), default="delegate")
    parent_role_id: Mapped[str | None] = mapped_column(
        ForeignKey("coordinator_roles.role_id", ondelete="RESTRICT"), index=True
    )
    current_conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.conversation_id", ondelete="SET NULL"), index=True
    )
    checkpoint_version: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(40), default="draft", index=True)
    extensions_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class RoleConversationRow(Base):
    __tablename__ = "role_conversations"
    __table_args__ = (UniqueConstraint("role_id", "conversation_id", name="uq_role_conversation"),)

    role_conversation_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role_id: Mapped[str] = mapped_column(
        ForeignKey("coordinator_roles.role_id", ondelete="CASCADE"), index=True
    )
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"), index=True
    )
    attached_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    detached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    handoff_summary: Mapped[str | None] = mapped_column(Text)


class RoleCheckpointRow(Base):
    __tablename__ = "role_checkpoints"

    role_id: Mapped[str] = mapped_column(
        ForeignKey("coordinator_roles.role_id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    fencing_token: Mapped[int] = mapped_column(Integer)
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class RoleEventRow(Base):
    __tablename__ = "role_events"
    __table_args__ = (UniqueConstraint("role_id", "sequence", name="uq_role_event_sequence"),)

    event_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    role_id: Mapped[str] = mapped_column(
        ForeignKey("coordinator_roles.role_id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(120), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    payload_json: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class RoleReportRow(Base):
    __tablename__ = "role_reports"

    report_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    reporting_role_id: Mapped[str] = mapped_column(
        ForeignKey("coordinator_roles.role_id", ondelete="CASCADE"), index=True
    )
    recipient_role_id: Mapped[str] = mapped_column(
        ForeignKey("coordinator_roles.role_id", ondelete="CASCADE"), index=True
    )
    checkpoint_version: Mapped[int] = mapped_column(Integer)
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class RoleLeaseRow(Base):
    __tablename__ = "role_leases"

    role_id: Mapped[str] = mapped_column(
        ForeignKey("coordinator_roles.role_id", ondelete="CASCADE"), primary_key=True
    )
    holder_id: Mapped[str] = mapped_column(String(160), index=True)
    fencing_token: Mapped[int] = mapped_column(Integer)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class NodeRow(Base):
    __tablename__ = "nodes"

    node_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    display_name: Mapped[str] = mapped_column(Text)
    platform: Mapped[str] = mapped_column(String(80), index=True)
    capabilities_json: Mapped[str] = mapped_column(Text, default="[]")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    credential_salt: Mapped[str] = mapped_column(String(128))
    credential_hash: Mapped[str] = mapped_column(String(128))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    heartbeat_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class EnvironmentRow(Base):
    __tablename__ = "environments"

    node_id: Mapped[str] = mapped_column(
        ForeignKey("nodes.node_id", ondelete="CASCADE"), primary_key=True
    )
    environment_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    display_name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(80), index=True)
    root_path: Mapped[str | None] = mapped_column(Text)
    sync_policy_json: Mapped[str] = mapped_column(Text, default="{}")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class NodeCommandRow(Base):
    __tablename__ = "node_commands"

    command_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    node_id: Mapped[str] = mapped_column(
        ForeignKey("nodes.node_id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(80), index=True)
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.conversation_id", ondelete="SET NULL"), index=True
    )
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(40), index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    claim_token_hash: Mapped[str | None] = mapped_column(String(128))
    result_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NodeTurnEventRow(Base):
    __tablename__ = "node_turn_events"

    event_id: Mapped[str] = mapped_column(String(500), primary_key=True)
    node_id: Mapped[str] = mapped_column(
        ForeignKey("nodes.node_id", ondelete="CASCADE"), index=True
    )
    environment_id: Mapped[str] = mapped_column(String(160), index=True)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    provider_thread_id: Mapped[str] = mapped_column(String(500), index=True)
    provider_turn_id: Mapped[str] = mapped_column(String(500), index=True)
    command_id: Mapped[str] = mapped_column(
        ForeignKey("node_commands.command_id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(40), index=True)
    detail: Mapped[str | None] = mapped_column(Text)
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class BrokerMessageRow(Base):
    """Queryable projection of a message whose delivery authority remains JetStream."""

    __tablename__ = "broker_messages"

    message_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    subject: Mapped[str] = mapped_column(String(320), index=True)
    stream: Mapped[str | None] = mapped_column(String(160), index=True)
    stream_sequence: Mapped[int | None] = mapped_column(BigInteger, index=True)
    message_type: Mapped[str] = mapped_column(String(80), index=True)
    source_kind: Mapped[str | None] = mapped_column(String(40), index=True)
    source_id: Mapped[str | None] = mapped_column(String(160), index=True)
    destination_kind: Mapped[str | None] = mapped_column(String(40), index=True)
    destination_id: Mapped[str | None] = mapped_column(String(160), index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(160), index=True)
    causation_id: Mapped[str | None] = mapped_column(String(160), index=True)
    work_id: Mapped[str | None] = mapped_column(String(160), index=True)
    role_id: Mapped[str | None] = mapped_column(String(160), index=True)
    execution_id: Mapped[str | None] = mapped_column(String(160), index=True)
    state: Mapped[str] = mapped_column(String(40), index=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    payload_summary_json: Mapped[str] = mapped_column(Text, default="{}")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    first_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class BrokerDeliveryRow(Base):
    __tablename__ = "broker_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "message_id", "consumer", "delivery_sequence", name="uq_broker_delivery_attempt"
        ),
    )

    delivery_id: Mapped[str] = mapped_column(String(240), primary_key=True)
    message_id: Mapped[str] = mapped_column(
        ForeignKey("broker_messages.message_id", ondelete="CASCADE"), index=True
    )
    stream: Mapped[str] = mapped_column(String(160), index=True)
    consumer: Mapped[str] = mapped_column(String(160), index=True)
    delivery_sequence: Mapped[int] = mapped_column(BigInteger)
    redelivery_count: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[str] = mapped_column(String(40), index=True)
    node_id: Mapped[str | None] = mapped_column(String(160), index=True)
    error_json: Mapped[str | None] = mapped_column(Text)
    delivered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ack_deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class BrokerDeadLetterRow(Base):
    __tablename__ = "broker_dead_letters"
    __table_args__ = (
        UniqueConstraint("message_id", "consumer", name="uq_broker_dead_letter_message_consumer"),
    )

    dead_letter_id: Mapped[str] = mapped_column(String(240), primary_key=True)
    message_id: Mapped[str] = mapped_column(
        ForeignKey("broker_messages.message_id", ondelete="CASCADE"), index=True
    )
    stream: Mapped[str] = mapped_column(String(160), index=True)
    consumer: Mapped[str] = mapped_column(String(160), index=True)
    reason: Mapped[str] = mapped_column(String(160), index=True)
    attempts: Mapped[int] = mapped_column(Integer)
    detail_json: Mapped[str] = mapped_column(Text, default="{}")
    dead_lettered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class BrokerConsumerStateRow(Base):
    __tablename__ = "broker_consumer_states"
    __table_args__ = (UniqueConstraint("stream", "consumer", name="uq_broker_stream_consumer"),)

    consumer_key: Mapped[str] = mapped_column(String(321), primary_key=True)
    stream: Mapped[str] = mapped_column(String(160), index=True)
    consumer: Mapped[str] = mapped_column(String(160), index=True)
    pending_count: Mapped[int] = mapped_column(BigInteger, default=0)
    ack_pending_count: Mapped[int] = mapped_column(BigInteger, default=0)
    redelivered_count: Mapped[int] = mapped_column(BigInteger, default=0)
    delivered_stream_sequence: Mapped[int] = mapped_column(BigInteger, default=0)
    ack_floor_stream_sequence: Mapped[int] = mapped_column(BigInteger, default=0)
    state: Mapped[str] = mapped_column(String(40), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    detail_json: Mapped[str] = mapped_column(Text, default="{}")


class ManualBridgeMessageRow(Base):
    __tablename__ = "manual_bridge_messages"

    message_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    correlation_id: Mapped[str] = mapped_column(String(160), index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    destination_kind: Mapped[str] = mapped_column(String(40), index=True)
    destination_id: Mapped[str] = mapped_column(String(160), index=True)
    work_id: Mapped[str | None] = mapped_column(String(160), index=True)
    execution_id: Mapped[str | None] = mapped_column(String(160), index=True)
    subject: Mapped[str] = mapped_column(String(320), index=True)
    envelope_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), index=True)
    error: Mapped[str | None] = mapped_column(Text)
    stream: Mapped[str | None] = mapped_column(String(160), index=True)
    stream_sequence: Mapped[int | None] = mapped_column(BigInteger)
    duplicate: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class BridgeExecutionRow(Base):
    __tablename__ = "bridge_executions"

    execution_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    request_message_id: Mapped[str] = mapped_column(
        ForeignKey("manual_bridge_messages.message_id", ondelete="RESTRICT"), unique=True
    )
    cancellation_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("manual_bridge_messages.message_id", ondelete="SET NULL")
    )
    operation: Mapped[str] = mapped_column(String(80), index=True)
    target_kind: Mapped[str] = mapped_column(String(40), index=True)
    target_id: Mapped[str] = mapped_column(String(160), index=True)
    work_id: Mapped[str | None] = mapped_column(String(160), index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(160), index=True)
    adapter: Mapped[str | None] = mapped_column(String(160), index=True)
    instruction: Mapped[str] = mapped_column(Text)
    request_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), index=True)
    result_json: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class BridgeExecutionAttemptRow(Base):
    __tablename__ = "bridge_execution_attempts"
    __table_args__ = (
        UniqueConstraint("execution_id", "attempt_number", name="uq_bridge_execution_attempt"),
    )

    attempt_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    execution_id: Mapped[str] = mapped_column(
        ForeignKey("bridge_executions.execution_id", ondelete="CASCADE"), index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer)
    node_id: Mapped[str | None] = mapped_column(String(160), index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    progress_json: Mapped[str] = mapped_column(Text, default="[]")
    result_json: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class CoordinatorIntakeRow(Base):
    __tablename__ = "coordinator_intakes"

    request_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    request_json: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    routed_work_id: Mapped[str | None] = mapped_column(String(160), index=True)
    routed_role_id: Mapped[str | None] = mapped_column(
        ForeignKey("coordinator_roles.role_id", ondelete="SET NULL"), index=True
    )
    proposed_actions_json: Mapped[str] = mapped_column(Text, default="[]")
    proposed_topology_json: Mapped[str] = mapped_column(Text, default="{}")
    attention_required: Mapped[str | None] = mapped_column(Text)
    approval_required: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    executed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    decision_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class CoordinatorIntakeEventRow(Base):
    __tablename__ = "coordinator_intake_events"
    __table_args__ = (
        UniqueConstraint("request_id", "sequence", name="uq_coordinator_intake_event"),
    )

    event_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    request_id: Mapped[str] = mapped_column(
        ForeignKey("coordinator_intakes.request_id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(80), index=True)
    data_json: Mapped[str] = mapped_column(Text, default="{}")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class CoordinatorActivationRow(Base):
    __tablename__ = "coordinator_activations"

    activation_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    role_id: Mapped[str] = mapped_column(
        ForeignKey("coordinator_roles.role_id", ondelete="CASCADE"), index=True
    )
    intake_request_id: Mapped[str | None] = mapped_column(
        ForeignKey("coordinator_intakes.request_id", ondelete="SET NULL"), index=True
    )
    holder_id: Mapped[str] = mapped_column(String(160), index=True)
    fencing_token: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    checkpoint_version_before: Mapped[int] = mapped_column(Integer)
    checkpoint_version_after: Mapped[int | None] = mapped_column(Integer)
    conversation_id: Mapped[str | None] = mapped_column(String(160), index=True)
    authority_json: Mapped[str] = mapped_column(Text)
    usage_json: Mapped[str] = mapped_column(Text)
    context_json: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    error: Mapped[str | None] = mapped_column(Text)


class RoleRollupStateRow(Base):
    __tablename__ = "role_rollup_states"
    __table_args__ = (
        UniqueConstraint("parent_role_id", "child_role_id", name="uq_role_rollup_parent_child"),
    )

    rollup_id: Mapped[str] = mapped_column(String(321), primary_key=True)
    parent_role_id: Mapped[str] = mapped_column(
        ForeignKey("coordinator_roles.role_id", ondelete="CASCADE"), index=True
    )
    child_role_id: Mapped[str] = mapped_column(
        ForeignKey("coordinator_roles.role_id", ondelete="CASCADE"), index=True
    )
    incorporated_checkpoint_version: Mapped[int] = mapped_column(Integer, default=0)
    report_id: Mapped[str | None] = mapped_column(
        ForeignKey("role_reports.report_id", ondelete="SET NULL"), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class RegisteredEndpointRow(Base):
    __tablename__ = "registered_endpoints"

    endpoint_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    display_name: Mapped[str] = mapped_column(Text)
    address_kind: Mapped[str] = mapped_column(String(40), index=True)
    address_id: Mapped[str] = mapped_column(String(160), index=True)
    capabilities_json: Mapped[str] = mapped_column(Text, default="[]")
    work_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(40), index=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    extensions_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class CollaborationRoomRow(Base):
    __tablename__ = "collaboration_rooms"

    room_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    work_id: Mapped[str | None] = mapped_column(String(160), index=True)
    durable: Mapped[bool] = mapped_column(Boolean, default=True)
    members_json: Mapped[str] = mapped_column(Text, default="[]")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    extensions_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class CollaborationMessageRow(Base):
    __tablename__ = "collaboration_messages"

    collaboration_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    operation: Mapped[str] = mapped_column(String(40), index=True)
    sender_kind: Mapped[str] = mapped_column(String(40), index=True)
    sender_id: Mapped[str] = mapped_column(String(160), index=True)
    destinations_json: Mapped[str] = mapped_column(Text)
    body_json: Mapped[str] = mapped_column(Text)
    work_id: Mapped[str | None] = mapped_column(String(160), index=True)
    correlation_id: Mapped[str] = mapped_column(String(160), index=True)
    causation_id: Mapped[str | None] = mapped_column(String(160), index=True)
    reply_to_json: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(40), index=True)
    bridge_message_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    error: Mapped[str | None] = mapped_column(Text)
    extensions_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def make_engine(database_url: str) -> Engine:
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, connect_args=connect_args)
    if database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    return engine


class Database:
    def __init__(self, database_url: str) -> None:
        self.engine = make_engine(database_url)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def initialize(self) -> None:
        core_table_names = (
            "conversations",
            "collections",
            "collection_members",
            "conversation_messages",
            "mailbox_deliveries",
            "mailbox_events",
            "mailbox_listeners",
            "rooms",
            "room_members",
            "attention_items",
            "nats_events",
            "legacy_exports",
            "catalog_settings",
            "nodes",
            "environments",
            "node_commands",
            "node_turn_events",
            "broker_messages",
            "broker_deliveries",
            "broker_dead_letters",
            "broker_consumer_states",
        )
        Base.metadata.create_all(
            self.engine,
            tables=[Base.metadata.tables[name] for name in core_table_names],
        )
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS conversation_fts USING fts5(
                    conversation_id UNINDEXED,
                    title,
                    preview,
                    transcript_text,
                    notes,
                    tags
                )
                """
            )

    def session(self) -> Session:
        return self.sessions()
