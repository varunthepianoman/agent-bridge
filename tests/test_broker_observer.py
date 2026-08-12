from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from agent_bridge_bridge.observer import BrokerActivity, BrokerActivityKind
from agent_bridge_catalog.broker_observer import BrokerProjectionObserver
from agent_bridge_catalog.broker_projection import BrokerProjectionStore
from agent_bridge_catalog.db import Database


async def test_transport_activity_materializes_queryable_projection(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'projection-observer.db'}")
    database.initialize()
    store = BrokerProjectionStore(database)
    observer = BrokerProjectionObserver(store)
    now = datetime.now(UTC)
    await observer.record(
        BrokerActivity(
            kind=BrokerActivityKind.PUBLISHED,
            subject="bridge.v1.inbox.node.node-a",
            message_id="message-observed",
            correlation_id="correlation-observed",
            stream="BRIDGE_WORK_V1",
            stream_sequence=7,
            occurred_at=now,
            detail={
                "message_type": "request",
                "source_kind": "role",
                "source_id": "role-a",
                "destination_kind": "node",
                "destination_id": "node-a",
                "work_id": "work-a",
                "encoded_size": 321,
            },
        )
    )
    await observer.record(
        BrokerActivity(
            kind=BrokerActivityKind.DELIVERED,
            subject="bridge.v1.inbox.node.node-a",
            message_id="message-observed",
            correlation_id="correlation-observed",
            stream="BRIDGE_WORK_V1",
            stream_sequence=7,
            consumer="node-a-runner",
            consumer_sequence=3,
            delivery_count=1,
            occurred_at=now,
        )
    )
    await observer.record(
        BrokerActivity(
            kind=BrokerActivityKind.ACKNOWLEDGED,
            subject="bridge.v1.inbox.node.node-a",
            message_id="message-observed",
            correlation_id="correlation-observed",
            stream="BRIDGE_WORK_V1",
            stream_sequence=7,
            consumer="node-a-runner",
            consumer_sequence=3,
            delivery_count=1,
            occurred_at=now,
        )
    )

    message = store.get_message("message-observed")
    assert message is not None
    assert message["state"] == "acknowledged"
    assert message["destination_id"] == "node-a"
    assert message["size_bytes"] == 321
    assert len(message["deliveries"]) == 1
    assert message["deliveries"][0]["state"] == "acknowledged"
    assert store.summary()["messages"] == 1
