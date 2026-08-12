# Manual Bridge API and CLI

Manual mode bypasses coordinator inference. The hub persists every submission before it asks the
configured Bridge publisher to deliver it. JetStream remains the delivery authority; the SQL rows
are the command/query and operational views.

## Submit a message

`POST /api/v1/bridge/messages` accepts an envelope-shaped input without controlled identity fields:

```json
{
  "envelope": {
    "kind": "message",
    "destination": {"kind": "room", "id": "planning"},
    "body": {"instruction": "Review the proposed test plan"},
    "work_id": "work-robot",
    "delivery": {"max_attempts": 3, "retry_backoff_seconds": 5, "acknowledgement_timeout_seconds": 60},
    "artifacts": [],
    "extensions": {"example.priority": "normal"}
  },
  "subject": "bridge.v1.room.planning"
}
```

The subject is optional and must remain inside the validated `bridge.v1` namespace. The server
generates `message_id`, `correlation_id`, `sender`, and `created_at`. Supplying those fields—or an
authorization field—is rejected instead of silently trusted.

## Submit and inspect an execution request

`POST /api/v1/bridge/requests` accepts:

```json
{
  "request": {
    "operation": "resume_conversation",
    "instruction": "Continue the robot server test",
    "target": {"kind": "node", "id": "robot-host"},
    "conversation_id": "conv-robot",
    "work_id": "work-robot",
    "parameters": {"suite": "abb_sim"}
  },
  "envelope": {
    "extensions": {"example.priority": "high"}
  }
}
```

The server generates the execution and message identities, persists a queued attempt, and embeds
the validated execution request in a normal Bridge request envelope. Query it through either
`GET /api/v1/bridge/requests/{execution_id}` or
`GET /api/v1/bridge/executions/{execution_id}`. List endpoints accept `status`, `work_id`, `limit`,
and `offset` filters.

`POST /api/v1/bridge/executions/{execution_id}/cancel` with a JSON `reason` publishes a correlated
control envelope. The central record becomes cancelled only after that control envelope is durably
published.

Broker delivery diagnostics live under `/api/v1/bridge/operations`; these are projections rather
than the authoritative Manual submission resources.

The hub-side `ExecutionResultProjectionWorker` uses a durable
`catalog-execution-results-v1` consumer on `bridge.v1.result.>`. It accepts the protocol's direct
`ExecutionProgress`, `ExecutionResult`, and `ExecutionFailure` bodies. Central SQL progress or
outcome state is committed before the result delivery is acknowledged, so a hub crash causes safe
redelivery rather than a lost completion. Malformed or unknown results are dead-lettered; transient
projection failures are negatively acknowledged for retry.

Set `AGENT_BRIDGE_NATS_SERVERS` to enable the catalog-owned publisher and result worker. Optional
`AGENT_BRIDGE_NATS_USERNAME` and `AGENT_BRIDGE_NATS_PASSWORD` must be provided together; a
`AGENT_BRIDGE_NATS_CREDENTIALS_FILE` can be used instead. At startup the catalog connects,
reconciles Bridge streams, and creates the durable result subscription. A connection or stream
provisioning failure fails startup rather than accepting Manual requests into a nonfunctional
publisher. The private Compose deployment supplies these settings and waits for NATS health.

## Headless CLI

The CLI accepts the same JSON documents as the HTTP API:

```shell
agent-bridge --api-url http://127.0.0.1:58081/api/v1 request-submit --json request.json
agent-bridge execution-status exec-012345
agent-bridge execution-cancel exec-012345 --reason "Changed test scope"
agent-bridge message-list --work-id work-robot
```

Use `--json -` to read a submission from standard input. The CLI prints the authoritative response
as JSON and exits nonzero for input, transport, or HTTP errors.

## Headless execution runner

`agent-bridge-runner serve --config runner.json` is the long-lived native process that performs
capability and node-addressed work. It blocks on JetStream pull consumers, not on a foreground
model or repeated HTTP polling. Each runner persists claims, attempts, leases, cancellations, and
terminal outcomes in local SQLite. A terminal result is durably published before the input is
acknowledged; redelivery after restart republishes the stored outcome without repeating the side
effect. See [`abb-simulator-e2e.md`](abb-simulator-e2e.md) for the RobotStudio configuration.

For a complete cross-machine, capability-addressed request, runner configuration, offline-delivery
procedure, and result workflow, see [ABB simulator E2E](abb-simulator-e2e.md).
