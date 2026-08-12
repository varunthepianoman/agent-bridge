# Recovered transcript: Design multi-agent bridge

- Session ID: `019ff1ca-c220-7152-9f79-e17f6018e401`
- Workspace: `/home/varunkamat/dev/ai-infra/agent-bridge`
- Raw source: `/home/varunkamat/.codex/sessions/2026/08/11/rollout-2026-08-11T10-07-04-019ff1ca-c220-7152-9f79-e17f6018e401.jsonl`
- Exported messages: 5

## User — 2026-08-11T17:07:29.278Z

> Historical context: this recovered discussion describes the retired two-party HTTP mailbox. The
> production path is now NATS JetStream with capability-addressed runners; see
> `docs/abb-simulator-e2e.md`. File links and statements about the then-current implementation below
> are intentionally preserved as design history and are not operational instructions.

Re this agent-bridge repo: Now I wanna think about how to organize this better the specific way this repose laid out is for a very niche application of a server-Client application where the adapter was ubuntu and the client was on wondows. so that’s why we have the ubuntu-windows naming and odd constraints, it’s explicitly designed for that paradigm. 

Really what I’m trying to build is not just a single two-way street between two agents but also some sort of infrastructure that can allow different networks of different topologies of agents to be connected. For example I might want to have multiple agents running on my work computer doing various dev task and some of them may be communicating with an adapter that controls a robot to send test over to that agent to run then the test results would be sent back to a receiving agent on my home laptop, and those results would be distributed to various agents to implement, etc. I want to have something that is efficient and not wasteful. Basically I currently tell my Codex agents: hey, every time you send a message use the polling infrastructure the wait for a bridge message script and run that in the foreground which basically hangs until it receives a message and then it returns immediately with the message so that kind of works basically for agents to be on standby but I can imagine you could get into weird deadlock situations and it would be brittle if you have lots of agents doing this concurrently at once.

I want to have a system that’s somewhat more reliable than that but also doesn’t require making some super complicated industrial grade server type distributed system network. If it’s a little bit fallible it’s OK it doesn’t need to be production grade. It just needs to be good enough for me to handle my Dev needs efficiently without having to step in too much when there is occasional hiccups. 

There are definitely some different network topologies we can consider: we could have one centralized coordinator that routes everything, or we could have multiple routers in a distributed network, or we could just have agents pass messages directly to each other, etc. Can you research and try to find the best options?

I don’t know how to handle the dumping of payloads right now. It’s basically queued in a message, but maybe it would make more sense to store those somewhere instead of just having that message to be hanging on a HTTP POST or dumped into a temp file. I guess we have that messages temp file that they’re dumped to currently, but that feels perhaps not organizing nice enough for a distributed network. 

Also, can you look into what tools exist for good communication? sometimes my experience is that the existing tools are not that great because AI is so new and I feel like this is something that we could probably make ourselves pretty easily but I’m just curious what would be good tools out there for what I’m looking for.


## Assistant — 2026-08-11T17:07:33.076Z

I’ll first map the current bridge’s actual protocol, persistence, and process model, then compare a few lightweight messaging/topology options against your concrete multi-machine agent workflows. I’ll keep the recommendation proportional: reliable enough to self-heal and avoid deadlocks, without turning this into a distributed-systems project.

## Assistant — 2026-08-11T17:07:50.381Z

The current code confirms the core mismatch: it is an append-only two-party mailbox, not yet a queue. Every read rescans the whole JSONL file, messages are never acknowledged or claimed, all consumers for a recipient see the same entries, and identity is hard-coded to `ubuntu|windows`. Long polling is also implemented in each client rather than at the server, so concurrency will amplify both polling and ambiguity about who owns work.

## Assistant — 2026-08-11T17:09:08.905Z

The design center I’m converging on is a useful middle ground: one durable broker, but no mandatory “master agent.” That keeps routing and recovery centralized while leaving workflow decisions decentralized. It gives you direct agent-to-agent messaging, one-of-many worker dispatch, and broadcast/event fan-out from the same primitive—and it leaves a clean path to edge routers later if the robot network needs offline operation.

## Assistant — 2026-08-11T17:10:13.627Z

The best fit is a centralized durable broker without a centralized coordinator. Concretely: use a single NATS server with JetStream, reachable through Tailscale, and keep orchestration decisions in the agents or optional workflow components.

That gives you the simplicity of one rendezvous point without forcing every conversation through a “master agent.”

## What the current bridge really is

The repository is a good proof of concept, but several assumptions prevent it from becoming a general agent network:

- Identities are fixed to `ubuntu` and `windows` in [bridge_server.py](/home/varunkamat/dev/ai-infra/agent-bridge/bridge_server.py:19), the Bash client, and the PowerShell client.
- A message is appended durably, but delivery is not tracked. There is no claim, acknowledgment, lease, retry, or dead-letter state.
- Every receive operation scans the entire JSONL history from the beginning in [bridge_server.py](/home/varunkamat/dev/ai-infra/agent-bridge/bridge_server.py:87).
- Two agents using the same recipient name receive the same messages; there is no distinction between “send to all” and “one available worker should take this.”
- Cursors are maintained manually by the caller, as documented in [README.md](/home/varunkamat/dev/ai-infra/agent-bridge/README.md:57).
- Both wait scripts poll every two seconds. The foreground wait is not inherently bad, but the repeated HTTP and ambiguous consumption semantics are.
- A single bearer token grants access to everything.
- The body is one text field, so task instructions, structured results, and large artifacts are conflated.

The current five tests pass. These are architectural limitations rather than existing test failures.

## Recommended topology

```text
 Work laptop                           Robot machine
 ┌────────────────────┐               ┌────────────────────┐
 │ dev-agent-a        │               │ robot adapter      │
 │ dev-agent-b        │               │ test runner        │
 │ local bridge CLI   │               │ local bridge CLI   │
 └─────────┬──────────┘               └─────────┬──────────┘
           │                                      │
           └──────────────┐       ┌───────────────┘
                          ▼       ▼
                 ┌───────────────────────┐
                 │ NATS + JetStream      │
                 │ durable messages      │
                 │ consumers and leases  │
                 │ artifact bucket       │
                 └───────────┬───────────┘
                             │
                  ┌──────────▼──────────┐
                  │ Home laptop         │
                  │ result agent        │
                  │ implementation pool │
                  └─────────────────────┘
```

This is centralized routing, but decentralized orchestration:

- Agents can address another specific agent.
- Tasks can target a capability such as `robot-test` or `implementation`, allowing one available agent from a pool to claim them.
- Status events can be broadcast to several interested agents.
- A coordinator can be added for a particular workflow without becoming mandatory infrastructure.
- The broker stores work while recipients are offline.

NATS subjects naturally model this:

```text
ab.v1.task.agent.<agent-id>
ab.v1.task.capability.<capability>
ab.v1.event.workflow.<workflow-id>
ab.v1.result.agent.<agent-id>
```

A durable consumer per named agent handles direct mail. A shared durable consumer per capability distributes work to exactly one available worker. Separate consumers can subscribe to status/event subjects for fan-out.

