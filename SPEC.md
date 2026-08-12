# AI Work Catalog and Agent Bridge Specification

Status: Draft product and architecture specification  
Updated: 2026-08-11

## 1. Purpose

This specification defines two complementary systems:

1. **AI Work Catalog** — a global interface for finding, organizing, understanding, and resuming AI conversations and related work.
2. **Agent Bridge** — a durable communication and execution substrate for exchanging messages and results between conversations, agents, tools, and machines.

The systems share stable identities and references, but they are independently useful and must remain independently deployable. Neither system defines a universal software-development workflow.

This document specifies what the systems are intended to do. It intentionally does not prescribe an implementation plan, task breakdown, delivery schedule, or repository migration.

## 2. Motivation

Modern coding-agent interfaces work well for understanding one active conversation. Codex and Claude already provide useful detailed chat views, tool activity, agent summaries, and, increasingly, native subagent delegation.

The larger problems appear across conversations and environments:

- A useful conversation was opened from a different VS Code window, folder, worktree, dev container, or machine and is difficult to find or reopen.
- Related discussions are scattered across independently created chats.
- Several exploratory threads must be manually renamed to remain distinguishable.
- Provider history panes present low-level, folder-sensitive lists rather than a coherent view of ongoing work.
- A user may remember the topic discussed but not the chat title or the environment in which it occurred.
- A result produced on one machine must reach an agent or user on another machine.
- Server/client, robot, and integration tests require durable cross-machine request and result delivery.
- Agents collaborating on a plan, audit, or review need a communication channel that survives individual turns and process restarts.
- An agent that ends its turn cannot be awakened merely because a message exists elsewhere.

A representative workload is a large stack of related pull requests, such as approximately 17 ARCI-V2 PRs, with two to four active at once across several VS Code windows, worktrees, and agent conversations. The user does not need every agent collapsed into a new chat client. The user needs a reliable global model of which conversations exist, what work they belong to, where they run, how to resume them, and how they can communicate.

## 3. Design principles

### 3.1 Preserve the native per-conversation experience

Codex, Claude, and other agent interfaces remain the preferred detailed view of an individual conversation. The Catalog should organize and reopen native conversations rather than duplicate every chat feature, terminal, tool event, or streaming token.

### 3.2 Separate organization from communication

The Catalog and Bridge share identity, but neither depends on the other's user interface or runtime:

- The Catalog can index, search, group, and resume conversations without NATS.
- Agent Bridge can route messages and run unattended work through a CLI or API without the Catalog UI.

### 3.3 Treat paths as location, not identity

A conversation or work item must not be identified solely by an absolute folder. The same repository can exist at different paths on Windows, Linux, a dev container, a worktree, and a remote machine.

### 3.4 Support flexible topologies

No coordinator, workflow, task tree, provider, or communication topology is globally mandatory. The system must support direct messaging, peer collaboration, request/reply, capability dispatch, fan-out, provider-native subagents, structured workflow controllers, and ad hoc networks.

### 3.5 Let capable agent runtimes orchestrate locally

When Codex, Claude, or another runtime can launch and coordinate its own subagents, Agent Bridge should not reproduce that internal orchestration. A native root conversation and its subagents form an orchestration island. The bridge becomes important when communication crosses a conversation, runtime, provider, repository, environment, or machine boundary.

### 3.6 Make structure optional and composable

AIWK is one optional policy system for work that benefits from specifications, invariants, gates, bounded role loops, evidence, and commits. It is not a global blueprint. Other work may be direct, exploratory, dynamically delegated, or organized by a completely different system.

### 3.7 Prefer high-value metadata over exhaustive tracing

The default interface should emphasize conversation identity, status, relationships, summaries, location, messages, and recovery. Detailed command logs, tool calls, streaming deltas, token metrics, and traces are diagnostic layers, not the primary product.

### 3.8 Preserve one coherent focus without prohibiting concurrency

