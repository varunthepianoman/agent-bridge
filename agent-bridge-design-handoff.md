# Agent Bridge design handoff

Updated: 2026-08-11

This document preserves the useful context and conclusions from the design discussion so the work can continue after chat compaction. It is a design handoff, not yet an implementation specification. Several conclusions below are deliberately provisional pending inspection of the sibling AIWK project.

## User's underlying goal

The repository began as a narrow Ubuntu-to-Windows bridge for an ABB/RobotStudio workflow. The desired system is broader: lightweight infrastructure through which coding agents, robot-test adapters, result reviewers, and implementation agents on several machines can communicate and hand off work without constant human intervention.

Example topology:

- Several development agents on a work computer.
- A robot-side adapter or test runner that receives test requests.
- A results agent on a home laptop.
- Further agents that diagnose results or implement fixes.
- Machines may be offline temporarily.
- The system should be reliable enough for personal development work without becoming an industrial distributed-systems project.

The user tolerates occasional fallibility but wants the system to recover from ordinary hiccups without frequent manual intervention.

## Current repository limitations found in the recovered discussion

The original bridge was characterized as an append-only two-party mailbox rather than a true queue:

- Identities were hard-coded around `ubuntu` and `windows`.
- Messages were appended to JSONL, but delivery was not claimed or acknowledged.
- There was no lease, retry, dead-letter, or cancellation state.
- Reads rescanned message history.
- Multiple consumers for one recipient could all see the same messages.
- Broadcast and one-of-many work dispatch were not distinguished.
- Callers managed cursors manually.
- Wait scripts repeatedly polled and occupied the foreground.
- One shared bearer token granted broad access.
- Structured instructions, results, and large artifacts were conflated into one text body.

The exact ABB documentation path the user initially referenced was not present in this repository's `main` branch:

`t_robotics/t_core/ros/abb/abb_arci_adapter/abb_arci_adapter/docs/rws_io_test_setup.md`

A recovered earlier design transcript is present at `recovered-design-multi-agent-bridge.md`.

## Recommended delivery architecture

The design direction was one NATS server with JetStream, reachable over a private network such as Tailscale.

Terminology:

- NATS is the message broker and subject-based router. It is unrelated to network address translation (NAT).
- Core NATS is fast live publish/subscribe and request/reply.
- JetStream adds stored messages, durable consumers, acknowledgments, redelivery, retention, replay, and object storage.

The important architectural split is between the durable broker and any optional workflow coordinator:

- The broker answers mechanical delivery questions: storage, routing, claims, acknowledgment, retry, and fan-out.
- A coordinator answers semantic workflow questions: what should run next, whether results satisfy an objective, and when a decision is required.
- A single broker can be physically centralized while agent decisions remain logically decentralized.
- A coordinator can be added for a particular workflow without becoming mandatory message infrastructure.

Possible subject families:

```text
ab.v1.task.agent.<agent-id>
ab.v1.task.capability.<capability>
ab.v1.result.agent.<agent-id>
ab.v1.event.workflow.<workflow-id>
```

Intended routing semantics:

- A durable consumer for a stable agent identity provides direct mail.
- A shared durable pull consumer for a capability distributes a task to one eligible worker.
- Independent consumers of workflow events provide broadcast/fan-out.
- Pull consumers should use explicit acknowledgments and bounded waits.

JetStream normally gives at-least-once delivery, so handlers must expect duplicates and use stable task/message IDs as idempotency keys.

## Waiting and waking agents

An AI turn should not remain alive merely to poll or block on incoming work. The long-lived component should be a small local runner or daemon:

```text
NATS/JetStream blocking pull
        -> local bridge runner wakes
        -> runner starts or resumes Codex
        -> Codex handles a task and ends its turn
```

The runner can be a systemd service on Linux or an equivalent background service on Windows. It blocks efficiently at the broker rather than consuming an active model turn.

Delivery lifecycle:

```text
pending -> leased -> running -> succeeded
                    |-> failed
                    |-> canceled
                    |-> expired/retry
```

The runner should:

