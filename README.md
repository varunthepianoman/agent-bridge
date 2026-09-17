# Agent Bridge

Agent Bridge is a private address book, message fabric, and attention dashboard for selected Codex
and Claude conversations across machines and execution environments.

It catalogs chats you choose, sends durable direct/room mail through NATS JetStream, and shows
completion, blocker, and listener state in one place. Mail never creates or steers a provider turn;
provider mutations are separate, explicit operations. It intentionally does not model Work Items,
Roles, agent hierarchies, or workflows.

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

Each local conversation offers two independent actions: **Open in Codex/Claude** hands a provider
deep link to the desktop operating system, while **Open in Terminal** resumes the exact local
session with the provider CLI. Native launch is always allowed when the host supports the requested
action; scheme, platform, executable, path, and argv validation still apply. Claude Desktop's
public deep-link format opens a new Claude Code session in the same workspace; use Terminal when
you need to resume the exact local Claude Code session.

## Interfaces

- Web/OpenAPI: `http://127.0.0.1:58080/docs`
- CLI: `agent-bridge --help`
- Local stdio MCP: `agent-bridge-mcp`
- Remote node: `agent-bridge-node` (`--once` is suitable for provider hooks)

Useful commands:

```bash
agent-bridge reconcile
agent-bridge candidates
agent-bridge add <conversation-id>
agent-bridge chats --query socket
agent-bridge bio <conversation-id> "Build and deployment specialist"
agent-bridge message --chat <conversation-id> "Check the server side"
agent-bridge message --chat <conversation-id> --from-chat <source-id> \
  --request-ack --wait-for acknowledged --timeout 30 "Start the review"
agent-bridge inbox <conversation-id>
agent-bridge wait <conversation-id> --max-wait-seconds 3600
agent-bridge acknowledge <conversation-id> <message-id> --detail "Starting work"
agent-bridge wait-receipt <source-id> <message-id> --until acknowledged --timeout 3600
agent-bridge complete <conversation-id> <message-id> --outcome succeeded
agent-bridge requeue <conversation-id> <message-id> --detail "Safe to retry"
agent-bridge stop-listener <conversation-id>
agent-bridge refresh <conversation-id> --wait-seconds 30
agent-bridge refresh <conversation-id> --wait-seconds 30 --last-message-only
agent-bridge start --provider codex --cwd /work/project \
  --bio "Diagnoses backend failures" --model gpt-5.6-sol --effort high \
  "Investigate the failing test"
agent-bridge turn <conversation-id> --effort xhigh "Re-check the edge cases"
agent-bridge attention
agent-bridge wait-attention --max-wait-seconds 3600
agent-bridge nats
```

`start` accepts optional provider model and reasoning-effort overrides. Without them, the provider's
configured defaults apply. An existing conversation's effort can be changed only through an
explicit `turn --effort`; ordinary Bridge messages never change it. Bridge intentionally does not
support changing a conversation's model after launch.

Each conversation has a Bridge-owned public directory `bio` of up to 500 characters. It appears in
`list_conversations`, API list/detail/candidate/import responses, CLI output, and the web directory,
and it is included in full-text search. Use MCP `set_conversation_bio`, CLI `agent-bridge bio`, or
the web detail editor to set or clear it. Bios summarize what an agent is useful for; `notes` remain
long-form internal metadata. Provider reconciliation never changes either field.

`message` appends to the recipient's durable mailbox and normally returns after Hub acceptance. It
never resumes, wakes, or steers the provider task. A sender can opt into a bounded foreground wait
for `claimed`, `acknowledged`, or `terminal`; timing out reports the durable transport, receipt,
listener, and node state without changing the message or starting background work.

An agent receives mail only while it has explicitly entered foreground listener mode; the pending
listener tool holds that agent's existing writer, and cancelling the turn or issuing
`stop-listener` releases it normally. Listener delivery automatically records `claimed`. If a
direct message requests acknowledgment and work will continue, call `acknowledge_message` (or
`agent-bridge acknowledge`) once. If the work finishes immediately, call only `complete_message`
(or `agent-bridge complete`): completion implicitly acknowledges it. Messages that did not request
acknowledgment need no extra call. Receipt notifications stay outside provider transcripts. Use
`turn` only when a new provider turn is intended.

The MCP facade uses one pooled asynchronous HTTP client, so waits do not block unrelated tools or
other waits on the same MCP session. `wait_mailbox`, `wait_for_receipt`, and `wait_for_attention`
accept an optional absolute `wait_until`. When `AGENT_BRIDGE_MCP_WAIT_SLICE_SECONDS` is set and an
overall wait outlasts one slice, they return `status: "continue"` plus the exact continuation tool
arguments; call that tool again rather than restarting the deadline. A sliced `send_message` wait
continues through `wait_for_receipt` and must never resend the message.

To wait indefinitely for the next available batch of mail, use:

```bash
agent-bridge wait <conversation-id> --forever
```

Or call MCP `wait_mailbox(conversation_id="<conversation-id>", forever=true)`.
The CLI silently renews empty 60-second waits and prints the first received batch before exiting.
MCP performs one request per call (at most 60 seconds, or a shorter configured wait slice); after
an empty timeout it returns `status: "continue"` with `forever=true` in the continuation arguments.
Follow those arguments until mail arrives; there is no overall deadline. Existing timed defaults
remain unchanged. CLI `--forever` conflicts with `--max-wait-seconds`; MCP `forever=true` conflicts
with `wait_until` and ignores `max_wait_seconds`, which applies only to timed waits.