The user should be able to focus on one selected work item while other work continues in the background. System concurrency must not force a terminal-multiplexer or "who needs babysitting?" experience.

## 4. System boundaries

```text
┌───────────────────────────────────────────────────────┐
│ AI Work Catalog                                       │
│ conversation discovery, global search, organization,  │
│ summaries, relationships, native open/resume          │
└─────────────────────────┬─────────────────────────────┘
                          │ shared stable references
                          │ optional send/wake actions
┌─────────────────────────▼─────────────────────────────┐
│ Agent Bridge                                          │
│ durable messages, request/reply, routing, NATS,       │
│ runners, wake/resume, retries, result delivery        │
└───────────────────────────────────────────────────────┘
```

The shared boundary is an identity and reference contract, not a shared UI, a mandatory NATS subject hierarchy, or a workflow state machine.

## 5. AI Work Catalog

### 5.1 Product responsibility

The Catalog answers:

- What AI work currently exists?
- Which conversations are related to a project, pull request, branch, topic, or objective?
- Where does each conversation live?
- What was the conversation doing when it last stopped?
- Is it active, idle, waiting for approval, failed, archived, or unavailable?
- How can the exact native conversation be reopened or resumed?
- Where was a particular topic, decision, error, or implementation previously discussed?

### 5.2 Conversation discovery

The Catalog must support provider-specific adapters that discover existing and new conversations. An adapter may use a supported provider API, local application service, SDK, exported transcript, session database, or other provider-specific mechanism.

Discovery must not require every conversation to have been created by Agent Bridge.

For each discovered conversation, the adapter should obtain as much of the following as the provider supports:

- Provider and provider-specific thread/session ID
- User-facing name and preview
- Creation and last-activity times
- Runtime status
- Current working directory
- Repository, remote, branch, commit, pull request, and worktree
- Machine, operating system, and execution environment
- Parent, child, and descendant conversation relationships
- Native source, such as CLI, IDE, application, cloud, exec, or subagent
- Pin/archive state
- Latest or final agent summary
- A provider-specific resume or open locator

Missing provider fields must not prevent registration.

### 5.3 Stable Catalog identity

Every imported conversation receives a stable Catalog identity independent of its title or folder:

```json
{
  "conversation_id": "conv-...",
  "provider": "codex",
  "provider_thread_id": "thr-...",
  "node_id": "work-laptop",
  "environment_id": "robotics-devcontainer"
}
```

The tuple used to deduplicate provider sessions must include enough location information to avoid assuming that provider IDs are globally unique across unrelated installations.

Human-readable titles are mutable metadata and must never be used as durable routing keys.

### 5.4 Repository and work identity

The Catalog should prefer stable repository identities such as a normalized Git remote plus repository-relative metadata. Absolute paths remain useful location attributes.

A conversation may be associated with zero or more of:

- Logical project
- Repository
- Worktree
- Branch
- Commit
- Pull request
- Objective or work item
- AIWK project, stage, step, or role
- Topic or user-defined tag

These associations are optional. A general research conversation may have no repository or pull request.

### 5.5 Search

The Catalog must provide global search across providers and environments.

Searchable material should include, subject to user configuration and provider availability:

- Conversation titles and previews
- User prompts
- Agent messages and final summaries
- Selected transcript text
- Pull requests, branches, repositories, and worktrees
- Tags, notes, objectives, and work-item names
- Artifact names
- Cross-conversation messages and handoffs

Tool-output streams and raw command logs should be excluded from the default full-text index unless explicitly enabled.

Search results must show enough location and work metadata to distinguish otherwise similar conversations and must provide an open or resume action when available.

### 5.6 Organization

The user must be able to:

- Rename a Catalog entry without changing its provider identity
- Add tags and notes
- Pin, archive, or hide entries
- Group conversations by project, PR, branch, objective, topic, or custom collection
- Relate conversations without declaring a formal workflow
- Mark one conversation as a continuation, fork, audit, review, plan, test run, or other relation to another
- View provider-native subagents as a conversation family
- Select one work item as the current focus

