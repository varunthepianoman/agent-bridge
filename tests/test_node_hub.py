from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_bridge_catalog.app import create_app
from agent_bridge_catalog.config import Settings
from agent_bridge_catalog.db import Database, NodeRow
from agent_bridge_catalog.node_api import mount_node_api
from agent_bridge_catalog.nodes import NodeStore
from agent_bridge_catalog.repository import CatalogRepository, stable_conversation_id


def _client(tmp_path: Path) -> tuple[TestClient, NodeStore, CatalogRepository]:
    database = Database(f"sqlite:///{tmp_path / 'hub.db'}")
    database.initialize()
    repository = CatalogRepository(database)
    store = NodeStore(database, repository)
    app = FastAPI()
    app.state.node_store = store
    mount_node_api(app)
    return TestClient(app), store, repository


def test_authenticated_sync_applies_exclusions_and_tracks_environment(tmp_path: Path) -> None:
    client, _store, repository = _client(tmp_path)
    provision = client.post(
        "/api/v1/nodes",
        json={
            "node_id": "node-windows",
            "display_name": "Robot laptop",
            "platform": "windows",
            "credential": "correct horse battery staple",
        },
    )
    assert provision.status_code == 201
    assert provision.json()["credential"] == "correct horse battery staple"
    assert "credential_hash" not in provision.text

    body = {
        "registration": {
            "node_id": "node-windows",
            "display_name": "Robot laptop",
            "platform": "windows",
            "capabilities": ["catalog", "native_resume"],
        },
        "environments": [
            {
                "environment_id": "windows-native",
                "kind": "windows",
                "root_path": "C:\\dev",
                "exclude_folders": ["C:\\dev\\secret"],
                "include_transcript_text": False,
            }
        ],
        "conversations": [
            {
                "provider": "codex",
                "provider_thread_id": "visible-thread",
                "environment_id": "windows-native",
                "title": "Visible work",
                "cwd": "C:\\dev\\robot",
                "transcript_text": "must not leave the node",
            },
            {
                "provider": "codex",
                "provider_thread_id": "excluded-thread",
                "environment_id": "windows-native",
                "title": "Secret work",
                "cwd": "C:\\dev\\secret\\project",
            },
        ],
    }
    assert client.post("/api/v1/node/sync", json=body).status_code == 401
    response = client.post(
        "/api/v1/node/sync",
        json=body,
        headers={"Authorization": "Bearer correct horse battery staple"},
    )
    assert response.status_code == 200
    assert response.json() == {"discovered": 2, "imported": 1, "excluded": 1}

    visible_id = stable_conversation_id("codex", "visible-thread", "node-windows", "windows-native")
    assert repository.get(visible_id).transcript_text == ""  # type: ignore[union-attr]
    excluded_id = stable_conversation_id(
        "codex", "excluded-thread", "node-windows", "windows-native"
    )
    assert repository.get(excluded_id) is None

    node = client.get("/api/v1/nodes/node-windows").json()
    assert node["reachable"] is True
    assert node["environments"] == [
        {
            "environment_id": "windows-native",
            "display_name": "windows-native",
            "kind": "windows",
            "root_path": "C:\\dev",
            "available": True,
            "last_seen_at": node["last_seen_at"],
            "metadata": {},
        }
    ]