- Claim a message through a durable consumer.
- Persist the task ID and attempt number locally before invoking an agent.
- Extend the acknowledgment deadline while long work is progressing.
- Publish the result before acknowledging the input task.
- Leave the message unacknowledged if the process or machine crashes, allowing redelivery.
- Deduplicate repeated delivery by logical task ID.

NATS cannot wake a terminated model turn by itself. It wakes the local runner, which invokes Codex.

## Codex integration options

Three stages were identified:

1. `codex exec --json` for a simple subprocess-based MVP.
2. Codex SDK (Python or TypeScript) for programmatic thread start/resume and automation.
3. Codex App Server for a rich client with streamed events, approvals, conversation history, and steering.

Useful Codex surfaces:

- `codex exec`: non-interactive runs, machine-readable JSONL events, resumable sessions, and schema-constrained final output.
- Codex SDK: start and resume threads from application code; likely the best long-term local-runner interface.
- Codex App Server: bidirectional JSON-RPC over stdio, WebSocket, or Unix socket; supports thread and turn lifecycle, streaming agent/tool/file events, approvals, user input, steering, and interruption.
- Codex as MCP server: useful if a broader orchestrator treats Codex as one specialist.
- Hooks: useful for lifecycle audit, policy, notification, and context injection, but not the primary durable message bus.
- Skills/plugins: reusable procedures and tool integrations.
- GitHub Action: Codex integration in CI.

App Server should normally run locally on each machine that owns the relevant repository, filesystem permissions, credentials, and approvals. The bridge runner translates between Agent Bridge tasks and local Codex threads/turns. Agent Bridge should not expose App Server's experimental wire protocol as its own permanent public protocol.

## Message and artifact model

Messages should be small, structured envelopes. Suggested correlation fields:

```json
{
  "schema": "agent-bridge/v1",
  "message_id": "msg-...",
  "workflow_id": "wf-...",
  "task_id": "task-...",
  "parent_task_id": "task-...",
  "causation_id": "evt-...",
  "sender": "work.dev-a",
  "destination": "capability.robot-test",
  "type": "task.requested",
  "created_at": "...",
  "expires_at": "...",
  "reply_to": "agent.home.results",
  "attempt": 1,
  "body": {},
  "artifacts": []
}
```

Inline content should include instructions, parameters, summaries, diagnostics, exit codes, and small structured results.

Large logs, screenshots, archives, patches, and test output should be referenced as artifacts. JetStream Object Store is convenient for moderate development artifacts; Git commits or patches are preferable for source changes; S3/MinIO could be added for large or long-lived objects.

Artifact references should include name, URI, media type, size, and checksum.

## Observability design

Observability needs three distinct views.

### Workflow observability

Answers what was requested, what is running, what depends on what, what failed, where results are, and why something is stuck.

Useful immutable domain events:

```text
workflow.created
task.queued
task.leased
task.started
task.progressed
task.succeeded
task.failed
task.retry_scheduled
task.canceled
artifact.created
approval.requested
approval.resolved
```

Every event should carry `event_id`, `workflow_id`, `task_id`, parent/causation IDs, agent/node identity, Codex thread/turn IDs when applicable, attempt number, and timestamp.

### Agent observability

Answers whether a runner is online, what it is doing, whether it is waiting for approval, whether progress has stalled, commands/files involved, duration, and token usage.

Codex App Server or `codex exec --json` provides detailed events. Retain compact summaries longer than high-volume deltas, command chunks, and low-level tool events.

### Infrastructure observability

Answers broker health, connected clients, consumer lag, pending work, redelivery, slow consumers, stream size, and artifact storage use.

NATS exposes `/healthz`, `/varz`, `/connz`, `/subsz`, `/jsz`, and `/leafz`, plus JetStream advisories. Its HTTP monitoring port has no built-in authentication and should remain on localhost or a protected monitoring network.

Prometheus/Grafana may later cover metrics and alerts, but should complement rather than replace the task-oriented UI.

### Query projection

JetStream should not be queried directly for every UI screen. A small idempotent projector/indexer should consume workflow events into SQLite (initially, using WAL mode) with tables such as:

