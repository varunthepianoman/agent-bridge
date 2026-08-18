# Agent Bridge

Agent Bridge is a private address book, message fabric, and attention dashboard for selected Codex
and Claude conversations across machines and execution environments.

It catalogs chats you choose, sends durable direct/room messages through NATS JetStream, wakes the
owning provider conversation as an ordinary user turn, and shows completions or failures in one
place. It intentionally does not model Work Items, Roles, agent hierarchies, or workflows.

The implemented architecture and complete interface matrix are in
[docs/plans/general-execution-engine.md](docs/plans/general-execution-engine.md). Multi-user sharing
is a separate future add-on in [docs/plans/shared-workspaces.md](docs/plans/shared-workspaces.md).

## Quick start

Requirements: Python 3.12+, Node.js 18+, an authenticated Codex CLI, and optionally Claude Code.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/agent-bridge serve
```

The Hub can run without NATS for catalog-only use. Configure NATS to enable durable messaging:

```bash
export AGENT_BRIDGE_NATS_SERVERS=nats://127.0.0.1:4222
export AGENT_BRIDGE_NATS_USERNAME=catalog
export AGENT_BRIDGE_NATS_PASSWORD='...'
.venv/bin/agent-bridge serve
```

Run the web UI separately during development:

```bash
cd catalog-web
npm install
npm run dev
```

Open `http://127.0.0.1:5173`, choose **Add chats**, and select current candidates. Discovery runs
every 10 seconds. Enable **Auto-add new chats** in the Conversations header to catalog every chat
first discovered afterward, including provider-native subagents. Existing candidates remain
unselected until you add them explicitly.

With `AGENT_BRIDGE_NATIVE_LAUNCH=1`, each local conversation offers two independent actions:
**Open in Codex/Claude** hands a provider deep link to the desktop operating system, while
**Open in Terminal** resumes the exact local session with the provider CLI. Claude Desktop's public
deep-link format opens a new Claude Code session in the same workspace; use Terminal when you need
to resume the exact local Claude Code session.

## Interfaces

- Web/OpenAPI: `http://127.0.0.1:58081/docs`
- CLI: `agent-bridge --help`
- Local stdio MCP: `agent-bridge-mcp`
- Remote node: `agent-bridge-node` (`--once` is suitable for provider hooks)

Useful commands:

```bash
agent-bridge reconcile
agent-bridge candidates
agent-bridge add <conversation-id>
agent-bridge chats --query socket
agent-bridge message --chat <conversation-id> "Check the server side"
agent-bridge start --provider codex --cwd /work/project \
  --model gpt-5.6-sol --effort high "Investigate the failing test"
agent-bridge turn <conversation-id> --effort xhigh "Re-check the edge cases"
agent-bridge attention
agent-bridge nats
```

`start` accepts optional provider model and reasoning-effort overrides. Without them, the provider's
configured defaults apply. An existing conversation's effort can be changed only through an
explicit `turn --effort`; ordinary Bridge messages never change it. Bridge intentionally does not
support changing a conversation's model after launch.

## Configuration

| Variable | Purpose | Default |
| --- | --- | --- |
| `AGENT_BRIDGE_STATE_DIR` | Persistent Hub state directory | XDG state / `~/.local/state` |
| `AGENT_BRIDGE_DATABASE_URL` | SQLAlchemy database URL | SQLite in state directory |
| `AGENT_BRIDGE_NODE_ID` | Stable machine identity | hostname |
| `AGENT_BRIDGE_ENVIRONMENT_ID` | Stable host/container identity | `host` |
| `AGENT_BRIDGE_CODEX_BIN` | Codex executable | `codex` |
| `AGENT_BRIDGE_CLAUDE_BIN` | Claude executable | `claude` |
| `AGENT_BRIDGE_NATIVE_LAUNCH` | Permit explicit terminal/UI launch | `0` |
| `AGENT_BRIDGE_DISCOVERY_INTERVAL_SECONDS` | Local reconciliation cadence | `10` |
| `AGENT_BRIDGE_NATS_SERVERS` | Comma-separated broker URLs | unset |
| `AGENT_BRIDGE_NATS_REPLICAS` | JetStream stream replicas | `1` |
| `AGENT_BRIDGE_NATS_USERNAME/PASSWORD` | Broker credentials | unset |
| `AGENT_BRIDGE_NATS_CREDENTIALS_FILE` | NATS credentials-file alternative | unset |

Remote nodes additionally use `AGENT_BRIDGE_HUB_URL`, `AGENT_BRIDGE_NODE_TOKEN`, and
`AGENT_BRIDGE_NODE_INTERVAL` (default 10 seconds). The Hub URL must be HTTPS except on loopback.
See the [Windows NUC node runbook](docs/windows-nuc-node-setup.md) for the first cross-machine
native-Windows setup.

## Migration

```bash
AGENT_BRIDGE_DATABASE_URL=sqlite:////absolute/path/catalog.db \
  .venv/bin/alembic upgrade head
```

Migration `0008` is the intentional boundary from the retired orchestration product. It writes a
JSON snapshot of old tables to `legacy_exports`, removes them, and cannot be downgraded in place.
Take a verified backup first if the old product must remain runnable.

## Verification

```bash
.venv/bin/pytest -q
.venv/bin/ruff check src tests migrations
.venv/bin/mypy
cd catalog-web
npm run typecheck
npm test -- --run
npm run build
```
