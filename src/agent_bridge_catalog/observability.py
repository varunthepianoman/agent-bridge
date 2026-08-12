"""High-level operational read model composed from authoritative stores."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import func, or_, select

from .broker_projection import BrokerProjectionStore
from .coordinator_runtime import CoordinatorRuntime
from .db import (
    BridgeExecutionAttemptRow,
    BridgeExecutionRow,
    BrokerDeliveryRow,
    CoordinatorIntakeRow,
    Database,
    RoleLeaseRow,
    RoleRollupStateRow,
)
from .nodes import NodeStore
from .roles import RoleStore
from .supervision import BackgroundSupervisor


class LiveBrokerDiagnostics(Protocol):
    async def diagnostics(self) -> dict[str, Any]: ...


class TelemetryExporter(Protocol):
    """Injection point for optional OpenTelemetry or other metric exporters."""

    async def export(self, snapshot: dict[str, Any]) -> None: ...


class OperationalObservability:
    def __init__(
        self,
        *,
        database: Database,
        broker_projection: BrokerProjectionStore,
        nodes: NodeStore,
        roles: RoleStore,
        coordinator: CoordinatorRuntime,
        supervisor: BackgroundSupervisor,
        live_broker: LiveBrokerDiagnostics | None,
        exporters: tuple[TelemetryExporter, ...] = (),
    ) -> None:
        self.database = database
        self.broker_projection = broker_projection
        self.nodes = nodes
        self.roles = roles
        self.coordinator = coordinator
        self.supervisor = supervisor
        self.live_broker = live_broker
        self.exporters = exporters

    async def broker(self) -> dict[str, Any]:
        if self.live_broker is None:
            projected_consumers = self.broker_projection.list_consumers()
            return {
                "status": "unconfigured",
                "connected": False,
                "streams": [],
                "consumers": projected_consumers,
                "advisories": [],
            }
        return await self.live_broker.diagnostics()

    def pending_requests(self, *, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        active = ["queued", "leased", "running"]
        with self.database.session() as session:
            filters = [BridgeExecutionRow.status.in_(active)]
            total = _count(session, BridgeExecutionRow, *filters)
            rows = session.scalars(
                select(BridgeExecutionRow)
                .where(*filters)
                .order_by(BridgeExecutionRow.requested_at)
                .limit(limit)
                .offset(offset)
            ).all()
            return _page([_execution_summary(row) for row in rows], total, limit, offset)

    def executions(self, *, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        with self.database.session() as session:
            total = _count(session, BridgeExecutionRow)
            rows = session.scalars(
                select(BridgeExecutionRow)
                .order_by(BridgeExecutionRow.updated_at.desc())
                .limit(limit)
                .offset(offset)
            ).all()
            return _page([_execution_summary(row) for row in rows], total, limit, offset)

    def retries(self, *, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        retry_filter = or_(
            BridgeExecutionAttemptRow.attempt_number > 1,
            BridgeExecutionAttemptRow.status == "failed",
        )
        with self.database.session() as session:
            total = _count(session, BridgeExecutionAttemptRow, retry_filter)
            rows = session.scalars(
                select(BridgeExecutionAttemptRow)
                .where(retry_filter)
                .order_by(BridgeExecutionAttemptRow.updated_at.desc())
                .limit(limit)
                .offset(offset)
            ).all()
            items = [
                {
                    "attempt_id": row.attempt_id,
                    "execution_id": row.execution_id,
                    "attempt_number": row.attempt_number,
                    "node_id": row.node_id,
                    "status": row.status,
                    "error": row.error,
                    "updated_at": _iso(row.updated_at),
                }
                for row in rows
            ]
            return _page(items, total, limit, offset)

    def leases(self) -> dict[str, Any]:
        now = datetime.now(UTC)
        with self.database.session() as session:
            rows = session.scalars(select(RoleLeaseRow).order_by(RoleLeaseRow.expires_at)).all()
            items = [
                {
                    "lease_type": "role",
                    "source": "catalog.role_leases",
                    "resource_id": row.role_id,
                    "holder_id": row.holder_id,
                    "fencing_token": row.fencing_token,
                    "acquired_at": _iso(row.acquired_at),
                    "expires_at": _iso(row.expires_at),
                    "active": _aware(row.expires_at) > now,
                }
                for row in rows
            ]
            deliveries = session.scalars(
                select(BrokerDeliveryRow)
                .where(BrokerDeliveryRow.ack_deadline_at.is_not(None))
                .order_by(BrokerDeliveryRow.ack_deadline_at)
            ).all()
            items.extend(
                {
                    "lease_type": "broker_delivery",
                    "source": "broker_projection.deliveries",
                    "resource_id": row.delivery_id,
                    "holder_id": row.consumer,
                    "fencing_token": None,
                    "acquired_at": _iso(row.delivered_at),
                    "expires_at": _iso(row.ack_deadline_at),
                    "active": bool(row.ack_deadline_at and _aware(row.ack_deadline_at) > now),
                }
                for row in deliveries
            )
            executions = session.scalars(
                select(BridgeExecutionRow)
                .where(BridgeExecutionRow.status.in_(["leased", "running"]))
                .order_by(BridgeExecutionRow.updated_at)
            ).all()
            items.extend(
                {
                    "lease_type": "execution_state",
                    "source": "catalog.bridge_executions",
                    "resource_id": row.execution_id,
                    "holder_id": row.target_id,
                    "fencing_token": None,
                    "acquired_at": _iso(row.updated_at),
                    "expires_at": None,
                    "active": True,
                }
                for row in executions
            )
            return {"items": items, "total": len(items)}

    def artifacts(self, *, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        artifacts_by_key: dict[str, dict[str, Any]] = {}

        def collect(artifact: dict[str, Any], source: dict[str, Any]) -> None:
            key = str(artifact.get("artifact_id") or artifact.get("uri"))
            if key not in artifacts_by_key:
                artifacts_by_key[key] = {**artifact, "sources": []}
            artifacts_by_key[key]["sources"].append(source)

        with self.database.session() as session:
            rows = session.scalars(
                select(BridgeExecutionRow).order_by(BridgeExecutionRow.requested_at.desc())
            ).all()
            for row in rows:
                request = json.loads(row.request_json)
                for artifact in request.get("artifacts", []):
                    collect(
                        artifact,
                        {
                            "source_type": "execution_request",
                            "execution_id": row.execution_id,
                            "work_id": row.work_id,
                        },
                    )
                if row.result_json:
                    result = json.loads(row.result_json)
                    for artifact in result.get("artifacts", []):
                        collect(
                            artifact,
                            {
                                "source_type": "execution_result",
                                "execution_id": row.execution_id,
                                "work_id": row.work_id,
                            },
                        )
        for role in self.roles.list_roles():
            for checkpoint in self.roles.list_checkpoints(role.role_id):
                for artifact in checkpoint.evidence:
                    collect(
                        artifact.model_dump(mode="json"),
                        {
                            "source_type": "role_checkpoint",
                            "role_id": role.role_id,
                            "checkpoint_version": checkpoint.version,
                        },
                    )
        items = list(artifacts_by_key.values())
        total = len(items)
        return _page(items[offset : offset + limit], total, limit, offset)

    def roles_view(self) -> dict[str, Any]:
        leases = {
            item["resource_id"]: item
            for item in self.leases()["items"]
            if item["lease_type"] == "role"
        }
        items = []
        for role in self.roles.list_roles():
            value = role.model_dump(mode="json")
            value["lease"] = leases.get(role.role_id)
            checkpoint = self.roles.get_latest_checkpoint(role.role_id)
            value["latest_checkpoint"] = (
                {
                    "version": checkpoint.version,
                    "status": checkpoint.status,
                    "summary": checkpoint.parent_summary,
                    "blockers": checkpoint.blockers,
                    "recommended_next_action": checkpoint.recommended_next_action,
                    "created_at": checkpoint.created_at,
                }
                if checkpoint
                else None
            )
            rollup = None
            if role.parent_role_id:
                with self.database.session() as session:
                    rollup = session.scalar(
                        select(RoleRollupStateRow).where(
                            RoleRollupStateRow.parent_role_id == role.parent_role_id,
                            RoleRollupStateRow.child_role_id == role.role_id,
                        )
                    )
            value["rollup"] = {
                "stale": bool(
                    role.parent_role_id
                    and (
                        rollup is None
                        or rollup.incorporated_checkpoint_version < role.checkpoint_version
                    )
                ),
                "incorporated_checkpoint_version": (
                    rollup.incorporated_checkpoint_version if rollup else None
                ),
            }
            items.append(value)
        return {"items": items, "total": len(items)}

    def nodes_view(self) -> dict[str, Any]:
        items = self.nodes.list_nodes()
        return {"items": items, "total": len(items)}

    async def advisories(self) -> list[dict[str, Any]]:
        broker = await self.broker()
        items = list(broker.get("advisories", []))
        for node in self.nodes.list_nodes():
            if not node.get("reachable", False):
                items.append(
                    {
                        "severity": "warning",
                        "code": "node_unreachable",
                        "node_id": node["node_id"],
                        "message": f"node {node['node_id']} is unreachable",
                    }
                )
        unresolved = self.broker_projection.summary()["unresolved_dead_letters"]
        if unresolved:
            items.append(
                {
                    "severity": "error",
                    "code": "unresolved_dead_letters",
                    "count": unresolved,
                    "message": "dead letters require attention",
                }
            )
        if self.supervisor.degraded:
            items.append(
                {
                    "severity": "error",
                    "code": "background_worker_failed",
                    "message": "a supervised background worker has failed",
                }
            )
        return items

    async def summary(self) -> dict[str, Any]:
        broker = await self.broker()
        advisories = await self.advisories()
        nodes = self.nodes.list_nodes()
        roles = self.roles.list_roles()
        broker_projection = self.broker_projection.summary()
        with self.database.session() as session:
            executions = _count(session, BridgeExecutionRow)
            pending = _count(
                session,
                BridgeExecutionRow,
                BridgeExecutionRow.status.in_(["queued", "leased", "running"]),
            )
            attention = _count(
                session,
                CoordinatorIntakeRow,
                CoordinatorIntakeRow.attention_required.is_not(None),
            )
        status = (
            "degraded"
            if any(item.get("severity") in {"warning", "error"} for item in advisories)
            else "healthy"
        )
        return {
            "status": status,
            "broker_status": broker["status"],
            "background_status": self.supervisor.snapshot()["status"],
            "coordinator": self.coordinator.status(),
            "counts": {
                "nodes": len(nodes),
                "unreachable_nodes": sum(1 for node in nodes if not node.get("reachable", False)),
                "roles": len(roles),
                "executions": executions,
                "pending_requests": pending,
                "attention_required": attention,
                "unresolved_dead_letters": broker_projection["unresolved_dead_letters"],
                "consumer_pending": broker_projection["consumer_pending"],
            },
            "advisories": advisories,
            "observed_at": datetime.now(UTC).isoformat(),
        }

    async def prometheus(self) -> str:
        summary = await self.summary()
        counts = summary["counts"]
        values = {
            "agent_bridge_up": 1 if summary["status"] == "healthy" else 0,
            "agent_bridge_pending_requests": counts["pending_requests"],
            "agent_bridge_unresolved_dead_letters": counts["unresolved_dead_letters"],
            "agent_bridge_consumer_pending": counts["consumer_pending"],
            "agent_bridge_unreachable_nodes": counts["unreachable_nodes"],
            "agent_bridge_roles": counts["roles"],
            "agent_bridge_executions": counts["executions"],
        }
        return "".join(f"# TYPE {name} gauge\n{name} {value}\n" for name, value in values.items())

    async def export(self) -> None:
        snapshot = await self.summary()
        for exporter in self.exporters:
            await exporter.export(snapshot)


def _count(session: Any, model: type[Any], *filters: Any) -> int:
    return int(session.scalar(select(func.count()).select_from(model).where(*filters)) or 0)


def _page(items: list[dict[str, Any]], total: int, limit: int, offset: int) -> dict[str, Any]:
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def _execution_summary(row: BridgeExecutionRow) -> dict[str, Any]:
    return {
        "execution_id": row.execution_id,
        "operation": row.operation,
        "target": {"kind": row.target_kind, "id": row.target_id},
        "work_id": row.work_id,
        "conversation_id": row.conversation_id,
        "adapter": row.adapter,
        "status": row.status,
        "error": row.error,
        "requested_at": _iso(row.requested_at),
        "updated_at": _iso(row.updated_at),
        "completed_at": _iso(row.completed_at),
    }


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    return _aware(value).isoformat() if value else None
