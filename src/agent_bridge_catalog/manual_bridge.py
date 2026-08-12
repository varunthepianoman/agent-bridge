from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import func, select

from agent_bridge_bridge.observer import BrokerActivity, BrokerActivityKind, TransportObserver
from agent_bridge_bridge.subjects import control_subject, subject_for, validate_subject
from agent_bridge_protocol.models import (
    BridgeEnvelope,
    EndpointKind,
    EndpointRef,
    ExecutionAttempt,
    ExecutionFailure,
    ExecutionProgress,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    MessageKind,
)

from .db import (
    BridgeExecutionAttemptRow,
    BridgeExecutionRow,
    Database,
    ManualBridgeMessageRow,
)


class PublishAcknowledgement(Protocol):
    stream: str
    sequence: int
    duplicate: bool


class BridgePublisher(Protocol):
    async def publish(
        self, envelope: BridgeEnvelope, *, subject: str | None = None
    ) -> PublishAcknowledgement: ...


class ManualBridgeService:
    """Authoritative Manual-mode persistence around an injectable durable publisher."""

    def __init__(
        self,
        database: Database,
        *,
        publisher: BridgePublisher | None = None,
        observer: TransportObserver | None = None,
        sender: EndpointRef | None = None,
    ) -> None:
        self.database = database
        self.publisher = publisher
        self.observer = observer
        self.sender = sender or EndpointRef(kind=EndpointKind.ENDPOINT, id="catalog-user")

    async def submit_message(
        self, *, envelope_input: dict[str, Any], custom_subject: str | None = None
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        message_id = _id("msg")
        values = dict(envelope_input)
        correlation_id = values.pop("correlation_id", None) or _id("corr")
        envelope = BridgeEnvelope(
            message_id=message_id,
            sender=self.sender,
            correlation_id=correlation_id,
            created_at=now,
            **values,
        )
        subject = validate_subject(custom_subject or subject_for(envelope))
        self._create_message(envelope, subject=subject)
        message = await self._publish(envelope, subject=subject)
        return {"message": message, "envelope": envelope.model_dump(mode="json")}

    async def submit_request(
        self,
        *,
        request_input: dict[str, Any],
        envelope_options: dict[str, Any] | None = None,
        custom_subject: str | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        execution_id = _id("exec")
        message_id = _id("msg")
        correlation_id = _id("corr")
        request = ExecutionRequest(
            execution_id=execution_id,
            requested_at=now,
            requested_by=self.sender,
            **request_input,
        )
        options = envelope_options or {}
        envelope = BridgeEnvelope(
            message_id=message_id,
            kind=MessageKind.REQUEST,
            sender=self.sender,
            destination=request.target,
            body=request.model_dump(mode="json"),
            correlation_id=correlation_id,
            reply_to=options.get("reply_to"),
            work_id=request.work_id,
            created_at=now,
            delivery=request.delivery,
            artifacts=request.artifacts,
            extensions=options.get("extensions", {}),
        )
        subject = validate_subject(custom_subject or subject_for(envelope))
        self._create_execution(request, envelope=envelope, subject=subject)
        message = await self._publish(envelope, subject=subject)
        if message["status"] == "publish_failed":
            self._mark_initial_attempt_failed(execution_id, message["error"])
        return {
            "execution": self.get_execution(execution_id),
            "message": message,
            "envelope": envelope.model_dump(mode="json"),
        }

    async def cancel_execution(self, execution_id: str, *, reason: str) -> dict[str, Any]:
        execution = self.get_execution(execution_id)
        if execution is None:
            raise LookupError("execution not found")
        if execution["status"] in {"succeeded", "failed", "cancelled", "expired"}:
            raise ValueError(f"cannot cancel terminal execution in {execution['status']} status")
        now = datetime.now(UTC)
        message_id = _id("msg")
        envelope = BridgeEnvelope(
            message_id=message_id,
            kind=MessageKind.CONTROL,
            sender=self.sender,
            destination=EndpointRef(kind=execution["target"]["kind"], id=execution["target"]["id"]),
            body={"operation": "cancel", "execution_id": execution_id, "reason": reason},
            correlation_id=execution["correlation_id"],
            causation_id=execution["request_message_id"],
            work_id=execution["work_id"],
            created_at=now,
        )
        subject = control_subject(envelope.destination)
        self._create_message(envelope, subject=subject, execution_id=execution_id)
        message = await self._publish(envelope, subject=subject)
        if message["status"] == "published":
            self._mark_cancelled(execution_id, message_id=message_id, now=now)
        return {"execution": self.get_execution(execution_id), "message": message}

    def list_messages(
        self,
        *,
        status: str | None = None,
        work_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        filters = []
        if status:
            filters.append(ManualBridgeMessageRow.status == status)
        if work_id:
            filters.append(ManualBridgeMessageRow.work_id == work_id)
        with self.database.session() as session:
            total = (
                session.scalar(
                    select(func.count()).select_from(ManualBridgeMessageRow).where(*filters)
                )
                or 0
            )
            rows = session.scalars(
                select(ManualBridgeMessageRow)
                .where(*filters)
                .order_by(ManualBridgeMessageRow.created_at.desc())
                .limit(limit)
                .offset(offset)
            ).all()
            return [_message_dict(row) for row in rows], int(total)

    def get_message(self, message_id: str) -> dict[str, Any] | None:
        with self.database.session() as session:
            row = session.get(ManualBridgeMessageRow, message_id)
            return _message_dict(row) if row else None

    def list_executions(
        self,
        *,
        status: str | None = None,
        work_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        filters = []
        if status:
            filters.append(BridgeExecutionRow.status == status)
        if work_id:
            filters.append(BridgeExecutionRow.work_id == work_id)
        with self.database.session() as session:
            total = (
                session.scalar(select(func.count()).select_from(BridgeExecutionRow).where(*filters))
                or 0
            )
            rows = session.scalars(
                select(BridgeExecutionRow)
                .where(*filters)
                .order_by(BridgeExecutionRow.requested_at.desc())
                .limit(limit)
                .offset(offset)
            ).all()
            return [self._execution_dict(session, row) for row in rows], int(total)

    def get_execution(self, execution_id: str) -> dict[str, Any] | None:
        with self.database.session() as session:
            row = session.get(BridgeExecutionRow, execution_id)
            return self._execution_dict(session, row) if row else None

    def record_attempt(self, attempt: ExecutionAttempt) -> dict[str, Any]:
        now = datetime.now(UTC)
        with self.database.session() as session:
            if session.get(BridgeExecutionRow, attempt.execution_id) is None:
                raise LookupError("execution not found")
            row = session.get(BridgeExecutionAttemptRow, attempt.attempt_id)
            if row is None:
                row = BridgeExecutionAttemptRow(
                    attempt_id=attempt.attempt_id,
                    execution_id=attempt.execution_id,
                    attempt_number=attempt.attempt_number,
                    created_at=now,
                    progress_json="[]",
                    updated_at=now,
                )
                session.add(row)
            row.node_id = attempt.node_id
            row.status = str(attempt.status)
            row.started_at = attempt.started_at
            row.finished_at = attempt.finished_at
            row.error = attempt.error
            row.updated_at = now
            execution = session.get(BridgeExecutionRow, attempt.execution_id)
            assert execution is not None
            execution.status = str(attempt.status)
            execution.error = attempt.error
            execution.updated_at = now
            if str(attempt.status) in {"succeeded", "failed", "cancelled", "expired"}:
                execution.completed_at = attempt.finished_at or now
            session.commit()
            return _attempt_dict(row)

    def ingest_progress(self, progress: ExecutionProgress) -> dict[str, Any]:
        """Idempotently advance central state from a durable result-stream event."""

        now = progress.occurred_at
        with self.database.session() as session:
            execution = session.get(BridgeExecutionRow, progress.execution_id)
            if execution is None:
                raise LookupError("execution not found")
            attempt = session.get(BridgeExecutionAttemptRow, progress.attempt_id)
            if attempt is None:
                attempt = self._new_observed_attempt(
                    session, progress.execution_id, progress.attempt_id, now
                )
            events = json.loads(attempt.progress_json)
            by_sequence = {
                int(item["sequence"]): item
                for item in events
                if isinstance(item, dict) and isinstance(item.get("sequence"), int)
            }
            by_sequence[progress.sequence] = progress.model_dump(mode="json")
            attempt.progress_json = json.dumps(
                [by_sequence[key] for key in sorted(by_sequence)], sort_keys=True
            )
            attempt.started_at = attempt.started_at or now
            attempt.updated_at = now
            terminal = {
                "succeeded",
                "blocked",
                "failed",
                "cancelled",
                "expired",
                "dead_lettered",
            }
            if attempt.status not in terminal:
                attempt.status = str(ExecutionStatus.RUNNING)
            if execution.status not in terminal:
                execution.status = str(ExecutionStatus.RUNNING)
            execution.updated_at = now
            session.commit()
            return _attempt_dict(attempt)

    def ingest_result(self, result: ExecutionResult) -> dict[str, Any]:
        return self._ingest_outcome(result)

    def ingest_failure(self, failure: ExecutionFailure) -> dict[str, Any]:
        return self._ingest_outcome(failure)

    def ingest_result_envelope(self, envelope: BridgeEnvelope) -> dict[str, Any]:
        if envelope.kind == MessageKind.EVENT:
            return self.ingest_progress(ExecutionProgress.model_validate(envelope.body))
        if envelope.kind != MessageKind.RESPONSE:
            raise ValueError("result stream envelope must be an event or response")
        status = envelope.body.get("status")
        if status in {"succeeded", "blocked"}:
            return self.ingest_result(ExecutionResult.model_validate(envelope.body))
        return self.ingest_failure(ExecutionFailure.model_validate(envelope.body))

    async def _publish(self, envelope: BridgeEnvelope, *, subject: str) -> dict[str, Any]:
        try:
            if self.publisher is None:
                raise RuntimeError("Bridge publisher is not configured")
            acknowledgement = await self.publisher.publish(envelope, subject=subject)
        except Exception as exc:
            return self._update_message_publish(
                envelope.message_id, status="publish_failed", error=str(exc)
            )
        now = datetime.now(UTC)
        message = self._update_message_publish(
            envelope.message_id,
            status="published",
            stream=acknowledgement.stream,
            stream_sequence=acknowledgement.sequence,
            duplicate=acknowledgement.duplicate,
            published_at=now,
        )
        if self.observer is not None:
            await self.observer.record(
                BrokerActivity(
                    kind=BrokerActivityKind.PUBLISHED,
                    subject=subject,
                    message_id=envelope.message_id,
                    correlation_id=envelope.correlation_id,
                    stream=acknowledgement.stream,
                    stream_sequence=acknowledgement.sequence,
                    occurred_at=now,
                    detail={
                        "duplicate": acknowledgement.duplicate,
                        "message_type": str(envelope.kind),
                        "source_kind": str(envelope.sender.kind),
                        "source_id": envelope.sender.id,
                        "destination_kind": str(envelope.destination.kind),
                        "destination_id": envelope.destination.id,
                        "work_id": envelope.work_id,
                        "expires_at": (
                            envelope.delivery.expires_at.isoformat()
                            if envelope.delivery.expires_at
                            else None
                        ),
                        "encoded_size": len(envelope.model_dump_json().encode()),
                    },
                )
            )
        return message

    def _create_message(
        self,
        envelope: BridgeEnvelope,
        *,
        subject: str,
        execution_id: str | None = None,
    ) -> None:
        row = ManualBridgeMessageRow(
            message_id=envelope.message_id,
            correlation_id=envelope.correlation_id or envelope.message_id,
            kind=str(envelope.kind),
            destination_kind=str(envelope.destination.kind),
            destination_id=envelope.destination.id,
            work_id=envelope.work_id,
            execution_id=execution_id,
            subject=subject,
            envelope_json=envelope.model_dump_json(),
            status="queued",
            created_at=envelope.created_at,
            updated_at=envelope.created_at,
        )
        with self.database.session() as session:
            session.add(row)
            session.commit()

    def _create_execution(
        self, request: ExecutionRequest, *, envelope: BridgeEnvelope, subject: str
    ) -> None:
        message = ManualBridgeMessageRow(
            message_id=envelope.message_id,
            correlation_id=envelope.correlation_id or envelope.message_id,
            kind=str(envelope.kind),
            destination_kind=str(envelope.destination.kind),
            destination_id=envelope.destination.id,
            work_id=envelope.work_id,
            execution_id=request.execution_id,
            subject=subject,
            envelope_json=envelope.model_dump_json(),
            status="queued",
            created_at=envelope.created_at,
            updated_at=envelope.created_at,
        )
        execution = BridgeExecutionRow(
            execution_id=request.execution_id,
            request_message_id=envelope.message_id,
            operation=str(request.operation),
            target_kind=str(request.target.kind),
            target_id=request.target.id,
            work_id=request.work_id,
            conversation_id=request.conversation_id,
            adapter=request.adapter,
            instruction=request.instruction,
            request_json=request.model_dump_json(),
            status=str(ExecutionStatus.QUEUED),
            requested_at=request.requested_at,
            updated_at=request.requested_at,
        )
        attempt = BridgeExecutionAttemptRow(
            attempt_id=f"{request.execution_id}-attempt-1",
            execution_id=request.execution_id,
            attempt_number=1,
            status=str(ExecutionStatus.QUEUED),
            progress_json="[]",
            created_at=request.requested_at,
            updated_at=request.requested_at,
        )
        with self.database.session() as session:
            session.add(message)
            session.flush()
            session.add(execution)
            session.flush()
            session.add(attempt)
            session.commit()

    def _ingest_outcome(self, outcome: ExecutionResult | ExecutionFailure) -> dict[str, Any]:
        finished_at = (
            outcome.completed_at if isinstance(outcome, ExecutionResult) else outcome.failed_at
        )
        with self.database.session() as session:
            execution = session.get(BridgeExecutionRow, outcome.execution_id)
            if execution is None:
                raise LookupError("execution not found")
            attempt = session.get(BridgeExecutionAttemptRow, outcome.attempt_id)
            if attempt is None:
                attempt = self._new_observed_attempt(
                    session, outcome.execution_id, outcome.attempt_id, finished_at
                )
            payload = outcome.model_dump(mode="json")
            if outcome.node_id is not None:
                attempt.node_id = outcome.node_id
            attempt.status = str(outcome.status)
            attempt.result_json = json.dumps(payload, sort_keys=True)
            attempt.error = outcome.message if isinstance(outcome, ExecutionFailure) else None
            attempt.finished_at = finished_at
            attempt.updated_at = finished_at
            execution.status = str(outcome.status)
            execution.result_json = json.dumps(payload, sort_keys=True)
            execution.error = outcome.message if isinstance(outcome, ExecutionFailure) else None
            execution.completed_at = finished_at
            execution.updated_at = finished_at
            if str(outcome.status) == "cancelled":
                execution.cancelled_at = finished_at
            session.commit()
            return self._execution_dict(session, execution)

    @staticmethod
    def _new_observed_attempt(
        session: Any, execution_id: str, attempt_id: str, observed_at: datetime
    ) -> BridgeExecutionAttemptRow:
        number = (
            int(
                session.scalar(
                    select(func.max(BridgeExecutionAttemptRow.attempt_number)).where(
                        BridgeExecutionAttemptRow.execution_id == execution_id
                    )
                )
                or 0
            )
            + 1
        )
        attempt = BridgeExecutionAttemptRow(
            attempt_id=attempt_id,
            execution_id=execution_id,
            attempt_number=number,
            status=str(ExecutionStatus.RUNNING),
            progress_json="[]",
            created_at=observed_at,
            updated_at=observed_at,
        )
        session.add(attempt)
        return attempt

    def _update_message_publish(
        self,
        message_id: str,
        *,
        status: str,
        error: str | None = None,
        stream: str | None = None,
        stream_sequence: int | None = None,
        duplicate: bool | None = None,
        published_at: datetime | None = None,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            row = session.get(ManualBridgeMessageRow, message_id)
            if row is None:
                raise LookupError("message not found")
            row.status = status
            row.error = error
            row.stream = stream
            row.stream_sequence = stream_sequence
            row.duplicate = duplicate
            row.published_at = published_at
            row.updated_at = published_at or datetime.now(UTC)
            session.commit()
            return _message_dict(row)

    def _mark_initial_attempt_failed(self, execution_id: str, error: str | None) -> None:
        now = datetime.now(UTC)
        with self.database.session() as session:
            execution = session.get(BridgeExecutionRow, execution_id)
            assert execution is not None
            attempt = session.scalar(
                select(BridgeExecutionAttemptRow).where(
                    BridgeExecutionAttemptRow.execution_id == execution_id,
                    BridgeExecutionAttemptRow.attempt_number == 1,
                )
            )
            assert attempt is not None
            execution.status = str(ExecutionStatus.FAILED)
            execution.error = error
            execution.completed_at = now
            execution.updated_at = now
            attempt.status = str(ExecutionStatus.FAILED)
            attempt.error = error
            attempt.finished_at = now
            attempt.updated_at = now
            session.commit()

    def _mark_cancelled(self, execution_id: str, *, message_id: str, now: datetime) -> None:
        with self.database.session() as session:
            execution = session.get(BridgeExecutionRow, execution_id)
            assert execution is not None
            execution.cancellation_message_id = message_id
            execution.status = str(ExecutionStatus.CANCELLED)
            execution.cancelled_at = now
            execution.completed_at = now
            execution.updated_at = now
            session.commit()

    def _execution_dict(self, session: Any, row: BridgeExecutionRow) -> dict[str, Any]:
        request = json.loads(row.request_json)
        message = session.get(ManualBridgeMessageRow, row.request_message_id)
        attempts = session.scalars(
            select(BridgeExecutionAttemptRow)
            .where(BridgeExecutionAttemptRow.execution_id == row.execution_id)
            .order_by(BridgeExecutionAttemptRow.attempt_number)
        ).all()
        return {
            "execution_id": row.execution_id,
            "request_message_id": row.request_message_id,
            "cancellation_message_id": row.cancellation_message_id,
            "correlation_id": message.correlation_id if message else None,
            "operation": row.operation,
            "target": {"kind": row.target_kind, "id": row.target_id},
            "work_id": row.work_id,
            "conversation_id": row.conversation_id,
            "adapter": row.adapter,
            "instruction": row.instruction,
            "request": request,
            "status": row.status,
            "result": json.loads(row.result_json) if row.result_json else None,
            "error": row.error,
            "requested_at": _iso(row.requested_at),
            "updated_at": _iso(row.updated_at),
            "completed_at": _iso(row.completed_at),
            "cancelled_at": _iso(row.cancelled_at),
            "attempts": [_attempt_dict(item) for item in attempts],
        }


def _message_dict(row: ManualBridgeMessageRow) -> dict[str, Any]:
    return {
        "message_id": row.message_id,
        "correlation_id": row.correlation_id,
        "kind": row.kind,
        "destination": {"kind": row.destination_kind, "id": row.destination_id},
        "work_id": row.work_id,
        "execution_id": row.execution_id,
        "subject": row.subject,
        "envelope": json.loads(row.envelope_json),
        "status": row.status,
        "error": row.error,
        "stream": row.stream,
        "stream_sequence": row.stream_sequence,
        "duplicate": row.duplicate,
        "created_at": _iso(row.created_at),
        "published_at": _iso(row.published_at),
        "updated_at": _iso(row.updated_at),
    }


def _attempt_dict(row: BridgeExecutionAttemptRow) -> dict[str, Any]:
    return {
        "attempt_id": row.attempt_id,
        "execution_id": row.execution_id,
        "attempt_number": row.attempt_number,
        "node_id": row.node_id,
        "status": row.status,
        "progress": json.loads(row.progress_json),
        "result": json.loads(row.result_json) if row.result_json else None,
        "error": row.error,
        "created_at": _iso(row.created_at),
        "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at),
        "updated_at": _iso(row.updated_at),
    }


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()