```text
workflows
tasks
task_dependencies
task_attempts
agents
agent_sessions
artifacts
approvals
events
```

The event stream is durable history; SQLite is the read/query model. SSE or WebSockets can deliver live updates to the UI.

## UI discussion and the user's strong preference

cmux was investigated and rejected as the primary experience. It is a polished macOS terminal control room with panes, workspaces, notifications, session restoration, a programmable browser, and a CLI/socket API. It can complement a bridge, but its process/pane model does not provide durable queued tasks, acknowledgments, leases, retries, capability routing, workflow dependencies, artifact tracking, or cross-machine event history.

More importantly, the user explicitly dislikes multitasking and does not want an interface centered on many terminal sessions or agents needing babysitting. The desired experience is focused, organized, and procedural:

- Human attention should be on one foreground objective at a time.
- System concurrency may exist underneath without becoming the user's multitasking burden.
- Other work may be queued or running unattended, but it should remain one abstraction level away.
- The UI should not be a list of random terminals, chat panes, or agents demanding attention.
- Failures should usually trigger automatic retry/recovery rather than notifications.
- Interruptions should be reserved for material decisions, authorization, genuine blockers, completion review, deadlines, or resource limits.
- Raw events/logs should be available through progressive disclosure, not shown by default.

A proposed focus screen showed one objective, a procedural sequence of phases, current activity, evidence, and a quiet aggregate of other work. Proposed primary navigation was Focus, Queue, Review, History, and System. Agent identities, messages, and terminals belong in advanced details or System rather than primary navigation.

The earlier suggested personal state model was:

```text
Inbox -> Ready -> Focused -> Review -> Done
                    ^
              human WIP limit 1
```

The earlier suggestion also described linearized phases such as Reproduce, Diagnose, Implement, Verify, and Review, with parallel internal subtasks collapsed by default. A graph visualizer could show task dependencies on demand, but should not graph every NATS message.

## Important new correction to investigate

The user has now explained that the sibling AIWK project is intended to solve the specification, gating, and organization problem. Agent Bridge is intended to solve orchestration and communication.

The relationship may be:

- AIWK: structured specifications, gates, checkpoints, organized execution, and workflows that benefit from rigor.
- Agent Bridge: durable communication, routing, launching agents, flexible exploration, retry/recovery, and organically expanding or contracting work.

The user does not always want an AIWK-style workflow. For some work, a more autonomous system should explore, launch agents, retry failures, and expand scope flexibly. There is concern that embedding too much procedure and phase structure into Agent Bridge would duplicate AIWK and make Agent Bridge insufficiently adaptable.

Therefore, the earlier procedural UI/state proposal must be reassessed after reading AIWK. Do not prematurely make fixed phases, gates, or a mandatory coordinator part of Agent Bridge's core protocol.

Likely design principle to test:

```text
Agent Bridge core = flexible event/message substrate and runtime state
AIWK integration  = optional structured workflow policy layered on top
Ad hoc autonomy   = optional dynamic task graph grown by agents at runtime
```

The underlying graph may need to be dynamic rather than declared in advance:

- Agents can create, split, merge, supersede, cancel, or re-scope tasks.
- A workflow can begin as one objective and acquire structure only as evidence demands.
- The UI can still present a coherent focused narrative without forcing a fixed lifecycle.
- Gates and procedures should be policy supplied by AIWK or a workflow definition, not hard-coded delivery semantics.

## Questions for the AIWK investigation

1. What are AIWK's actual concepts, files, commands, state machine, gates, and sources of truth?
2. Does AIWK already model task dependencies, phases, reviews, artifacts, or agent assignments?
3. Where is AIWK tightly coupled to one local repository or filesystem?
4. What integration surface would allow AIWK to publish work to Agent Bridge and consume results?
5. Which fields belong in the neutral Agent Bridge envelope versus an AIWK-specific payload?
6. Should Agent Bridge call an optional policy/coordinator plugin, or should AIWK itself act as an ordinary bridge participant?
7. How should autonomous task-graph mutations be represented: task-created, task-split, dependency-added, scope-revised, task-superseded, and similar events?
8. How can the UI maintain one coherent foreground objective while the graph underneath changes organically?
9. Which states are universal transport/runtime states, and which are AIWK workflow states that must remain out of the core?

