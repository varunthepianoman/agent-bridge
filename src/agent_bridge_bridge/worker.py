"""JetStream execution worker with durable settlement ordering."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from agent_bridge_protocol.models import (
    BridgeEnvelope,
    EndpointKind,
    EndpointRef,
    ExecutionFailure,
    ExecutionLease,
    ExecutionRequest,
    ExecutionResult,
    MessageKind,
)

from .execution_store import LeaseBusyError, Outcome, SQLiteExecutionStore, StaleLeaseError
from .idempotency import ClaimResult
from .runners import (
    CancellationToken,
    ExecutionCancelled,
    ExecutionDispatcher,
    RetryableRunnerError,
    RunnerError,
)
from .subjects import result_subject
from .transport import BridgeDelivery, BridgeSubscription, JetStreamTransport

LOGGER = logging.getLogger(__name__)


class CancellationControl(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["cancel"]
    execution_id: str
    reason: str = "cancelled by user"


@dataclass(frozen=True)
class WorkerSettings:
    worker_id: str
    node_id: str
    lease_seconds: float = 60.0
    lease_renewal_seconds: float = 20.0
    retry_backoff_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.lease_seconds <= 0 or self.lease_renewal_seconds <= 0:
            raise ValueError("lease and renewal intervals must be positive")
        if self.lease_renewal_seconds >= self.lease_seconds:
            raise ValueError("lease renewal interval must be shorter than the lease")


class ExecutionWorker:
    def __init__(
        self,
        *,
        settings: WorkerSettings,
        transport: JetStreamTransport,
        store: SQLiteExecutionStore,
        dispatcher: ExecutionDispatcher,
    ) -> None:
        self.settings = settings
        self.transport = transport
        self.store = store
        self.dispatcher = dispatcher

    async def run_once(self, subscription: BridgeSubscription, *, timeout: float = 1.0) -> bool:
        deliveries = await subscription.fetch(batch=1, timeout=timeout)
        if not deliveries:
            return False
        await self.process(deliveries[0])
        return True

    async def process(self, delivery: BridgeDelivery) -> None:
        try:
            request = ExecutionRequest.model_validate(delivery.envelope.body)
        except (ValidationError, ValueError):
            await delivery.dead_letter(reason="invalid_execution_request")
            return
        envelope = delivery.envelope
        claim = await self.store.claim(
            envelope.message_id,
            owner=self.settings.worker_id,
            ttl_seconds=self.settings.lease_seconds,
        )
        if claim == ClaimResult.COMPLETED:
            outcome = await self.store.outcome(request.execution_id)
            if outcome is None:
                await delivery.dead_letter(reason="completed_claim_missing_outcome")
                return
            await self._publish_outcome(envelope, request, outcome)
            await delivery.ack()
            return
        if claim == ClaimResult.IN_PROGRESS:
            await delivery.nak(
                delay_seconds=self.settings.retry_backoff_seconds,
                reason="execution_already_claimed",
            )
            return

        try:
            lease = await self.store.acquire_lease(
                request.execution_id,
                holder_id=self.settings.worker_id,
                ttl_seconds=self.settings.lease_seconds,
            )
        except LeaseBusyError:
            await self.store.release(envelope.message_id, owner=self.settings.worker_id)
            await delivery.nak(
                delay_seconds=self.settings.retry_backoff_seconds,
                reason="execution_lease_busy",
            )
            return

        attempt = await self.store.start_attempt(
            request.execution_id, node_id=self.settings.node_id
        )
        cancellation = CancellationToken(self.store, request.execution_id)
        maintenance = asyncio.create_task(
            self._maintain_lease(delivery, envelope.message_id, lease)
        )
        try:
            expiration = envelope.delivery.expires_at or request.delivery.expires_at
            if expiration is not None:
                if expiration.tzinfo is None:
                    expiration = expiration.replace(tzinfo=UTC)
                if expiration <= datetime.now(UTC):
                    failure = self._failure(
                        request,
                        attempt.attempt_id,
                        status="expired",
                        code="request_expired",
                        message="Execution request expired before it was started",
                    )
                    await self._finish_publish_settle(
                        delivery, envelope, request, failure, dead_letter_reason="expired"
                    )
                    return
            await cancellation.raise_if_cancelled()

            async def progress(summary: str, percent: float | None) -> None:
                event = await self.store.append_progress(attempt, summary=summary, percent=percent)
                progress_envelope = self._response_envelope(
                    envelope,
                    request,
                    message_id=f"progress-{attempt.attempt_id}-{event.sequence}",
                    kind=MessageKind.EVENT,
                    body=event.model_dump(mode="json"),
                )
                try:
                    await self.transport.publish(
                        progress_envelope, subject=result_subject(request.execution_id)
                    )
                except Exception:
                    LOGGER.exception(
                        "Could not publish execution progress",
                        extra={"execution_id": request.execution_id},
                    )

            runner_task = asyncio.create_task(self.dispatcher.run(request, cancellation, progress))
            done, _ = await asyncio.wait(
                {runner_task, maintenance}, return_when=asyncio.FIRST_COMPLETED
            )
            if maintenance in done:
                runner_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await runner_task
                maintenance.result()
                raise RuntimeError("lease maintenance ended unexpectedly")
            output = runner_task.result()
            result = ExecutionResult(
                execution_id=request.execution_id,
                attempt_id=attempt.attempt_id,
                node_id=self.settings.node_id,
                status=output.workflow_status,
                summary=output.summary,
                output=output.output,
            )
            await self._finish_publish_settle(delivery, envelope, request, result)
        except ExecutionCancelled as error:
            failure = self._failure(
                request,
                attempt.attempt_id,
                status="cancelled",
                code="cancelled",
                message=str(error),
            )
            await self._finish_publish_settle(delivery, envelope, request, failure)
        except RetryableRunnerError as error:
            if delivery.delivery_count >= envelope.delivery.max_attempts:
                failure = self._failure(
                    request,
                    attempt.attempt_id,
                    status="dead_lettered",
                    code="attempts_exhausted",
                    message=str(error),
                )
                await self._finish_publish_settle(
                    delivery,
                    envelope,
                    request,
                    failure,
                    dead_letter_reason="attempts_exhausted",
                )
            else:
                await self.store.record_retryable_failure(attempt, error=str(error))
                await self.store.release(envelope.message_id, owner=self.settings.worker_id)
                await delivery.nak(
                    delay_seconds=self.settings.retry_backoff_seconds,
                    reason="runner_retryable_failure",
                )
        except RunnerError as error:
            failure = self._failure(
                request,
                attempt.attempt_id,
                status="failed",
                code="runner_error",
                message=str(error),
            )
            await self._finish_publish_settle(delivery, envelope, request, failure)
        except Exception as error:
            # A completed local outcome means only result publication/settlement failed.
            # Leave the source unacknowledged so redelivery republishes without rerunning.
            if await self.store.outcome(request.execution_id) is not None:
                raise
            if delivery.delivery_count >= envelope.delivery.max_attempts:
                failure = self._failure(
                    request,
                    attempt.attempt_id,
                    status="dead_lettered",
                    code="unexpected_attempts_exhausted",
                    message=str(error),
                )
                await self._finish_publish_settle(
                    delivery,
                    envelope,
                    request,
                    failure,
                    dead_letter_reason="attempts_exhausted",
                )
            else:
                await self.store.record_retryable_failure(attempt, error=str(error))
                await self.store.release(envelope.message_id, owner=self.settings.worker_id)
                await delivery.nak(
                    delay_seconds=self.settings.retry_backoff_seconds,
                    reason="unexpected_runner_failure",
                )
        finally:
            maintenance.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await maintenance
            try:
                await self.store.release_lease(lease)
            except StaleLeaseError:
                LOGGER.warning(
                    "Execution lease expired before release",
                    extra={"execution_id": request.execution_id},
                )

    async def _maintain_lease(
        self,
        delivery: BridgeDelivery,
        message_id: str,
        lease: ExecutionLease,
    ) -> None:
        current = lease
        while True:
            await asyncio.sleep(self.settings.lease_renewal_seconds)
            current = await self.store.renew_lease(current, ttl_seconds=self.settings.lease_seconds)
            claim = await self.store.claim(
                message_id,
                owner=self.settings.worker_id,
                ttl_seconds=self.settings.lease_seconds,
            )
            if claim == ClaimResult.COMPLETED or delivery.settled:
                return
            try:
                await delivery.in_progress()
            except RuntimeError:
                if delivery.settled:
                    return
                raise

    async def _finish_publish_settle(
        self,
        delivery: BridgeDelivery,
        envelope: BridgeEnvelope,
        request: ExecutionRequest,
        outcome: Outcome,
        *,
        dead_letter_reason: str | None = None,
    ) -> None:
        # The durable local outcome makes result publication replayable after a crash.
        await self.store.finish(
            outcome,
            message_id=envelope.message_id,
            claim_owner=self.settings.worker_id,
        )
        # This durable publish must succeed before the input can be acknowledged.
        await self._publish_outcome(envelope, request, outcome)
        await self.store.mark_result_published(request.execution_id)
        if dead_letter_reason is None:
            await delivery.ack()
        else:
            await delivery.dead_letter(reason=dead_letter_reason)

    async def _publish_outcome(
        self,
        envelope: BridgeEnvelope,
        request: ExecutionRequest,
        outcome: Outcome,
    ) -> None:
        response = self._response_envelope(
            envelope,
            request,
            message_id=f"result-{request.execution_id}",
            kind=MessageKind.RESPONSE,
            body=outcome.model_dump(mode="json"),
        )
        await self.transport.publish(response, subject=result_subject(request.execution_id))

    def _response_envelope(
        self,
        source: BridgeEnvelope,
        request: ExecutionRequest,
        *,
        message_id: str,
        kind: MessageKind,
        body: dict[str, object],
    ) -> BridgeEnvelope:
        destination = source.reply_to or request.requested_by or source.sender
        return BridgeEnvelope(
            message_id=message_id,
            kind=kind,
            sender=EndpointRef(kind=EndpointKind.NODE, id=self.settings.node_id),
            destination=destination,
            body=body,
            correlation_id=source.correlation_id or request.execution_id,
            causation_id=source.message_id,
            work_id=request.work_id or source.work_id,
        )

    def _failure(
        self,
        request: ExecutionRequest,
        attempt_id: str,
        *,
        status: Literal["failed", "cancelled", "expired", "dead_lettered"],
        code: str,
        message: str,
    ) -> ExecutionFailure:
        return ExecutionFailure(
            execution_id=request.execution_id,
            attempt_id=attempt_id,
            node_id=self.settings.node_id,
            status=status,
            code=code,
            message=message,
            retryable=False,
        )


class ControlWorker:
    """Consumes node control traffic independently from execution requests."""

    def __init__(self, store: SQLiteExecutionStore) -> None:
        self.store = store

    async def run_once(self, subscription: BridgeSubscription, *, timeout: float = 1.0) -> bool:
        deliveries = await subscription.fetch(batch=1, timeout=timeout)
        if not deliveries:
            return False
        await self.process(deliveries[0])
        return True

    async def process(self, delivery: BridgeDelivery) -> None:
        try:
            envelope = delivery.envelope
            if envelope.kind != MessageKind.CONTROL:
                raise ValueError("not a control envelope")
            control = CancellationControl.model_validate(envelope.body)
        except (ValidationError, ValueError):
            await delivery.dead_letter(reason="invalid_control_request")
            return
        # The upsert is idempotent and durable; ACK is always later.
        await self.store.request_cancellation(
            control.execution_id,
            reason=control.reason,
        )
        await delivery.ack()
