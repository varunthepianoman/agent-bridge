# Observability

The operational overview is intentionally separate from raw broker diagnostics. The overview
answers “what needs attention?” without requiring terminal or NATS inspection. JetStream remains
delivery authority; SQL projections are query and history views.

## High-level API

`GET /api/v1/observability/summary` returns overall, broker, background-worker, and coordinator
status plus counts and actionable advisories. Related list endpoints are:

- `/broker` for selected live stream and consumer lag state
- `/advisories`
- `/pending-requests`, `/executions`, and `/retries`
- `/leases` and `/dead-letters`
- `/artifacts`, `/roles`, and `/nodes`

Execution overview records omit instructions and payload bodies. Broker diagnostics expose sizes,
sequences, counts, and identities, never message payloads or credentials.

Role list items include `latest_checkpoint` summary, blockers, recommended action, and a `rollup`
object whose `stale` flag detects a parent summary that has not incorporated the child's current
checkpoint. Artifact rows aggregate execution-request, execution-result, and checkpoint-evidence
sources. Lease rows identify both `lease_type` and authoritative `source`; role leases, projected
broker delivery leases, and active execution-state projections are intentionally distinguishable.

Consumer lag uses the same names in live and projected views: `pending_count`,
`ack_pending_count`, and `redelivered_count`. Consumer rows also expose `observed_at` and `stale`;
projected consumer state becomes stale after two minutes without a fresh observation, while a live
broker read is never stale.

Lower-level troubleshooting is available under `/api/v1/diagnostics`: `/background`, `/broker`,
`/messages`, and `/deliveries`. The existing `/api/v1/bridge/operations` projection API remains
available for message-specific investigation.

`GET /api/v1/health` reports `degraded` when a critical supervised projection task has stopped or
the configured NATS connection is lost. The process stays alive so Manual and Catalog operations
that do not depend on the failed component remain available.

## Metrics and telemetry

Set `AGENT_BRIDGE_METRICS_ENABLED=1` to expose Prometheus text at `/metrics` and
`/api/v1/observability/metrics`. The default installation has no Prometheus dependency.

OpenTelemetry and other exporters use the optional `TelemetryExporter` protocol passed to
`create_app(telemetry_exporters=...)`. Exporters receive the same payload as the summary API on the
configured `AGENT_BRIDGE_TELEMETRY_INTERVAL_SECONDS` interval. This keeps OpenTelemetry SDK and
collector packages optional and lets a deployment select its exporter and credential strategy.

HTTP responses include `X-Correlation-ID`; callers may supply the same header. The shared logging
context supports conversation, role, work, message, execution, correlation, and node IDs through
`bind_log_context` and `structured_extra` so JSON log formatters or OpenTelemetry log bridges can
preserve those fields.

## Advisories

Live diagnostics derive advisories for missing streams, reported stream data loss, JetStream API
errors, consumer lag, and redelivery. The operational view also reports unreachable nodes,
unresolved dead letters, and failed critical background tasks. Informational consumer lag remains
healthy; warnings and errors mark the overview degraded.
