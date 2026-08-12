# Agent Bridge

Agent Bridge is becoming two complementary, independently usable tools:

- **AI Work Catalog** finds, searches, organizes, and resumes native coding-agent conversations.
- **Agent Bridge** provides a durable NATS JetStream communication substrate; Manual UI,
  runners, and optional coordinator layers on top.

Milestones 0 through 9 are implemented: shared contracts, Codex and Claude discovery, the
SQLite/FTS5 Catalog, work and durable-role organization, and a private multi-machine
Catalog with native node agents, plus the durable JetStream transport, operational
projection, first-class Manual UI/CLI, durable execution runners, and the optional
portfolio/work coordinator runtime, and topology-neutral collaboration rooms,
request/reply, capability, fan-out, native-subagent, and policy-adapter support, plus the
Operations overview, diagnostics, metrics hooks, retention, backup/restore, deletion, and
credential-rotation tooling. ABB simulator tests now use a durable, capability-addressed
JetStream request and the supervised `agent-bridge-runner`; the legacy Windows–Ubuntu HTTP mailbox
and polling clients have been removed.

See [SPEC.md](SPEC.md) for product behavior and [IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md)
for delivery order and current status.

## Local Catalog quick start

Requirements:

- Python 3.12+
- Node.js 18+
- An installed and authenticated `codex` CLI; Claude Code is optional for Claude discovery/recovery

Install and start the API:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/agent-bridge-catalog
```

In another terminal, start the UI:

```bash
cd catalog-web
npm install
npm run dev
```

Open `http://127.0.0.1:5173` and choose **Sync**. App Server remains a local stdio
subprocess. By default, **Open in Codex** returns an exact resume command without launching
a terminal. Set `AGENT_BRIDGE_NATIVE_LAUNCH=1` before starting the API to enable explicit
user-triggered terminal launch when a supported terminal is present.

Catalog state defaults to `$XDG_STATE_HOME/agent-bridge/catalog.db` when `XDG_STATE_HOME`
is absolute, otherwise `~/.local/state/agent-bridge/catalog.db`.

Useful configuration:

| Variable | Purpose |
| --- | --- |
| `AGENT_BRIDGE_STATE_DIR` | Override local persistent state directory |
| `AGENT_BRIDGE_DATABASE_URL` | Override the SQLAlchemy database URL |
| `AGENT_BRIDGE_NODE_ID` | Stable identity for this machine |
| `AGENT_BRIDGE_ENVIRONMENT_ID` | Stable identity for this host/container/workspace environment |
| `AGENT_BRIDGE_CODEX_BIN` | Path to the Codex executable |
| `AGENT_BRIDGE_CLAUDE_BIN` | Path to the Claude Code executable on node agents |
| `AGENT_BRIDGE_NATIVE_LAUNCH` | Set to `1` to allow explicit native terminal launch |
| `AGENT_BRIDGE_NATS_SERVERS` | Comma-separated broker URLs; enables Manual publishing and result projection |
| `AGENT_BRIDGE_NATS_USERNAME` | Broker username; must be paired with a password |
| `AGENT_BRIDGE_NATS_PASSWORD` | Broker password; must be paired with a username |
| `AGENT_BRIDGE_NATS_CREDENTIALS_FILE` | Credentials-file alternative to username/password |
| `AGENT_BRIDGE_COORDINATOR_ENABLED` | Set to `1` to enable SDK-backed background coordination |
| `AGENT_BRIDGE_COORDINATOR_MODEL` | Optional coordinator model override |
| `AGENT_BRIDGE_COORDINATOR_WORKSPACE` | Default workspace for new coordinator conversations |

Execution requests may also include a first-class `cwd`. Manual Bridge exposes it for
new and resumed Codex turns, records it in the execution envelope, and passes it to the
Codex SDK. The legacy `parameters.workspace` form remains accepted for queued requests.

## API

Implemented local endpoints:

- `GET /api/v1/health`
- `GET /api/v1/conversations`
- `GET /api/v1/conversations/{conversation_id}`
- `PATCH /api/v1/conversations/{conversation_id}`
- `GET /api/v1/search?q=...`
- `POST /api/v1/actions/sync`
- `POST /api/v1/actions/resume`
- `GET/POST /api/v1/work-items`
- `GET/POST /api/v1/roles`
- `GET/POST /api/v1/relationships`
- `GET/POST /api/v1/nodes`
- `GET/POST /api/v1/bridge/messages`
- `POST /api/v1/bridge/requests`
- `GET /api/v1/bridge/executions`
- `POST /api/v1/bridge/executions/{execution_id}/cancel`
- `GET /api/v1/bridge/operations`
- `GET /api/v1/coordinator/runtime`
- `GET/POST /api/v1/coordinator/intake`
- `POST /api/v1/coordinator/intake/{request_id}/decision`
- `GET/POST /api/v1/coordinator/roles/{role_id}/activations`
- `GET /api/v1/coordinator/roles/{role_id}/rollups`
- `GET/POST /api/v1/collaboration/endpoints`
- `GET/POST /api/v1/collaboration/rooms`
- `GET/POST /api/v1/collaboration/messages`
- `GET /api/v1/collaboration/topology`
- `GET /api/v1/collaboration/native-subagents`
- Authenticated node synchronization, heartbeat, claim, and result endpoints under
  `/api/v1/node/*`

Interactive OpenAPI documentation is served at `/docs`.

For multi-machine operation, see [the native node guide](docs/node-agent.md) and
[the private hub guide](docs/private-hub.md). Node credentials are distinct, stored only
as salted hashes by the hub, and should be transported only over HTTPS.

For the broker subject model, credentials, and private deployment boundary, see
[the JetStream guide](docs/nats-jetstream.md).
For coordinator-free submission and execution, see
[the Manual Bridge guide](docs/manual-bridge.md).
For the ABB simulator capability and migration from the removed HTTP mailbox, see
[the ABB durable runner guide](docs/abb-simulator-e2e.md).
For optional SDK-backed intake and durable role activation, see
[the coordinator guide](docs/coordinator.md).
For peer, room, fan-out, capability, and planner/auditor traffic, see
[the collaboration guide](docs/collaboration.md). The optional policy boundary is
documented in [the AIWK adapter guide](docs/aiwk-adapter.md).
For retention, transcript deletion, verified backup/restore, credential rotation, and recovery,
see [the hardening and recovery guide](docs/hardening-and-recovery.md).

## Contracts and migrations

Generate the JSON Schema and TypeScript protocol bindings:

```bash
.venv/bin/python scripts/generate_contracts.py
cd catalog-web && npm run generate:protocol
```

Apply the initial migration to an explicitly configured database:

```bash
AGENT_BRIDGE_DATABASE_URL=sqlite:////absolute/path/catalog.db \
  .venv/bin/alembic upgrade head
```

The local server also creates missing Milestone 1 tables on first start so the quick start
does not require a separate migration command.

## Verification

```bash
.venv/bin/pytest -q
.venv/bin/ruff check src tests migrations
.venv/bin/mypy src
cd catalog-web
npm run typecheck
npm test
npm run build
npm audit --omit=dev
```

## ABB simulator E2E

Submit `examples/abb-robot-simulator-e2e.request.json` through the Manual UI or CLI. One eligible
Windows runner claims capability `robot-simulator-e2e`; JetStream retains the request while that
runner is offline and the Catalog projects progress and the terminal result. No model or shell
process needs to poll in the foreground.
