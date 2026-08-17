# Agent Bridge Single-User Core Rework

Status: implemented in the `0008` product-boundary migration
Updated: 2026-08-14

## Product thesis

Agent Bridge is a durable address book, message fabric, and attention surface for AI
conversations operating across machines and execution environments.

It is not a system for modeling or orchestrating AI organizations. Modern Codex and Claude
agents already plan, launch native subagents, review work, run tests, and ask for approval. Bridge
adds value where a single provider chat cannot: finding selected chats across locations, moving
messages between independently running chats, and showing which remote conversations finished or
need input.

## Scope

The single-user core provides:

1. A selected conversation directory spanning Codex, Claude, machines, worktrees, hosts, and dev
   containers.
2. Durable direct messages and lightweight rooms over one logical NATS JetStream service.
3. An attention inbox split into ordinary updates and items that need the user.
4. Trusted remote nodes that discover provider chats, execute native open/turn/start actions only
   in their owning environment, and never fall back to another machine.
5. Web, CLI, HTTP, and MCP interfaces over the same domain operations.
6. NATS activity and issue history covering publishes, deliveries, acknowledgements, retries,
   dead letters, connection failures, streams, and consumers.

The core deliberately does not provide Work Items, Roles, Relationships, coordinators, autonomy
modes, workflow definitions, stages, gates, capability competition, fan-out, planner/auditor
conventions, or a general execution engine. Agents remain free to change direction in an ordinary
conversation and to use their provider-native subagent features.

## Architecture

```text
Codex / Claude native stores
          │ discover every 10 s (and on an optional provider hook)
          ▼
  Node daemon per execution environment ───── authenticated HTTPS ────┐
          │ native open / start / turn commands                        │
          └─────────────────────────────────────────────────────────────┤
                                                                        ▼
                                                             Single-user Hub
                                                     SQLite directory + attention
                                                                        │
                                             durable messages / rooms   │
                                                                        ▼
                                                               NATS JetStream
```

The Hub can also own a local environment directly. Remote nodes use HTTPS for catalog sync and
command claiming; they do not receive reusable broker credentials. The Hub is the only ordinary
single-user broker principal.

## Conversation identity and selection

- `provider_thread_id` is the provider-native identity.
- `conversation_id` is deterministic across provider, thread, node, and environment.
- `conversation_number` is allocated once when a discovered chat is selected.
- `provider_title` remains provider-owned.
- `alias` is the Bridge display name. Human edits and actual provider title changes use
  last-writer-wins semantics.
- The primary label is `Chat N · alias`; internal hashes are not shown as short IDs.
- Discovery creates candidates. **Add → Select all current** selects only the current candidate
  set; future conversations are never silently selected.
- Transcript payloads are retained only for selected conversations.

Provider-native subagents are cataloged visibly and linked to their parent where discoverable.
Their delivery mode is truthful: `direct`, `via_parent`, or `catalog_only`. Bridge never silently
sends a child-targeted message to its parent.

## Messaging behavior

Direct messages target one selected conversation. Rooms have members with one of three delivery
modes:

- `wake`: deliver the message as an ordinary provider user turn;
- `notify`: create an update without starting a turn;
- `digest`: retain it for a later summarized update.

Every message has a generated message ID, correlation ID, optional causation ID, source, target,
operation, durable state, and error. Bridge does not impose a conversational exchange limit;
correlation counts and rates make runaway traffic visible.

Provider turns receive a plain authenticated header containing source, message ID, correlation,
and operation. Per-conversation Bridge turns are serialized. Codex delivery resumes the stored
thread and waits for the official `turn/completed` notification; Claude delivery waits for the
non-interactive process. Broker leases are extended while a local turn runs. Duplicate broker
delivery does not create a duplicate turn after a message is delivered or durably queued remotely.

An agent may start a new full Codex or Claude conversation through MCP, CLI, or HTTP and may choose
a trusted node/environment. This is a convenience action, not a hierarchy or role relationship.

## Attention and diagnostics

The attention surface has two lanes:

- **Needs attention:** failed delivery, provider failure, remote-node failure, or an explicit
  `needs_user` operation.
- **Updates:** completed local/remote turns, remotely started agents, and room notifications.

The NATS page combines live diagnostics with durable activity records. It exposes broker status,
stream and consumer state, inbound/outbound history, retry and lease events, dead letters, startup
or reconciliation issues, message/correlation identifiers, and JSON export.

## Interface matrix