Automatic grouping and title suggestions may assist the user but must remain editable.

### 5.7 Native open, resume, and continuation

The Catalog must distinguish three operations:

1. **Open:** show an existing native conversation without starting new work.
2. **Resume:** start a new turn in the same provider conversation and original environment.
3. **Continue elsewhere:** create a new conversation in another environment using a durable summary/handoff when direct resumption is unavailable or undesirable.

The interface must report when the original machine or environment is unavailable rather than silently opening a different conversation.

### 5.8 Catalog user experience

The top-level experience should organize logical work rather than terminal panes:

```text
ARCI-V2
  PR 03 - control handshake       2 conversations   working
  PR 07 - reconnect behavior      3 conversations   audit ready
  PR 11 - robot E2E               2 conversations   waiting for robot
```

A selected work item should show its associated conversations, locations, states, summaries, relationships, and available actions.

Other work should remain summarized one level away. Background activity must not reorder the focused interface or produce attention-demanding notifications unless user action is genuinely required.

## 6. Agent Bridge

### 6.1 Product responsibility

Agent Bridge answers:

- How can one conversation or machine send a durable message to another?
- How can a request wait while its recipient is offline?
- How can one available worker be selected by capability?
- How can several independent agents receive the same proposal or event?
- How can a stopped agent be resumed when actionable work arrives?
- How can execution survive process or machine failure without silently losing work?
- How can results and artifacts be returned to a different conversation or machine?

### 6.2 Broker architecture

The selected broker is NATS with JetStream:

- Core NATS provides subject-based publish/subscribe and request/reply.
- JetStream provides durable streams, consumers, acknowledgments, redelivery, retention, and replay.
- NATS Object Store may hold moderate development artifacts, while Git or external object storage may be used where more appropriate.

A single broker is the initial reliable rendezvous point. The protocol must not require a permanent master agent or workflow coordinator.

### 6.3 Communication primitives

The bridge must support these logical primitives:

- **Message:** durable or ephemeral information sent to a conversation, agent, room, or subject.
- **Request:** work or information for which a response is expected.
- **Response:** a correlated answer or result.
- **Event:** a fact that zero or more subscribers may observe.
- **Control message:** cancellation, wake, retry, pause, or other runtime instruction where authorized.
- **Artifact reference:** an immutable or versioned reference to larger content.

Workflow, objective, task, PR, and AIWK fields are optional extensions, not required envelope fields.

### 6.4 Flexible routing topologies

The bridge must support, without treating any as canonical:

#### Direct conversation-to-conversation

```text
Conversation A ↔ Conversation B
```

#### Cross-machine request/reply

```text
Developer conversation → robot runner → result conversation
```

#### Capability dispatch

```text
Request → one available worker with capability robot-test
```

#### Fan-out

```text
Proposal → independent reviewers A, B, and C
```

#### Peer collaboration

```text
Planner ↔ auditor ↔ revised plan ↔ acceptance
```

#### Provider-native hierarchy

```text
Root Codex/Claude conversation → runtime-owned subagents
```

The bridge may register and display this hierarchy but does not need to mediate its internal messages.

#### External policy controller

```text
AIWK or another controller → bridge executions → controller evaluates results
```

#### Ad hoc network

Several conversations may communicate without a central coordinator or declared global objective.

### 6.5 Message envelope

A base envelope should contain only stable transport and correlation fields:

```json
{
  "schema": "agent-bridge/v1",
  "message_id": "msg-...",
  "kind": "request",
  "sender": "conversation:conv-a",
  "destination": "capability:robot-test",
  "correlation_id": "corr-...",
  "causation_id": "msg-...",
  "reply_to": "conversation:conv-b",
  "created_at": "...",
  "expires_at": "...",
  "body": {},
  "artifacts": [],
  "extensions": {}
}
```