## Provisional boundary to preserve

Universal delivery/runtime concepts likely belong in Agent Bridge:

- Agent/node identity and capabilities.
- Message/task IDs and correlation/causation.
- Subjects and routing.
- Durable storage.
- Claim/lease/acknowledgment/redelivery.
- Attempts, deadlines, cancellation, and terminal delivery outcomes.
- Artifact references.
- Agent wake/start/resume integration.
- Runtime events and query projections.
- Authorization and subject permissions.

Potentially workflow-specific concepts that should not be hard-coded before examining AIWK:

- Fixed phase names.
- Mandatory linear procedures.
- Required specification documents.
- Review gates.
- Acceptance criteria format.
- Coordinator decisions about what work should exist.
- A universal definition of workflow completion beyond observable task state.
- Human WIP policy, even if the UI defaults to one focused objective.

## Historical implementation order (completed and superseded)

This handoff records the design discussion that preceded the current implementation. References
below to retaining the legacy HTTP bridge describe a transitional plan, not a supported runtime.
The mailbox and its polling clients were removed in Milestone 9; use the durable Manual Bridge and
capability runners documented in `docs/manual-bridge.md` and `docs/abb-simulator-e2e.md`.

1. Generalize node and agent identities.
2. Define a small versioned neutral envelope.
3. Add a NATS/JetStream transport while retaining legacy HTTP temporarily.
4. Implement send, blocking receive, ack/nack, heartbeat/in-progress, cancellation, and inspect.
5. Add direct inboxes, capability queues, and workflow/event subjects.
6. Add artifact references and storage adapters.
7. Add per-node credentials, permissions, retry limits, retention, and dead-letter handling.
8. Add a query projection and focused UI.
9. Add AIWK as an optional structured workflow integration rather than baking its policy into the bridge.
10. Consider leaf nodes, relay daemons, or offline outboxes only after a single broker proves insufficient.

## Key design tension for the next step

The system must support both:

```text
Structured work
  specification -> gates -> controlled execution -> review

Emergent work
  objective -> exploration -> dynamic delegation/re-scoping -> useful result
```

The probable solution is not choosing one mode globally. It is separating mechanism from policy:

- NATS/JetStream and the runner provide durable execution mechanisms.
- The Agent Bridge protocol records an evolving graph of work without prescribing its shape.
- AIWK can impose a rigorous graph and gates when appropriate.
- An autonomous coordinator or agents can grow and revise the graph dynamically when exploration is appropriate.
- The UI provides a focused narrative and explicit decisions regardless of which policy produced the graph.

## AIWK investigation findings

The sibling project was inspected at `/home/varunkamat/dev/ai-infra/aiwk/aiwk`. Its local `main` is two commits ahead of `origin/main`; the latest local commit adds `docs/codex_renderer_plan.md`.

AIWK describes itself as a Python CLI for durable, reviewable AI coding workflows around an existing Git repository. It keeps intent, invariants, objective checks, execution state, and handoffs outside chat, then renders a provider-specific execution artifact.

### What AIWK actually owns today

Durable author-edited authority:

```text
aiwk.yaml
workflow.yaml
spec/project.spec.md
spec/invariants.yaml
spec/gates.yaml
```

Operational/generated artifacts:

```text
generated/<project>.claude_workflow.js
master_coordinator_prompt.md
state/gates/*.json
logs/gates/*.log
state/handoffs/*.md
state/<phase>_context.json
state/<phase>_handoff.md
```

AIWK's current provider-neutral schema has fixed supported phases:

```text
scope, discovery, dev, redteam, review, commit
```

It models stages and ordered steps, objective gates, commit policies, optional Discovery, optional narrow code review, optional parallel red-team lenses, bounded checkpoint continuations, durable handoffs, and explicit intra-step restart points.

Normal generated control flow is deliberately bounded:

```text
Scope
  -> optional Discovery
  -> Developer
  -> optional code-review filter
  -> Red Team or parallel red-team fan
  -> Objective Gate
  -> Reviewer
  -> bounded Developer Fix / Gate / Reviewer attempts
  -> Commit policy
  -> clean-status enforcement
```

The generated workflow owns role order and bounded loops. The master coordinator prompt is explicitly thin and is told not to improvise substitute role loops or expand scope. AIWK does not currently launch Claude itself; it renders a workflow artifact and launch runbook for an external Claude Workflow runtime.

AIWK is therefore already the structured specification/gating/policy orchestrator. It is not currently:

- a durable multi-machine message broker;
- an always-on daemon;
- an agent discovery or capability-routing service;
- an offline queue;
- a general dynamic work graph;
- a cross-project task dashboard;
- a wake-up service for stopped Codex sessions.

### Important proposed Codex overlap

The local, proposed `docs/codex_renderer_plan.md` says "SDK-first, with AIWK owning orchestration." It proposes generated AIWK code that directly starts/resumes Codex threads, orders roles, handles parallel fan-out, validates structured output, persists runtime state, invokes gates, and owns bounded retries.

This does not invalidate Agent Bridge, but the word "orchestration" must be split into two layers:

```text
AIWK orchestration
  policy: which role runs next, gates, bounded fix/review loops,
  acceptance, commit rules, durable software-delivery meaning

Agent Bridge orchestration
  execution: where work runs, durable delivery, wake-up, leases,
  retries of failed execution attempts, routing, communication,
  machine/agent availability, and result transport
```

For a structured AIWK run, Agent Bridge must not reinterpret gate results or choose AIWK's next role. AIWK should remain the policy owner and use Agent Bridge as an execution backend.

The clean integration seam is an executor adapter. Instead of every generated AIWK runtime directly owning local Codex SDK processes, AIWK can submit a role invocation to Agent Bridge with an AIWK-specific extension payload and wait for a durable result. The bridge handles placement and execution reliability; AIWK handles semantic routing.

Possible correlation extension:

```json
{
  "policy_owner": "aiwk",
  "external_ref": {
    "project": "...",
    "stage": "build",
    "step": "STEP_SS0",
    "role": "redteam",
    "cycle": 1,
    "attempt": 1,
    "workflow_fingerprint": "sha256:..."
  }
}
```

These fields should live in an AIWK extension, not become mandatory Agent Bridge concepts.

### Refined Agent Bridge core

Agent Bridge should not hard-code AIWK's fixed phases or invent a competing universal procedure. Its stable model should be smaller:

- **Objective:** the durable focus anchor and evolving intent.
- **Work item:** a unit of requested work that may be created dynamically.
- **Execution attempt:** one leased run of a work item on a runner/agent.
- **Event:** an immutable fact about creation, routing, execution, or graph change.
- **Artifact:** referenced evidence or output.
- **Decision:** an explicit human or policy choice when needed.
- **Typed relationship:** `depends_on`, `decomposes`, `produced`, `supersedes`, `blocks`, or `relates_to`.

Universal runtime state can remain minimal:

```text
proposed -> ready -> leased -> running -> completed
                              |-> failed/retryable
                              |-> waiting
                              |-> canceled
                              |-> superseded
```

`completed` means the execution reported an outcome; it does not mean an AIWK gate accepted the software change. Semantic acceptance belongs to the active policy owner.

### Organic graph expansion and contraction

Exploratory/autonomous mode should allow runtime graph mutations rather than require a declared phase list:

```text
work.created
work.split
dependency.added
dependency.removed
work.merged
work.superseded
work.canceled
objective.scope_revised
objective.summary_updated
```

Expansion examples:

- An investigation discovers an independent robot-side failure and creates a child diagnostic task.
- A broad code task splits into repository mapping, experiment, and implementation tasks.
- An agent launches several read-only critics against different hypotheses.

Contraction examples:

- Competing hypotheses are refuted and their branches are canceled.
- Several findings are merged into one implementation task.
- A newer task supersedes an obsolete approach.
- Completed branches are summarized and collapsed from the foreground view.