def test_remote_command_is_fenced_by_node_and_claim_token(tmp_path: Path) -> None:
    client, store, _repository = _client(tmp_path)
    credential = "a sufficiently long node credential"
    client.post(
        "/api/v1/nodes",
        json={
            "node_id": "node-linux",
            "display_name": "Linux workstation",
            "platform": "linux",
            "credential": credential,
        },
    )
    headers = {"Authorization": f"Bearer {credential}"}
    heartbeat = client.post(
        "/api/v1/node/heartbeat",
        json={"node_id": "node-linux", "ttl_seconds": 30},
        headers=headers,
    )
    assert heartbeat.status_code == 200
    queued = store.queue_command(
        node_id="node-linux",
        kind="resume_conversation",
        payload={"provider_thread_id": "thread-1", "workspace": "/work/repo"},
    )

    claim = client.post(
        "/api/v1/node/commands/claim", json={"node_id": "node-linux"}, headers=headers
    )
    command = claim.json()["command"]
    assert command["command_id"] == queued["command_id"]
    assert command["provider_thread_id"] == "thread-1"
    assert command["claim_token"]

    rejected = client.post(
        f"/api/v1/node/commands/{command['command_id']}/result",
        json={
            "node_id": "node-linux",
            "claim_token": "wrong-claim-token-that-is-long",
            "status": "succeeded",
        },
        headers=headers,
    )
    assert rejected.status_code == 401

    completed = client.post(
        f"/api/v1/node/commands/{command['command_id']}/result",
        json={
            "node_id": "node-linux",
            "claim_token": command["claim_token"],
            "status": "succeeded",
            "detail": "opened",
            "output": {"pid": 42},
        },
        headers=headers,
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "succeeded"
    assert completed.json()["result"] == {"detail": "opened", "output": {"pid": 42}}
    assert client.post(
        "/api/v1/node/commands/claim", json={"node_id": "node-linux"}, headers=headers
    ).json() == {"command": None}


def test_environment_identity_is_scoped_to_owning_node(tmp_path: Path) -> None:
    client, _store, repository = _client(tmp_path)
    for node_id in ("node-a", "node-b"):
        credential = f"credential for {node_id} that is long enough"
        client.post(
            "/api/v1/nodes",
            json={
                "node_id": node_id,
                "display_name": node_id,
                "platform": "linux",
                "credential": credential,
            },
        )
        response = client.post(
            "/api/v1/node/sync",
            json={
                "registration": {"node_id": node_id},
                "environments": [{"environment_id": "host"}],
                "conversations": [
                    {
                        "provider_thread_id": f"thread-{node_id}",
                        "environment_id": "host",
                    }
                ],
            },
            headers={"Authorization": f"Bearer {credential}"},
        )
        assert response.status_code == 200

    _rows, total = repository.list(include_hidden=True, selected_only=False)
    assert total == 2
    nodes = client.get("/api/v1/nodes").json()["items"]
    assert {node["node_id"] for node in nodes} == {"node-a", "node-b"}
    assert all(node["environments"][0]["environment_id"] == "host" for node in nodes)


def test_unreachable_node_refuses_command_without_fallback(tmp_path: Path) -> None:
    client, store, _repository = _client(tmp_path)
    client.post(
        "/api/v1/nodes",
        json={
            "node_id": "offline",
            "display_name": "Offline",
            "platform": "linux",
            "credential": "offline node credential is long enough",
        },
    )
    try:
        store.queue_command(node_id="offline", kind="open_path", payload={"path": "/tmp"})
    except ValueError as exc:
        assert str(exc) == "node is unavailable"
    else:
        raise AssertionError("unreachable node accepted a command")

    with store.database.session() as session:
        row = session.get(NodeRow, "offline")
        assert row is not None
        row.heartbeat_expires_at = datetime.now(UTC) + timedelta(seconds=20)
        session.commit()
    assert (
        store.queue_command(node_id="offline", kind="open_path", payload={"path": "/tmp"})["status"]
        == "queued"
    )


def test_authoritative_resume_routes_to_owner_and_refuses_offline_fallback(
    tmp_path: Path,
) -> None:
    settings = Settings(
        state_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'integrated.db'}",
        node_id="hub-local",
        environment_id="host",
    )
    with TestClient(create_app(settings=settings)) as client:
        credential = "remote credential that is sufficiently long"
        client.post(
            "/api/v1/nodes",
            json={
                "node_id": "remote-node",
                "display_name": "Remote",
                "platform": "linux",
                "credential": credential,
            },
        )
        headers = {"Authorization": f"Bearer {credential}"}
        client.post(
            "/api/v1/node/heartbeat",
            json={"node_id": "remote-node", "ttl_seconds": 30},
            headers=headers,
        )
        synced = client.post(
            "/api/v1/node/sync",
            json={
                "registration": {"node_id": "remote-node"},
                "environments": [{"environment_id": "remote-env"}],
                "conversations": [
                    {
                        "provider": "codex",
                        "provider_thread_id": "remote-thread",
                        "environment_id": "remote-env",
                        "cwd": "/remote/work",
                    }
                ],
            },
            headers=headers,
        )
        assert synced.status_code == 200
        candidate = client.get("/api/v1/conversations/candidates").json()["items"][0]
        conversation = client.post(
            "/api/v1/conversations/import",
            json={"conversation_ids": [candidate["conversation_id"]]},
        ).json()["items"][0]
        assert conversation["node_reachable"] is True
        assert conversation["environment_available"] is True

        resumed = client.post(
            f"/api/v1/conversations/{conversation['conversation_id']}/open",
        )
        assert resumed.status_code == 200
        assert resumed.json()["queued"] is True
        claimed = client.post(
            "/api/v1/node/commands/claim",
            json={"node_id": "remote-node"},
            headers=headers,
        ).json()["command"]
        assert claimed["kind"] == "resume_conversation"
        assert claimed["workspace"] == "/remote/work"

        desktop_opened = client.post(
            f"/api/v1/conversations/{conversation['conversation_id']}/open?target=desktop",
        )
        assert desktop_opened.status_code == 200
        assert desktop_opened.json()["queued"] is True
        claimed_desktop = client.post(
            "/api/v1/node/commands/claim",
            json={"node_id": "remote-node"},
            headers=headers,
        ).json()["command"]
        assert claimed_desktop["kind"] == "open_native_url"
        assert claimed_desktop["native_url"] == "codex://threads/remote-thread"

        with client.app.state.database.session() as session:
            row = session.get(NodeRow, "remote-node")
            assert row is not None
            row.heartbeat_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            session.commit()
        unavailable = client.post(
            f"/api/v1/conversations/{conversation['conversation_id']}/open",
        )
        assert unavailable.status_code == 409
        assert "unavailable" in unavailable.json()["detail"]