The `extensions` object may carry policy-specific data such as an AIWK workflow fingerprint, stage, step, role, cycle, and attempt. Agent Bridge must not interpret an extension unless an installed integration explicitly owns it.

### 6.6 Delivery semantics

Durable processing should use explicit acknowledgments and at-least-once delivery.

Consequences:

- Producers and consumers must use stable IDs.
- Duplicate delivery is expected and must be handled idempotently.
- Receiving work creates a lease or acknowledgment deadline.
- Long-running work can report progress to extend the lease.
- A crashed or disconnected worker leaves work eligible for redelivery.
- Retry count and terminal delivery failure must be visible.
- A result should be durably published before the input request is acknowledged as completed.

Exactly-once side effects are not promised by the broker. Integrations performing external mutations must provide their own idempotency or reconciliation.

### 6.7 Local runner and wake behavior

An AI model turn must not stay open merely to poll for messages. Each participating machine may run a lightweight local runner that blocks efficiently on the broker.

```text
message becomes actionable
  → runner receives or claims it
  → runner starts/resumes the appropriate provider conversation or execution
  → provider performs work
  → runner publishes the result
  → runner acknowledges the input
```

The runner, not NATS itself, translates an incoming message into a provider-specific start or resume operation.

The system must distinguish:

- Waking a registered conversation
- Starting a new conversation
- Running a non-interactive agent execution
- Invoking a non-agent adapter such as a robot test runner

### 6.8 Conversation recovery versus execution recovery

Agent Bridge and the Catalog must distinguish:

#### Conversation recovery

Locate a provider thread, its environment, and its native resume mechanism. This primarily solves human continuity and organization.

#### Execution recovery

Persist a request, execution attempt, lease, retry policy, and result so unattended work can continue after process or machine failure.

An existing conversational thread need not be automatically retried merely because it can be resumed. Retry behavior must be explicit policy associated with an unattended request or runner.

### 6.9 Collaboration rooms

The bridge may represent a durable multi-party discussion as a room with participants and ordered, correlated messages.

A planner/auditor protocol may use typed messages such as:

```text
proposal
critique
revision
acceptance
```

These types support a useful integration but do not define global workflow semantics. The participants or an optional room policy decide when agreement is complete.

### 6.10 Artifacts

Messages should inline small instructions, summaries, structured results, diagnostics, and exit codes.

Larger content should use references containing at least:

```json
{
  "name": "robot-test-results.json",
  "uri": "nats-object://artifacts/sha256-...",
  "media_type": "application/json",
  "size": 184231,
  "sha256": "..."
}
```

Source changes should normally be represented through Git commits, branches, pull requests, or patches instead of copying repositories through the broker.

## 7. Shared identity and reference model

The Catalog and Bridge share reference types conceptually equivalent to:

- `ConversationRef`
- `AgentRef`
- `NodeRef`
- `EnvironmentRef`
- `RepositoryRef`
- `WorkRef`
- `ArtifactRef`
- `ProviderThreadRef`
- `CapabilityRef`

### 7.1 Conversation reference

```json
{
  "conversation_id": "conv-...",
  "provider": "codex",
  "provider_thread_id": "thr-...",
  "title": "ARCI-V2 PR 07 reconnect audit",
  "node_id": "work-laptop",
  "environment_id": "robotics-devcontainer",
  "repository_id": "github:org/t_robotics",
  "worktree_path": "/machine-specific/path",
  "branch": "feature/reconnect",
  "pull_request": 7,
  "parent_conversation_id": null,
  "status": "active",
  "last_summary": "...",
  "tags": ["transport", "reconnect"],
  "created_at": "...",
  "last_activity_at": "..."
}
```

Only the bridge ID, provider identity, and sufficient provider locator metadata are fundamental. All work and repository associations are optional.

### 7.2 Relationships

The shared model must allow typed, extensible relationships rather than require one tree:

```text
parent_of
continuation_of
fork_of
audits
reviews
plans_for
tests
implements
related_to
depends_on
supersedes
```

Unknown relationship types may be preserved as namespaced extensions.

### 7.3 Presence and availability

The system should distinguish durable registration from current reachability:

- Known and reachable
- Known but offline
- Environment unavailable
- Provider thread present but idle
- Provider thread active
- Waiting for approval or user input
- Archived
- Missing or no longer recoverable

An offline conversation remains searchable and addressable. Durable messages wait according to retention and expiration policy.

## 8. Native agent and subagent boundary

A native agent runtime may decide to spawn subagents when asked directly or when its configuration permits. Agent Bridge must remain compatible with increasingly capable models and avoid requiring every internal delegation to be predeclared externally.

The Catalog should record provider-native parent/descendant relationships when available. By default it should summarize a subagent family under its root conversation while allowing inspection of individual descendants.

Agent Bridge only needs to address a native subagent directly if the provider exposes a stable address and there is a concrete reason to communicate with it independently. Otherwise messages should target the root conversation and allow the native runtime to delegate.

Parallel writers in one worktree are unsafe by default. Any integration allowing concurrent code-writing agents must explicitly isolate worktrees or provide another conflict-control policy.

## 9. AIWK and other policy integrations

AIWK may act as an external policy owner for structured software-delivery work. In that mode:

- AIWK owns specifications, stages, steps, roles, gates, bounded correction loops, semantic acceptance, and commit policy.
- Agent Bridge owns durable delivery, execution placement, agent wake/start, leases, execution retry, and result transport.
- Agent Bridge must not reinterpret a gate result or decide AIWK's next semantic role.
- AIWK-specific references belong in a namespaced message extension.

AIWK may submit a role invocation to Agent Bridge through an executor adapter. A possible extension is:

```json
{
  "aiwk": {
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

Other policy systems must be able to integrate without adopting AIWK terminology or topology.

An AIWK-controlled role may itself ask a native agent to use subagents or may delegate an exploratory request through Agent Bridge. Conversely, an ad hoc investigation may later create or attach an AIWK project. These transitions are optional and explicit.

## 10. Observability

### 10.1 Catalog observability

The normal user-facing view should expose:

- Conversation and work status
- Last activity and latest summary
- Provider, node, environment, repository, branch, and PR
- Parent/descendant and user-defined relationships
- Native resume availability
- Cross-agent messages and durable handoffs
- Requests waiting on an offline endpoint

### 10.2 Bridge observability

Operational views should expose:

- Broker health
- Connected and offline nodes
- Pending and leased messages
- Consumer lag
- Redelivery and retry counts
- Expired and dead-lettered requests
- Runner heartbeats and active executions
- Artifact storage use

NATS server monitoring, JetStream advisories, and optional metrics systems can support this view.

### 10.3 Diagnostic detail

Fine-grained model/tool activity is optional. The system may retain or stream detailed events for debugging, but it must not require exhaustive event ingestion to provide useful organization and communication.

## 11. Security and privacy

- Each node or integration should have its own credentials.
- Broker permissions should restrict publish and consume subjects by identity and role.
- A single global bearer token is insufficient.
- NATS monitoring endpoints must not be exposed without an external protection boundary.
- Provider credentials and authentication files must never be copied into message bodies, Catalog records, handoffs, generated artifacts, or logs.
- Transcript indexing must be configurable because conversations may contain proprietary code, credentials, personal information, or sensitive logs.
- Users should be able to exclude providers, projects, folders, conversations, message kinds, and tool output from indexing.
- Cross-machine transcript synchronization should be explicit; a deployment may keep full text local while sharing only metadata and summaries centrally.
- Destructive or externally visible actions remain governed by the executing agent's permissions and policy. Receipt of a bridge message does not grant new authority.

## 12. Non-goals

The combined system is not intended to be:

- A replacement for the Codex, Claude, or IDE detailed chat interface
- A terminal multiplexer
- A mandatory global workflow engine
- An AIWK replacement
- A system in which every task must have phases, gates, or a coordinator
- A requirement that all native subagent communication pass through NATS
- An exactly-once external side-effect system
- A general distributed filesystem
- A production-scale Kafka- or Temporal-like platform
- A mandatory full trace archive of every model token, tool call, and command output
- A system that automatically broadens agent authority when work is delegated

## 13. Required scenarios

### 13.1 Find and resume a misplaced chat

A user searches for "RWS reconnect invalidates session." The Catalog returns relevant Codex and Claude conversations across worktrees and machines, shows their summaries and locations, and resumes the selected native thread when its environment is available.

### 13.2 Continue when the original environment is unavailable

The Catalog identifies that a conversation belongs to an offline dev container. It does not silently open an unrelated thread. It offers the durable summary and artifacts required to continue in a new conversation elsewhere.

### 13.3 Cross-machine robot test

A development conversation submits a durable test request to capability `robot-test`. The robot machine is offline. The request remains queued, a runner claims it after reconnection, the test executes, and the result is delivered to the requested conversation even if the original sender is no longer active.

### 13.4 Planner and auditor reach agreement

A planning conversation publishes a proposal to an audit conversation. They exchange correlated critique and revision messages until the participants or room policy records acceptance. Neither participant must be the global coordinator for unrelated work.

### 13.5 Native subagents remain native

A Codex root conversation launches several read-only exploration subagents. The Catalog records their relationship and summaries. Agent Bridge does not force their internal work through external queues. The root conversation may later send its consolidated result to another machine.

### 13.6 AIWK-controlled execution

AIWK submits a structured role invocation through Agent Bridge. The bridge runs it on an eligible node and returns the structured result. AIWK evaluates the result against its workflow and determines the next role. Agent Bridge does not apply AIWK semantics to unrelated requests.

### 13.7 Headless bridge use

A script sends and receives durable messages through Agent Bridge without installing or opening the Catalog UI.

### 13.8 Catalog-only use

A user indexes, searches, groups, and resumes local AI conversations without running a NATS server.

## 14. Product success criteria

The product satisfies this specification when a user can:

1. Find a conversation by topic without remembering its original folder, window, or manually assigned title.
2. See where a conversation lives and reliably open, resume, or continue it elsewhere.
3. Organize conversations around logical work such as projects and pull requests without imposing a workflow.
4. Preserve native agent and subagent experiences for detailed work.
5. Send a durable message or request across conversations and machines.
6. Receive results after either party has ended its original active turn.
7. Execute server/client and robot-test exchanges without foreground polling.
8. Use direct, peer, capability, fan-out, hierarchical, structured, or ad hoc topologies.
9. Integrate AIWK as one optional policy controller without making it a global schema.
10. Inspect delivery failures and recoverable execution state without requiring exhaustive model-level tracing.

## 15. Terminology

- **Catalog conversation:** the stable cross-environment record representing a provider conversation.
- **Provider thread:** the native Codex, Claude, or other runtime session used for actual resumption.
- **Node:** a machine or runtime host connected to Agent Bridge.
- **Environment:** a native workspace, worktree, dev container, VM, WSL instance, or other execution context on a node.
- **Agent:** a model-backed or non-model worker capable of receiving work.
- **Runner:** a lightweight process that receives bridge messages and invokes a provider or adapter.
- **Capability:** a routable declaration such as `robot-test`, `code-review`, or `implementation`.
- **Conversation family:** a root provider conversation and its native descendants or explicitly related chats.
- **Work item:** an optional logical grouping such as a PR, objective, investigation, or user-defined topic.
- **Policy owner:** an optional component, such as AIWK, that interprets results and decides semantic next steps.
- **Artifact:** a referenced file, commit, patch, log, result, screenshot, or other output.