Autonomy should be bounded by budgets and permissions instead of fixed procedures:

- allowed repositories, machines, capabilities, and external side effects;
- maximum graph depth, child count, and concurrent executions;
- maximum retries, elapsed time, tokens/cost, and artifact size;
- one-writer-per-worktree constraint;
- which scope changes are automatic versus require a decision;
- cancellation and deadline policy.

This preserves adaptability without allowing uncontrolled expansion.

### Three compatible operating policies

Agent Bridge can support three modes over the same transport/runtime primitives:

```text
direct
  one requested work item, one result

adaptive
  an objective controller or agents dynamically grow and contract work

aiwk
  AIWK supplies the declared stages, gates, role order, and acceptance policy
```

These should be policy choices, not separate message systems. A run may cross boundaries deliberately: an AIWK Discovery role can delegate an adaptive investigation, and an exploratory objective can later be promoted into an AIWK project when its scope becomes clear or risk warrants formal gates.

### Refined focused UI principle

The user's desire for an organized, single-focus UI does not require a fixed workflow schema. The UI can project either structured or dynamic work into one stable narrative:

```text
Objective
Current action
Why this action exists
Evidence learned
What changed in scope
Likely next action
Decision required, if any
```

In AIWK mode, the detailed view can render AIWK stages, steps, gates, and reviews. In adaptive mode, it can render dynamically named branches and collapse completed/refuted branches. Procedure is therefore a view supplied by policy, not a mandatory transport-level state machine.

## Revised product center: an AI-work registry and communication fabric

The user clarified that AIWK is not a global blueprint and may not participate at all. Work may use completely different topologies. Agent Bridge must remain topology-neutral and should not assume that every objective has an external coordinator, declared task graph, AIWK project, or even more than one chat.

The user's current working pattern is already reasonably effective:

- a large stack of approximately 17 ARCI-V2 PRs;
- usually two to four PRs active at once;
- roughly two VS Code windows with several Codex agents running and fixing things;
- switching tabs only when an agent actually needs help;
- relying on each Codex chat's existing UI and strong end-of-turn summary to understand that individual agent.

The major pain is not fine-grained visibility inside one model process. It is global organization and recovery across conversations:

- a chat was opened in a different window and is difficult to reopen;
- a chat is associated with another folder, worktree, dev container, or machine;
- related discussions are scattered across several threads;
- the correct thread for one topic cannot be found;
- multiple exploratory question threads require manual memorable renaming;
- the stock Codex/Claude history is a low-level, folder-sensitive list rather than an organized view of ongoing AI work.

Therefore the primary product should be understood as:

```text
durable, global AI-work registry
  + conversation/session recovery
  + cross-agent communication
  + optional durable execution wake/restart
  + flexible relationship/topology modeling
```

It should not begin as:

```text
workflow engine
  + replacement chat client
  + terminal multiplexer
  + full trace viewer for every tool call
```

### Treat native agent runtimes as capable orchestration islands

Current Codex can spawn subagents, run them in parallel, expose their threads, and collect summaries in the parent. Claude can provide similar provider-native collaboration capabilities. These runtimes will likely become more capable over time.

Agent Bridge should therefore avoid rebuilding intra-session orchestration that the active model can already handle. A root Codex or Claude conversation can be treated as an orchestration island:

```text
Agent Bridge sees
  root conversation
  provider/runtime identity
  parent/descendant session relationships
  high-level state and summary
  external messages and durable handoffs

Native runtime owns
  when to create local subagents
  internal prompts and delegation
  local tool use
  collection of subagent results
  detailed per-chat interaction UI
```

Agent Bridge becomes important at the boundary between islands: another machine, provider, repository, worktree, dev container, server/client test endpoint, or independently started chat.

### Communication patterns that must remain flexible

The bridge should support at least:

- simple human-to-one-chat messaging;
- direct chat-to-chat messages;
- request/reply across a server/client or robot-test boundary;
- one agent sending a durable result to a different machine;
- capability queue dispatch to one eligible worker;
- broadcast/fan-out where several independent agents should inspect something;
- peer collaboration, such as planner and auditor exchanging revisions until both report agreement;
- provider-native subagent trees represented as one imported conversation family;
- AIWK-controlled structured execution;
- ad hoc topologies with no coordinator;
- optional coordinator-mediated topologies;
- dynamic graphs created by agents at runtime.

No one of these should be the canonical global topology.

### Conversation identity must be independent of location

The core missing abstraction is a stable bridge-level conversation/work identity that is not derived from the current folder or UI window.

Suggested record:

```json
{
  "conversation_id": "conv-...",
  "provider": "codex",
  "provider_thread_id": "thr-...",
  "title": "ARCI-V2 PR 07 reconnect audit",
  "objective_id": "obj-arci-v2",
  "project_id": "arci-v2",
  "repo_identity": "github:org/repo",
  "worktree": "/machine/path/to/worktree",
  "branch": "feature/...",
  "pr": 7,
  "machine_id": "work-laptop",
  "environment_id": "devcontainer-...",
  "parent_conversation_id": null,
  "status": "active",
  "last_summary": "...",
  "tags": ["transport", "reconnect"],
  "created_at": "...",
  "last_activity_at": "..."
}
```

Paths are location metadata, not identity. The same repository/work item may have different paths on Linux, Windows, a dev container, and a home laptop.

Provider thread IDs remain authoritative for resumption, while bridge IDs organize across providers and environments.

### Index first; instrument selectively

For the first useful product, ingest high-value session metadata rather than exhaustive traces:

- provider and thread/session ID;
- title and preview;
- status: idle, active, waiting for approval, failed, archived;
- CWD, repository, branch, commit, PR, worktree, machine, and environment;
- parent/descendant relationships for native subagents;
- created/updated time;
- final or latest summary;
- user tags, grouping, pinning, and notes;
- durable resume locator and availability;
- messages/handoffs sent or received through Agent Bridge.

Detailed tool events, command logs, token metrics, and live deltas should be opt-in diagnostics. The standard Codex or Claude chat remains the preferred detailed view for one running model.

### The primary UI should organize work, not execution traces

Useful top-level groupings may be:

```text
ARCI-V2
  PR 03 - control handshake
    Codex implementation chat        working
    Codex audit chat                 complete
  PR 07 - reconnect behavior
    planning conversation            waiting
    planner/auditor collaboration    active
  PR 11 - robot E2E
    work-side request chat           idle
    robot-side test runner           offline/result queued
```

The UI should support search and grouping by project, PR, branch, topic, machine, provider, status, tag, and recency. Opening an item should resume or deep-link to the native conversation when possible. Related threads can be grouped without forcing them into a declared workflow.

The focused view can show one selected work item and its associated conversations. The portfolio view stays one level above and summarizes other active work without surfacing random terminal panes.

### Durable restart has two levels

1. **Conversation recovery:** remember the provider thread ID, environment, repository/worktree, and launch/resume method so a user can reliably reopen a specific chat from any organizing view.
2. **Execution recovery:** for unattended bridge-delivered work, persist the message, execution attempt, lease, result, and restart policy so a local runner can resume or safely retry after failure.

Conversation recovery is likely the earlier and more broadly useful feature. Full unattended execution recovery is most valuable for cross-machine tests, automation, and background delegated work.

### Implication for MVP order

A revised product order should likely be:

1. Discover/import local Codex sessions and record stable bridge conversation IDs.
2. Normalize repository/worktree/branch/machine/environment metadata.
3. Provide global search, tags, grouping, titles, summaries, and reliable resume/deep links independent of current folder.
4. Add NATS-backed direct messages and durable server/client request/reply.
5. Add conversation families and related-thread links, including provider-native subagents.
6. Add collaboration rooms/protocols such as planner/auditor revision exchange without requiring a universal coordinator.
7. Add durable runners, leases, wake/restart, and capability dispatch for unattended work.
8. Add AIWK as one optional policy/executor integration.
9. Add richer graph, metrics, and diagnostic traces only where actual use shows they are needed.