After finishing work, enter the indefinite wait. Process the returned mail, then enter a new wait
when done. Collect CLI output in the active task; do not leave an unattended background consumer.
Cancel the CLI with Ctrl-C, cancel the MCP tool call, or use `stop-listener` for the active listener.
A stopped response ends the wait; transport failures and other errors are reported without retry.
This mode does not automatically recover or redeliver claimed messages; inspect the inbox manually
if a result is lost to interruption. It waits for mail only and does not keep processing after mail
arrives or restart an ended agent turn.

For observability, `refresh` requests sanitized, read-only transcript data from the machine that
owns a conversation. Remote Codex refresh uses App Server `thread/read(includeTurns=true)` and does
not resume, subscribe to, or acquire the task writer. Pass `last_message_only=true` to the HTTP or
MCP operation, or `--last-message-only` to the CLI, to return only the newest native assistant
message while still refreshing and storing the complete sanitized transcript. A chat with no
assistant message returns `last_message: null`. See
[ADR 0004](docs/adr/0004-durable-mailbox-and-foreground-listener.md).

### Read the last N messages

Use `last_n_messages` (1–500) on `get_conversation` or `refresh_conversation`:

```bash
agent-bridge show conv-… --last-n-messages 5
agent-bridge refresh conv-… --last-n-messages 5
agent-bridge refresh conv-… --last-message-only
```

HTTP equivalents are `GET /api/v1/conversations/{id}?last_n_messages=5` and
`POST /api/v1/conversations/{id}/refresh?last_n_messages=5&wait_seconds=10`.
The MCP tools accept `last_n_messages=5`; refresh also accepts `last_message_only=true`.
Choose only one refresh selector. The latest-only selector means the latest **assistant**
reply; last-N counts both user and assistant prose messages, including assistant progress
messages, and returns them oldest to newest. Tool output and reasoning are excluded.

Last-N responses contain `conversation_id`, `messages` (`role` and `text`), `message_count`,
and `has_more`. Successful refresh adds `status` and `command_id`. They omit full transcript,
preview, and raw metadata so old history is not repeated. Omitting the selector preserves the
existing full response. A queued refresh still returns HTTP 202 and its command ID; after it
finishes, read with `show --last-n-messages` / `get_conversation(last_n_messages=...)`.

Message boundaries are retained at discovery/refresh time, not inferred from flattened text.
Upgrade the owning node and resync or refresh existing conversations before using last-N.
Older projections without message boundaries return HTTP 409 with an upgrade/resync instruction;
no schema migration is needed. Stored reads support Codex and Claude; remote refresh remains
Codex-only. A successfully collected empty conversation returns an empty messages list.

### App connector schema updates

The app connector's configured tunnel launches `agent-bridge-mcp`, the same server as direct MCP.
Its `tools/list` schema exposes both `last_n_messages` and refresh's `last_message_only`.
After upgrading, reload the tunnel's MCP process when its requests are idle. Then refresh the
connector's tool definitions in the client if it still advertises the old parameter list;
already-open sessions may retain their old tool schema. Updating Hub code alone does not refresh
cached connector definitions. This requires no conversation resume or writer acquisition.

## Configuration

| Variable | Purpose | Default |
| --- | --- | --- |
| `AGENT_BRIDGE_STATE_DIR` | Persistent Hub state directory | XDG state / `~/.local/state` |
| `AGENT_BRIDGE_DATABASE_URL` | SQLAlchemy database URL | SQLite in state directory |
| `AGENT_BRIDGE_NODE_ID` | Stable machine identity | hostname |
| `AGENT_BRIDGE_ENVIRONMENT_ID` | Stable host/container identity | `host` |
| `AGENT_BRIDGE_CODEX_BIN` | Codex executable | `codex` |
| `AGENT_BRIDGE_CLAUDE_BIN` | Claude executable | `claude` |
| `AGENT_BRIDGE_DISCOVERY_INTERVAL_SECONDS` | Local reconciliation cadence | `10` |
| `AGENT_BRIDGE_NATS_SERVERS` | Comma-separated broker URLs | unset |
| `AGENT_BRIDGE_NATS_REPLICAS` | JetStream stream replicas | `1` |
| `AGENT_BRIDGE_NATS_USERNAME/PASSWORD` | Broker credentials | unset |
| `AGENT_BRIDGE_NATS_CREDENTIALS_FILE` | NATS credentials-file alternative | unset |
| `AGENT_BRIDGE_MCP_WAIT_SLICE_SECONDS` | MCP wait slice; unset keeps one uninterrupted wait | unset |

Remote nodes additionally use `AGENT_BRIDGE_HUB_URL`, `AGENT_BRIDGE_NODE_TOKEN`, and
`AGENT_BRIDGE_NODE_INTERVAL` (default 10 seconds). The Hub URL must be HTTPS except on loopback.
See the [Windows NUC node runbook](docs/windows-nuc-node-setup.md) and
[ABB T-Box Ubuntu node runbook](docs/abb-t-box-linux-node-setup.md) for cross-machine setup.

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