| Action | Web UI | CLI | MCP | Native provider UI | Mechanism |
| --- | --- | --- | --- | --- | --- |
| Discover current chats | automatic | `agent-bridge reconcile` | — | creates chats naturally | 10-second reconciliation; full payload refresh |
| Select current chats | Add dialog / Select all current | `agent-bridge add …` | — | — | candidate IDs become selected and receive numbers |
| Search/list chats | directory | `agent-bridge chats` | `list_conversations` | provider-local only | Hub FTS and filters |
| Inspect a chat | detail pane | `agent-bridge show` | `get_conversation` | native transcript | selected projection |
| Rename/annotate | conversation detail | `agent-bridge rename` | — | provider title edit | alias metadata API |
| Open native chat | Open native | `agent-bridge open` | `open_conversation` | already native | local launch or fenced remote command |
| Send direct message | composer | `agent-bridge message --chat` | `send_message` | type a prompt manually | NATS inbox → provider user turn |
| Send room message | room composer | `agent-bridge message --room` | `send_message` | — | NATS room → wake/notify/digest members |
| Start full agent | creation form | `agent-bridge start [--model ...] [--effort ...]` | `start_agent` | New chat | local App Server/Claude or remote command; optional launch model and effort |
| Send explicit turn / change effort | detail composer | `agent-bridge turn [--effort ...]` | `send_turn` | type a prompt / change provider setting | effort override applies to this and later turns; model cannot be changed after launch |
| View/ack attention | Attention | `attention` / `ack` | `list_attention` / `acknowledge_attention` | provider notification | durable attention rows |
| Manage rooms | Rooms | `rooms` | `list_rooms` | — | room and membership APIs |
| Inspect machines | Machines | `nodes` | `list_nodes` | — | heartbeat/environment ownership |
| Inspect NATS | NATS server log | `nats` | — | — | live JetStream diagnostics + durable events |

HTTP/OpenAPI remains the complete low-level interface under `/api/v1`. The MCP server is a local
stdio facade (`agent-bridge-mcp`) over that API, so agents do not need direct database or NATS
access.

## API disposition

### Kept and amended

- Conversations: changed from auto-imported catalog records to candidates plus explicit selection;
  added stable numbers, aliases, kinds, delivery modes, location facts, turns, open, and creation.
- Nodes/environments: kept authenticated sync, heartbeat, credentials, and fenced commands; added
  message-turn and agent-start commands.
- Broker projection: retained internally for delivery/dead-letter history and surfaced through the
  `/nats/*` product vocabulary.
- Backup, restore, retention, transcript deletion, redaction, and credential rotation: retained and
  retargeted to conversation messages and NATS events.

### Added

- `/conversations/candidates`, `/conversations/import`, collections, rooms, messages,
  correlations, attention, and NATS diagnostics/activity endpoints.
- Unified human CLI and local stdio MCP tools.
- Codex `thread/start`, `thread/resume`, `turn/start`, and completion-aware delivery.

### Removed

- Work Item, Role, Relationship, coordinator, convergence, workflow, execution-request, capability,
  fan-out, collaboration-topology, Manual Bridge, and legacy operations APIs.
- Runner/coordinator/integration packages and their provider-neutral organizational contracts.
- UI pages and deployment credentials belonging to those systems.

Any old endpoint or contract not represented by this document is intentionally unsupported and
must not be kept as a hidden compatibility surface. Migration `0008` stores one JSON snapshot in
`legacy_exports`, then removes the old tables. It is an intentional non-downgradable boundary;
restore a pre-0008 database backup to run the retired product.

## Reconciliation cadence

- Provider/node discovery: every 10 seconds by default (`AGENT_BRIDGE_DISCOVERY_INTERVAL_SECONDS`
  or `AGENT_BRIDGE_NODE_INTERVAL`).
- Full reconciliation safety pass: configurable at 5 minutes; the current adapters' normal pass is
  complete, so the fast pass also serves as the full repair pass.
- Provider hooks may invoke `agent-bridge reconcile` locally or `agent-bridge-node --once` remotely
  for immediate refresh. Hooks are an acceleration, never the sole source of truth.
- Web attention/messages/NATS poll every 5 seconds; directory and machine views poll every 10.

## Deployment boundary

The supported core topology is one logical NATS service. A single server is sufficient for a
personal network. The transport accepts multiple server URLs and configurable stream replicas, so
a three-node JetStream cluster may be used for availability without changing Bridge semantics.
Federation and multi-user authorization are explicitly deferred to
[shared-workspaces.md](shared-workspaces.md).

## Acceptance

- Fresh and upgraded databases create only the core, node, and broker tables; legacy rows are
  exported before old tables are removed.
- Current candidates can be selected without selecting future chats.
- Codex and Claude root chats and native subagents are cataloged with truthful delivery modes.
- Direct and room messages are durable, correlated, deduplicated, retryable, and observable.
- Local and remote provider turns preserve location ownership and surface completion/failure.
- Agent creation works locally and through a trusted node command.
- Removed organizational APIs return 404.
- Python tests, Ruff, strict mypy, frontend tests/typecheck/build, and a fresh Alembic upgrade pass.
