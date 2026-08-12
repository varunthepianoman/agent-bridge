from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from agent_bridge_catalog.app import create_app
from agent_bridge_catalog.collaboration import CollaborationStore
from agent_bridge_catalog.config import Settings
from agent_bridge_catalog.db import CollaborationMessageRow, Database
from agent_bridge_catalog.maintenance import (
    MaintenanceService,
    RetentionPolicy,
    backup_database,
    redact_sensitive,
    restore_database,
    verify_database,
)
from agent_bridge_catalog.repository import CatalogRepository
from agent_bridge_protocol import (
    CollaborationMessage,
    CollaborationOperation,
    EndpointKind,
    EndpointRef,
)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        state_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'catalog.db'}",
        node_id="hub",
        environment_id="test",
    )


def test_transcript_deletion_removes_raw_payload_and_fts_text(tmp_path: Path) -> None:
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        row = app.state.repository.upsert_discovered(
            {
                "provider_thread_id": "sensitive-thread",
                "title": "Ordinary title",
                "transcript_text": "unique-sensitive-phrase",
                "raw_metadata": {
                    "thread": {"turns": [{"text": "unique-sensitive-phrase"}]},
                    "stable_locator": "thread",
                },
            },
            node_id="hub",
            environment_id="test",
        )
        assert (
            client.get("/api/v1/search", params={"q": "unique-sensitive-phrase"}).json()["total"]
            == 1
        )
        response = client.delete(f"/api/v1/conversations/{row.conversation_id}/transcript")
        assert response.status_code == 200
        assert response.json()["conversation"]["transcript_text"] == ""
        assert (
            client.get("/api/v1/search", params={"q": "unique-sensitive-phrase"}).json()["total"]
            == 0
        )
        reread = app.state.repository.get(row.conversation_id)
        assert reread is not None
        assert "unique-sensitive-phrase" not in reread.raw_metadata_json
        assert "stable_locator" in reread.raw_metadata_json


def test_retention_deletes_only_old_terminal_collaboration(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'retention.db'}")
    database.initialize()
    store = CollaborationStore(database)
    old = datetime.now(UTC) - timedelta(days=200)
    recent = datetime.now(UTC) - timedelta(days=2)
    for identifier, occurred, state in (
        ("old-terminal", old, "received"),
        ("old-pending", old, "pending"),
        ("recent-terminal", recent, "received"),
    ):
        store.create_message(
            CollaborationMessage(
                collaboration_id=identifier,
                operation=CollaborationOperation.DIRECT,
                sender=EndpointRef(kind=EndpointKind.NODE, id="source"),
                destinations=[EndpointRef(kind=EndpointKind.ROLE, id="target")],
                body={"message": identifier},
                correlation_id=identifier,
                state=state,
                created_at=occurred,
                updated_at=occurred,
            )
        )
    counts = MaintenanceService(database).apply_retention(RetentionPolicy(collaboration_days=90))
    assert counts["collaboration_messages"] == 1
    with database.session() as session:
        remaining = {row.collaboration_id for row in session.query(CollaborationMessageRow)}
    assert remaining == {"old-pending", "recent-terminal"}


def test_verified_backup_and_non_overwriting_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.db"
    monkeypatch.setenv("AGENT_BRIDGE_DATABASE_URL", f"sqlite:///{source}")
    alembic = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    alembic.set_main_option("sqlalchemy.url", f"sqlite:///{source}")
    command.upgrade(alembic, "head")
    repository = CatalogRepository(Database(f"sqlite:///{source}"))
    repository.upsert_discovered(
        {"provider_thread_id": "backup-thread", "title": "Backed up"},
        node_id="hub",
        environment_id="test",
    )

    backup = tmp_path / "backups" / "catalog.db"
    result = backup_database(f"sqlite:///{source}", backup)
    assert result["integrity"] == "ok"
    assert result["revision"] == "0007"
    restored = tmp_path / "restore" / "catalog.db"
    assert restore_database(backup, restored)["integrity"] == "ok"
    assert verify_database(restored)["revision"] == "0007"
    with pytest.raises(FileExistsError, match="never overwrites"):
        restore_database(backup, restored)
    restored_repository = CatalogRepository(Database(f"sqlite:///{restored}"))
    rows, total = restored_repository.list()
    assert total == 1
    assert rows[0].title == "Backed up"