JetStream persists and replays messages, tracks acknowledgments, redelivers unacknowledged messages, supports bounded retention, and recommends pull consumers for new projects where flow control and error handling matter. [NATS JetStream overview](https://docs.nats.io/nats-concepts/jetstream), [consumer documentation](https://docs.nats.io/nats-concepts/jetstream/consumers).

## Foreground waiting is okay

A command like this can remain part of the agent instructions:

```bash
agent-bridge receive --agent dev-a --timeout 300
```

The important change is its implementation:

- It performs a broker-side blocking pull rather than polling a JSONL-backed HTTP endpoint.
- Receiving creates a lease.
- The agent explicitly acknowledges completion.
- If the agent crashes, the lease expires and the task is redelivered.
- Every wait is bounded so the agent can reassess workflow state.
- Heartbeats extend leases for long-running robot tests.
- Duplicate delivery is expected, so handlers use the task ID as an idempotency key.

A broker prevents lost or multiply claimed work. It cannot prevent logical deadlocks—for example, agent A waiting for B while B waits for A. Address those with task deadlines, terminal states, cancellation, and a `bridge inspect workflow <id>` command showing outstanding dependencies.

Use states such as:

```text
pending → leased → running → succeeded
                    ├──────→ failed
                    ├──────→ canceled
                    └──────→ expired/retry
```

## Payloads and artifacts

Keep the message envelope small and structured:

```json
{
  "schema": "agent-bridge/v1",
  "message_id": "019...",
  "workflow_id": "wf-...",
  "task_id": "task-...",
  "parent_id": "task-...",
  "sender": "work.dev-a",
  "destination": "capability.robot-test",
  "type": "task.requested",
  "created_at": "...",
  "expires_at": "...",
  "reply_to": "agent.home.results",
  "body": {
    "instructions": "Run the ABB simulation test",
    "parameters": {}
  },
  "artifacts": []
}
```

Use two storage paths:

- Inline: instructions, summaries, small JSON results, exit codes, and diagnostics.
- Artifact reference: logs, screenshots, test archives, patches, and other larger files.

An artifact reference should contain at least:

```json
{
  "name": "robot-test-results.json",
  "uri": "nats-object://artifacts/sha256-...",
  "media_type": "application/json",
  "size": 184231,
  "sha256": "..."
}
```

JetStream includes an object store that chunks files and transfers them through the same infrastructure. It is convenient for moderate development artifacts, although NATS explicitly notes that it is not a general distributed filesystem. [NATS Object Store](https://docs.nats.io/nats-concepts/jetstream/obj_store).

For source changes, prefer a Git commit or patch reference over copying an entire repository. For very large or long-lived assets, add S3/MinIO later. Avoid temporary paths unless the producer and consumer are guaranteed to share a filesystem.

## Tool comparison

| Option | Fit | Assessment |
|---|---|---|
| **NATS + JetStream** | Routing, work queues, fan-out, replay, request/reply, edge expansion, artifacts | Best overall fit. Small cross-platform server, relatively light operational burden. |
| **Redis Streams** | Durable ordered log and competing consumers | Strong runner-up, especially if you already run Redis. Consumer groups provide acknowledgment, pending-item inspection, blocking reads, and reassignment. Persistence configuration needs care. [Redis Streams](https://redis.io/docs/latest/develop/data-types/streams/) |
| **RabbitMQ** | Traditional reliable queues and sophisticated routing | Very capable, but more broker administration than this project appears to need. Its acknowledgment and quorum-queue behavior are excellent when production-grade HA is required. [RabbitMQ reliability](https://www.rabbitmq.com/docs/reliability) |
| **MQTT/Mosquitto** | Robot and device telemetry | Worth considering if the robot ecosystem already uses MQTT. Less natural as the primary agent-task lifecycle and replay system. |
| **Direct HTTP/webhooks** | A few stable peers | Simple initially, but every node needs discovery, reachability, retry storage, authentication, and failure handling. Complexity moves into your code. |
| **Custom SQLite queue** | Minimal single-host or single-server deployment | Viable fallback. Better than JSONL, but you would still be implementing leases, acknowledgment, redelivery, notification, retention, and authorization yourself. |
| **Kafka/Temporal** | High-scale event processing or durable workflow execution | Excessive for the scale and fallibility tolerance you described. |

NATS also has leaf nodes for edge networks and intermittent connectivity. A local robot-side broker can later continue operating while disconnected and synchronize persistent streams with the hub. I would deliberately postpone this until a single broker proves insufficient. [NATS leaf nodes](https://docs.nats.io/running-a-nats-service/configuration/leafnodes), [JetStream on leaf nodes](https://docs.nats.io/running-a-nats-service/configuration/leafnodes/jetstream_leafnodes).

For connectivity, Tailscale is a practical complement: machines get encrypted WireGuard connectivity with NAT traversal, generally without exposing the broker publicly. [Tailscale networking documentation](https://tailscale.com/docs/concepts/control-data-planes). Still give each node its own NATS credentials and restrict which subjects it can publish or consume; do not retain one shared global bearer token.

## Where A2A and MCP fit

Neither replaces the broker:

- A2A is an agent interaction protocol: agent discovery, tasks, status, artifacts, streaming, and asynchronous notifications. Its vocabulary is useful, but adopting its entire protocol now would add considerable surface area. Borrow `Task`, `Message`, `Artifact`, task states, and capability discovery first. [A2A specification](https://github.com/a2aproject/A2A/blob/main/docs/specification.md)
- MCP is primarily how an agent host accesses tools, resources, and prompts through client-server sessions. An MCP server could expose `bridge_send`, `bridge_receive`, `bridge_ack`, and `bridge_status` to Codex or Claude, but MCP itself is not your durable multi-agent message bus. [MCP architecture](https://modelcontextprotocol.io/specification/2025-06-18/architecture)

Thus the clean layering is:

```text
Codex/Claude integration: MCP tool or CLI
Agent semantics: small A2A-inspired envelope
Delivery and recovery: NATS JetStream
Machine connectivity: Tailscale
Large payloads: NATS Object Store, Git, or later S3
```

## Repository reorganization

I would evolve this into:

```text
agent-bridge/
├── src/agent_bridge/
│   ├── cli.py
│   ├── envelope.py
│   ├── identity.py
│   ├── artifacts.py
│   ├── consumers.py
│   └── transports/
│       ├── base.py
│       ├── nats.py
│       └── legacy_http.py
├── adapters/
│   └── abb_robotstudio/
│       └── run_e2e.ps1
├── clients/
│   ├── powershell/
│   └── shell/
├── deploy/
│   ├── nats/
│   ├── systemd/
│   └── windows-service/
├── docs/
│   ├── architecture.md
│   ├── protocol.md
│   ├── reliability.md
│   └── operations.md
└── tests/
    ├── unit/
    ├── integration/
    └── e2e/
```

The ABB helper becomes one adapter rather than defining the architecture. “Ubuntu” and “Windows” become node metadata; stable identities look like `work.dev-a`, `robot.test-runner`, and `home.results`.

## Suggested implementation order

1. Introduce general agent/node identities and the versioned envelope.
2. Add the NATS transport while preserving the current HTTP transport as `legacy_http`.
3. Implement `send`, blocking `receive`, `ack`, `nack`, `heartbeat`, and `inspect`.
4. Add direct inboxes, capability queues, and workflow event subjects.
5. Move large results into artifact references.
6. Add per-node credentials, subject permissions, retention limits, retry limits, and a dead-letter subject.
7. Only then consider local relay daemons, SQLite outboxes, or NATS leaf nodes for disconnected operation.

The key architectural decision is not “centralized versus distributed.” It is separating the durable broker from the optional coordinator. A single NATS broker gives you the reliable center you need now, while subject-based routing keeps the agent network flexible enough to grow into the topologies you described.
