from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_bridge_catalog.app import create_app
from agent_bridge_catalog.broker_projection import BrokerProjectionStore
from agent_bridge_catalog.config import Settings
from agent_bridge_catalog.db import Database


@pytest.fixture
def projection(tmp_path: Path) -> BrokerProjectionStore:
    database = Database(f"sqlite:///{tmp_path / 'catalog.db'}")
    database.initialize()
    return BrokerProjectionStore(database)


def test_projection_materialization_is_idempotent_and_monotonic(
    projection: BrokerProjectionStore,
) -> None:
    now = datetime(2026, 8, 11, 10, tzinfo=UTC)
    projection.materialize_message(
        message_id="msg-1",
        subject="bridge.inbox.node.robot",
        message_type="request",
        state="published",
        stream="BRIDGE_WORK",
        stream_sequence=12,
        correlation_id="corr-1",
        work_id="work-robot",
        payload_summary={"operation": "run-tests"},
        observed_at=now,
    )
    projection.materialize_message(
        message_id="msg-1",
        subject="stale.subject",
        message_type="request",
        state="created",
        observed_at=now - timedelta(seconds=1),
    )
    projection.materialize_delivery(
        delivery_id="delivery-1",
        message_id="msg-1",
        stream="BRIDGE_WORK",
        consumer="robot-runner",
        delivery_sequence=4,
        state="delivered",
        delivered_at=now,
        ack_deadline_at=now + timedelta(seconds=30),
        observed_at=now,
    )
    projection.materialize_delivery(
        delivery_id="delivery-1",
        message_id="msg-1",
        stream="BRIDGE_WORK",
        consumer="robot-runner",
        delivery_sequence=4,
        state="acknowledged",
        delivered_at=now,
        acknowledged_at=now + timedelta(seconds=2),
        observed_at=now + timedelta(seconds=2),
    )

    item = projection.get_message("msg-1")
    assert item is not None
    assert item["subject"] == "bridge.inbox.node.robot"
    assert item["stream_sequence"] == 12
    assert item["deliveries"][0]["state"] == "acknowledged"
    assert projection.list_messages(correlation_id="corr-1")[1] == 1
    assert projection.list_deliveries(state="acknowledged")[1] == 1


def test_dead_letters_consumer_snapshots_and_summary(
    projection: BrokerProjectionStore,
) -> None:
    now = datetime(2026, 8, 11, 11, tzinfo=UTC)
    projection.materialize_message(
        message_id="msg-dead",
        subject="bridge.capability.test",
        message_type="request",
        state="dead_lettered",
        observed_at=now,
    )
    projection.materialize_dead_letter(
        dead_letter_id="dead-1",
        message_id="msg-dead",
        stream="BRIDGE_WORK",
        consumer="test-runner",
        reason="max_deliveries",
        attempts=5,
        detail={"last_error": "node offline"},
        dead_lettered_at=now,
        observed_at=now,
    )
    projection.materialize_consumer_state(
        stream="BRIDGE_WORK",
        consumer="test-runner",
        state="active",
        pending_count=7,
        ack_pending_count=2,
        delivered_stream_sequence=20,
        ack_floor_stream_sequence=11,
        observed_at=now,
    )
    projection.materialize_consumer_state(
        stream="BRIDGE_WORK",
        consumer="test-runner",
        state="stale",
        pending_count=99,
        observed_at=now - timedelta(seconds=1),
    )

    dead_letters, total = projection.list_dead_letters()
    assert total == 1
    assert dead_letters[0]["detail"] == {"last_error": "node offline"}
    assert projection.list_consumers()[0]["pending_count"] == 7
    assert projection.summary() == {
        "messages": 1,
        "deliveries": 0,
        "pending_deliveries": 0,
        "unresolved_dead_letters": 1,
        "consumers": 1,
        "consumer_pending": 7,
    }


def test_legacy_projection_query_api_is_removed(tmp_path: Path) -> None:
    settings = Settings(
        state_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'api.db'}",
        node_id="hub",
        environment_id="test",
        codex_bin="codex",
    )
    app = create_app(settings=settings)
    with TestClient(app) as client:
        assert client.get("/api/v1/bridge/operations/messages").status_code == 404
        assert app.state.broker_projection.summary()["messages"] == 0
