from __future__ import annotations

import hashlib
import hmac
import json
import posixpath
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import PurePath
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Database, EnvironmentRow, NodeCommandRow, NodeRow
from .maintenance import redact_sensitive
from .repository import CatalogRepository, stable_conversation_id
from .schemas import EnvironmentRegistration, NodeRegistration

_PBKDF2_ITERATIONS = 180_000


class NodeAuthenticationError(ValueError):
    pass


class NodeStore:
    def __init__(
        self,
        database: Database,
        repository: CatalogRepository,
        auto_add_new_chats: Callable[[], bool] | None = None,
    ) -> None:
        self.database = database
        self.repository = repository
        self.auto_add_new_chats = auto_add_new_chats or (lambda: False)

    def provision(
        self,
        *,
        node_id: str,
        display_name: str,
        platform: str,
        capabilities: list[str],
        metadata: dict[str, Any],
        credential: str | None = None,
    ) -> tuple[dict[str, Any], str]:
        secret = credential or secrets.token_urlsafe(36)
        salt = secrets.token_hex(24)
        now = datetime.now(UTC)
        with self.database.session() as session:
            if session.get(NodeRow, node_id) is not None:
                raise ValueError("node already exists")
            row = NodeRow(
                node_id=node_id,
                display_name=display_name,
                platform=platform,
                capabilities_json=_json(sorted(set(capabilities))),
                metadata_json=_json(metadata),
                credential_salt=salt,
                credential_hash=_credential_hash(secret, salt),
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.commit()
            return self._node_dict(row, now=now), secret

    def authenticate(self, node_id: str, credential: str) -> NodeRow:
        with self.database.session() as session:
            row = session.get(NodeRow, node_id)
            if row is None or not hmac.compare_digest(
                row.credential_hash, _credential_hash(credential, row.credential_salt)
            ):
                raise NodeAuthenticationError("invalid node credential")
            session.expunge(row)
            return row

    def rotate_credential(self, node_id: str) -> str:
        """Replace the stored verifier and return the new credential exactly once."""

        secret = secrets.token_urlsafe(36)
        salt = secrets.token_hex(24)
        with self.database.session() as session:
            row = session.get(NodeRow, node_id)
            if row is None:
                raise LookupError("node not found")
            row.credential_salt = salt
            row.credential_hash = _credential_hash(secret, salt)
            row.updated_at = datetime.now(UTC)
            session.commit()
        return secret

    def list_nodes(self) -> list[dict[str, Any]]:
        now = datetime.now(UTC)
        with self.database.session() as session:
            rows = session.scalars(select(NodeRow).order_by(NodeRow.display_name)).all()
            return [
                self._node_dict(
                    row,
                    now=now,
                    environments=self._environment_dicts(session, row.node_id, now=now),
                )
                for row in rows
            ]

    def observe_execution_node(
        self,
        node_id: str,
        *,
        environment_id: str = "host",
        root_path: str | None = None,
        ttl_seconds: int = 600,
    ) -> dict[str, Any]:
        """Project an authenticated broker runner into the location catalog.

        The broker has already authenticated the publisher. This internal path
        deliberately does not expose or mint a reusable node API credential.
        """
        now = datetime.now(UTC)
        with self.database.session() as session:
            row = session.get(NodeRow, node_id)
            if row is None:
                salt = secrets.token_hex(24)
                row = NodeRow(
                    node_id=node_id,
                    display_name=node_id,
                    platform="broker-runner",
                    capabilities_json=_json(["codex"]),
                    metadata_json=_json({"registration_source": "broker_execution"}),
                    credential_salt=salt,
                    credential_hash=_credential_hash(secrets.token_urlsafe(36), salt),
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            row.last_seen_at = now
            row.heartbeat_expires_at = now + timedelta(seconds=ttl_seconds)
            row.updated_at = now
            environment = session.get(EnvironmentRow, (node_id, environment_id))
            if environment is None:
                environment = EnvironmentRow(
                    node_id=node_id,
                    environment_id=environment_id,
                    display_name=environment_id,
                    kind="host",
                    root_path=root_path,
                    sync_policy_json="{}",
                    metadata_json="{}",
                    created_at=now,
                    updated_at=now,
                )
                session.add(environment)
            elif root_path:
                environment.root_path = root_path
                environment.updated_at = now
            session.commit()
            return self._node_dict(
                row,
                now=now,
                environments=self._environment_dicts(session, node_id, now=now),
            )

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        now = datetime.now(UTC)
        with self.database.session() as session:
            row = session.get(NodeRow, node_id)
            return (
                self._node_dict(
                    row,
                    now=now,
                    environments=self._environment_dicts(session, row.node_id, now=now),
                )
                if row
                else None
            )

    def heartbeat(
        self,
        registration: NodeRegistration,
        *,
        ttl_seconds: int,
        capabilities: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        with self.database.session() as session:
            row = session.get(NodeRow, registration.node_id)
            if row is None:
                raise ValueError("node not found")
            if registration.display_name:
                row.display_name = registration.display_name
            if registration.platform:
                row.platform = registration.platform
            effective_capabilities = (
                capabilities if capabilities is not None else registration.capabilities
            )
            if effective_capabilities:
                row.capabilities_json = _json(sorted(set(effective_capabilities)))
            effective_metadata = metadata if metadata is not None else registration.metadata
            if effective_metadata:
                row.metadata_json = _json(effective_metadata)
            row.last_seen_at = now
            row.heartbeat_expires_at = now + timedelta(seconds=ttl_seconds)
            row.updated_at = now
            session.commit()
            return self._node_dict(row, now=now)

    def sync_catalog(
        self,
        registration: NodeRegistration,
        conversations: list[dict[str, Any]],
        environments: list[EnvironmentRegistration],
    ) -> dict[str, int]:
        self.heartbeat(registration, ttl_seconds=90)
        policies = {item.environment_id: item for item in environments}
        imported = excluded = 0
        for environment in environments:
            self._upsert_environment(registration.node_id, environment)
        for item in conversations:
            environment_id = str(item.get("environment_id") or "default")
            policy = policies.get(environment_id)
            if policy is None:
                policy = EnvironmentRegistration(environment_id=environment_id)
                self._upsert_environment(registration.node_id, policy)
            if self._is_excluded(item, registration.node_id, environment_id, policy):
                excluded += 1
                continue
            sanitized = redact_sensitive(dict(item))
            assert isinstance(sanitized, dict)
            sanitized.pop("node_id", None)
            sanitized.pop("environment_id", None)
            if not policy.include_transcript_text:
                sanitized["transcript_text"] = ""
            self.repository.upsert_discovered(
                sanitized,
                node_id=registration.node_id,
                environment_id=environment_id,
                transcript_included=policy.include_transcript_text,
                select_if_new=self.auto_add_new_chats(),
            )
            imported += 1
        self.repository.resolve_parents()
        return {"discovered": len(conversations), "imported": imported, "excluded": excluded}

    def queue_command(
        self,
        *,
        node_id: str,
        kind: str,
        payload: dict[str, Any],
        conversation_id: str | None = None,
        ttl_seconds: int = 300,
    ) -> dict[str, Any]:
        if not self.is_reachable(node_id):
            raise ValueError("node is unavailable")
        now = datetime.now(UTC)
        row = NodeCommandRow(
            command_id=f"cmd-{secrets.token_hex(12)}",
            node_id=node_id,
            kind=kind,
            conversation_id=conversation_id,
            payload_json=_json(payload),
            status="queued",
            attempt=0,
            created_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
        with self.database.session() as session:
            session.add(row)
            session.commit()
            return self._command_dict(row)

    def claim_command(self, node_id: str) -> dict[str, Any] | None:
        now = datetime.now(UTC)
        with self.database.session() as session:
            expired = session.scalars(
                select(NodeCommandRow).where(
                    NodeCommandRow.node_id == node_id,
                    NodeCommandRow.status == "queued",
                    NodeCommandRow.expires_at <= now,
                )
            ).all()
            for expired_row in expired:
                expired_row.status = "expired"
                expired_row.completed_at = now
            row = session.scalar(
                select(NodeCommandRow)
                .where(
                    NodeCommandRow.node_id == node_id,
                    NodeCommandRow.status == "queued",
                    NodeCommandRow.expires_at > now,
                )
                .order_by(NodeCommandRow.created_at)
                .limit(1)
            )
            if row is None:
                session.commit()
                return None
            claim_token = secrets.token_urlsafe(32)
            row.status = "claimed"
            row.attempt += 1
            row.claimed_at = now
            row.claim_token_hash = _token_hash(claim_token)
            session.commit()
            result = self._command_dict(row)
            result["claim_token"] = claim_token
            return result

    def complete_command(
        self,
        *,
        node_id: str,
        command_id: str,
        claim_token: str,
        status: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        with self.database.session() as session:
            row = session.get(NodeCommandRow, command_id)
            if row is None or row.node_id != node_id:
                raise LookupError("command not found")
            if row.claim_token_hash is None or not hmac.compare_digest(
                row.claim_token_hash, _token_hash(claim_token)
            ):
                raise NodeAuthenticationError("invalid command claim token")
            if row.status != "claimed":
                existing_result = json.loads(row.result_json) if row.result_json else {}
                if row.status == status and existing_result == result:
                    command = self._command_dict(row)
                    command["_already_completed"] = True
                    return command
                raise ValueError("command already has a different terminal result")
            row.status = status
            row.result_json = _json(result)
            row.completed_at = datetime.now(UTC)
            session.commit()
            return self._command_dict(row)

    def get_command(self, command_id: str) -> dict[str, Any] | None:
        with self.database.session() as session:
            row = session.get(NodeCommandRow, command_id)
            return self._command_dict(row) if row else None

    def is_reachable(self, node_id: str) -> bool:
        with self.database.session() as session:
            row = session.get(NodeRow, node_id)
            return bool(row and _aware(row.heartbeat_expires_at) > datetime.now(UTC))

    def location_status(self, node_id: str, environment_id: str) -> dict[str, bool | None]:
        """Return explicit location availability without changing conversation state."""
        with self.database.session() as session:
            node = session.get(NodeRow, node_id)
            if node is None:
                return {
                    "node_reachable": None,
                    "environment_available": None,
                    "location_available": None,
                }
            node_reachable = _aware(node.heartbeat_expires_at) > datetime.now(UTC)
            environment = session.get(EnvironmentRow, (node_id, environment_id))
            environment_available = bool(
                environment and environment.node_id == node_id and node_reachable
            )
            return {
                "node_reachable": node_reachable,
                "environment_available": environment_available,
                "location_available": environment_available,
            }

    def _upsert_environment(self, node_id: str, item: EnvironmentRegistration) -> None:
        now = datetime.now(UTC)
        policy = item.model_dump(
            include={
                "exclude_providers",
                "exclude_repositories",
                "exclude_folders",
                "exclude_conversation_ids",
                "include_transcript_text",
            }
        )
        with self.database.session() as session:
            row = session.get(EnvironmentRow, (node_id, item.environment_id))
            if row is None:
                row = EnvironmentRow(
                    environment_id=item.environment_id,
                    node_id=node_id,
                    display_name=item.display_name or item.environment_id,
                    kind=item.kind,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            row.display_name = item.display_name or row.display_name
            row.kind = item.kind
            row.root_path = item.root_path
            row.sync_policy_json = _json(policy)
            row.metadata_json = _json(item.metadata)
            row.updated_at = now
            session.commit()

    @staticmethod
    def _is_excluded(
        item: dict[str, Any],
        node_id: str,
        environment_id: str,
        policy: EnvironmentRegistration,
    ) -> bool:
        provider = str(item.get("provider") or "codex")
        thread_id = str(item.get("provider_thread_id") or "")
        conversation_id = stable_conversation_id(provider, thread_id, node_id, environment_id)
        if provider in policy.exclude_providers:
            return True
        if (
            thread_id in policy.exclude_conversation_ids
            or conversation_id in policy.exclude_conversation_ids
        ):
            return True
        repository = _normalized(str(item.get("repository") or item.get("git_origin_url") or ""))
        if repository and any(
            repository == _normalized(value) for value in policy.exclude_repositories
        ):
            return True
        cwd = _normalized(str(item.get("cwd") or ""))
        return bool(cwd and any(_path_contains(cwd, value) for value in policy.exclude_folders))

    @staticmethod
    def _node_dict(
        row: NodeRow, *, now: datetime, environments: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        expires = _aware(row.heartbeat_expires_at)
        return {
            "node_id": row.node_id,
            "display_name": row.display_name,
            "platform": row.platform,
            "capabilities": json.loads(row.capabilities_json),
            "metadata": json.loads(row.metadata_json),
            "last_seen_at": _iso(row.last_seen_at),
            "heartbeat_expires_at": _iso(row.heartbeat_expires_at),
            "reachable": expires > now,
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
            "environments": environments or [],
        }

    @staticmethod
    def _environment_dicts(
        session: Session, node_id: str, *, now: datetime
    ) -> list[dict[str, Any]]:
        node = session.get(NodeRow, node_id)
        reachable = bool(node and _aware(node.heartbeat_expires_at) > now)
        rows = session.scalars(
            select(EnvironmentRow)
            .where(EnvironmentRow.node_id == node_id)
            .order_by(EnvironmentRow.display_name)
        ).all()
        return [
            {
                "environment_id": row.environment_id,
                "display_name": row.display_name,
                "kind": row.kind,
                "root_path": row.root_path,
                "available": reachable,
                "last_seen_at": _iso(node.last_seen_at) if node else None,
                "metadata": json.loads(row.metadata_json),
            }
            for row in rows
        ]

    @staticmethod
    def _command_dict(row: NodeCommandRow) -> dict[str, Any]:
        return {
            "command_id": row.command_id,
            "node_id": row.node_id,
            "kind": row.kind,
            "conversation_id": row.conversation_id,
            **json.loads(row.payload_json),
            "status": row.status,
            "attempt": row.attempt,
            "result": json.loads(row.result_json) if row.result_json else None,
            "created_at": _iso(row.created_at),
            "expires_at": _iso(row.expires_at),
            "claimed_at": _iso(row.claimed_at),
            "completed_at": _iso(row.completed_at),
        }


def _credential_hash(secret: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", secret.encode(), bytes.fromhex(salt), _PBKDF2_ITERATIONS
    ).hex()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, default=str, separators=(",", ":"), sort_keys=True)


def _aware(value: datetime | None) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=UTC)
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    return _aware(value).isoformat() if value is not None else None


def _normalized(value: str) -> str:
    replaced = value.replace("\\", "/")
    return posixpath.normpath(replaced).rstrip("/").casefold()


def _path_contains(path: str, excluded: str) -> bool:
    excluded_path = _normalized(str(PurePath(excluded)))
    return path == excluded_path or path.startswith(f"{excluded_path}/")
