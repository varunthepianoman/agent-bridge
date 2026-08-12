from __future__ import annotations

from pathlib import Path

import pytest

from agent_bridge_bridge.execution_store import (
    LeaseBusyError,
    SQLiteExecutionStore,
    StaleLeaseError,
)
from agent_bridge_bridge.idempotency import ClaimResult
from agent_bridge_protocol.models import ExecutionResult


async def test_store_persists_completed_idempotency_and_outcome(tmp_path: Path) -> None:
    path = tmp_path / "runner.sqlite3"
    store = SQLiteExecutionStore(path)
    assert await store.claim("msg-1", owner="worker-a", ttl_seconds=10) == ClaimResult.CLAIMED
    attempt = await store.start_attempt("exec-1", node_id="node-a")
    progress = await store.append_progress(attempt, summary="started", percent=1)
    assert progress.sequence == 0
    result = ExecutionResult(
        execution_id="exec-1",
        attempt_id=attempt.attempt_id,
        summary="done",
    )
    await store.finish(result, message_id="msg-1", claim_owner="worker-a")
    store.close()

    reopened = SQLiteExecutionStore(path)
    assert await reopened.claim("msg-1", owner="worker-b", ttl_seconds=10) == ClaimResult.COMPLETED
    assert await reopened.outcome("exec-1") == result
    attempts = await reopened.attempts("exec-1")
    assert len(attempts) == 1
    assert attempts[0].status == "succeeded"
    reopened.close()


async def test_lease_fencing_and_cancellation_are_durable(tmp_path: Path) -> None:
    store = SQLiteExecutionStore(tmp_path / "runner.sqlite3")
    lease = await store.acquire_lease("exec-1", holder_id="worker-a", ttl_seconds=30)
    with pytest.raises(LeaseBusyError):
        await store.acquire_lease("exec-1", holder_id="worker-b", ttl_seconds=30)
    renewed = await store.renew_lease(lease, ttl_seconds=30)
    assert renewed.fencing_token == lease.fencing_token
    await store.request_cancellation("exec-1", reason="user requested stop")
    assert await store.cancellation_reason("exec-1") == "user requested stop"
    await store.release_lease(renewed)
    with pytest.raises(StaleLeaseError):
        await store.renew_lease(renewed, ttl_seconds=30)
    store.close()