def test_node_credential_rotation_is_authenticated_and_invalidates_old_secret(
    tmp_path: Path,
) -> None:
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        old = "old node credential that is sufficiently long"
        provision = client.post(
            "/api/v1/nodes",
            json={
                "node_id": "robot",
                "display_name": "Robot",
                "platform": "linux",
                "credential": old,
            },
        )
        assert provision.status_code == 201
        unauthorized = client.post("/api/v1/nodes/robot/credentials/rotate")
        assert unauthorized.status_code == 401
        rotated = client.post(
            "/api/v1/nodes/robot/credentials/rotate",
            headers={"Authorization": f"Bearer {old}"},
        )
        assert rotated.status_code == 200
        new = rotated.json()["credential"]
        assert new != old
        old_auth = client.post(
            "/api/v1/node/heartbeat",
            json={"node_id": "robot"},
            headers={"Authorization": f"Bearer {old}"},
        )
        assert old_auth.status_code == 401
        new_auth = client.post(
            "/api/v1/node/heartbeat",
            json={"node_id": "robot"},
            headers={"Authorization": f"Bearer {new}"},
        )
        assert new_auth.status_code == 200


def test_secret_redaction_and_nats_control_permissions() -> None:
    value = redact_sensitive(
        {
            "token": "raw-token",
            "nested": {"Authorization": "Bearer abc", "safe": "visible"},
            "items": [{"password": "raw-password"}],
        }
    )
    assert value == {
        "token": "[REDACTED]",
        "nested": {"Authorization": "[REDACTED]", "safe": "visible"},
        "items": [{"password": "[REDACTED]"}],
    }
    config = (Path(__file__).parents[1] / "deploy/nats/nats-server.conf").read_text()
    catalog = config.split("user: $NATS_CATALOG_USER", 1)[1].split("user: $NATS_NODE_A_USER", 1)[0]
    assert "bridge.v1.control" not in catalog
    node_a = config.split("user: $NATS_NODE_A_USER", 1)[1].split("user: $NATS_NODE_B_USER", 1)[0]
    assert '"bridge.v1.control.node.node-a"' in node_a
    assert '"bridge.v1.control.>"' not in node_a


def test_remote_sync_redacts_secrets_and_normalizes_excluded_paths(tmp_path: Path) -> None:
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        credential = "sync node credential that is sufficiently long"
        assert (
            client.post(
                "/api/v1/nodes",
                json={
                    "node_id": "sync-node",
                    "display_name": "Sync node",
                    "platform": "linux",
                    "credential": credential,
                },
            ).status_code
            == 201
        )
        response = client.post(
            "/api/v1/node/sync",
            headers={"Authorization": f"Bearer {credential}"},
            json={
                "registration": {"node_id": "sync-node"},
                "environments": [
                    {
                        "environment_id": "host",
                        "exclude_folders": ["/srv/private"],
                    }
                ],
                "conversations": [
                    {
                        "provider_thread_id": "excluded",
                        "environment_id": "host",
                        "cwd": "/srv/public/../private/project",
                        "transcript_text": "must never import",
                    },
                    {
                        "provider_thread_id": "visible",
                        "environment_id": "host",
                        "title": "Visible",
                        "token": "raw-provider-token",
                        "nested": {"password": "raw-provider-password"},
                    },
                ],
            },
        )
        assert response.status_code == 200
        assert response.json() == {"discovered": 2, "imported": 1, "excluded": 1}
        rows, total = app.state.repository.list()
        assert total == 1
        assert rows[0].provider_thread_id == "visible"
        assert "raw-provider-token" not in rows[0].raw_metadata_json
        assert "raw-provider-password" not in rows[0].raw_metadata_json
        assert rows[0].raw_metadata_json.count("[REDACTED]") == 2
