# Agent Bridge Implementation Roadmap

Status: Active  
Updated: 2026-08-11

This roadmap complements `SPEC.md`. Work advances at verified milestone boundaries; later
milestones may be refined from implementation evidence without changing the product principles.

## Architecture decisions

- One monorepo contains independently runnable Catalog, Bridge, protocol, UI, and node packages.
- Python 3.12 services and React/TypeScript are the implementation baselines.
- SQLite with FTS5 is the initial trusted single-user Catalog store.
- Codex App Server runs locally over stdio for discovery; unattended turns will use the Codex SDK.
- Catalog remains usable without NATS, and headless Bridge use will not require the web UI.
- Manual mode always bypasses coordinator inference. Delegate is the default intelligent mode.
- Portfolio and work coordinators are durable logical roles, not immortal model conversations.

## Milestones

### 0 — Monorepo and shared contracts: implemented

- Python workspace and frontend application tooling
- Versioned Pydantic contracts, deterministic identities, JSON Schema, and generated TypeScript
- Conversation, relationship, Bridge, execution, node, work, and coordinator role contracts
- Initial database migration and architecture decision record

### 1 — Local Codex Catalog: implemented

- Supervised App Server stdio client with handshake, correlation, pagination, diagnostics, and restart
- Active, archived, IDE, CLI, exec, App Server, and native-subagent discovery
- SQLite catalog with FTS5 search and user/agent transcript indexing
- Explicit exclusion of command and tool output from the transcript index
- Catalog metadata overrides, tags, notes, pin, hide, archive, native resume command, and React UI

### 2 — Work organization and durable roles: implemented

- Work items, optional repository/PR associations, role hierarchy, checkpoints, reports, and events
- Focused work view and relationship graph backed by one shared model
- Durable conversation handoff and coordinator placeholders before automation

Gate: 35 Python tests and 8 frontend tests pass; strict type checks, production build,
fresh migration, and an integrated work/conversation/role/relationship smoke test pass.

### 3 — Private multi-machine Catalog: implemented

- Native Linux/WSL and Windows node agent
- Authenticated HTTPS synchronization independent of NATS
- Node reachability, environment ownership, remote resume routing, and indexing exclusions

Gate: 49 Python tests and 12 frontend tests pass; two independently authenticated nodes
may both own a `host` environment, remote resume is routed only to its recorded owner,
offline fallback is rejected, migrations round-trip, and the private API/web container
pair passes a live same-origin health smoke test.

### 4 — NATS JetStream foundation: implemented

- Private broker, per-node credentials, subject permissions, durable work, events, and results
- Acknowledgement, redelivery, expiration, dead letters, idempotency, and operational projection

Gate: 64 Python tests pass, including a real Docker broker restart with offline delivery,
de-duplication, redelivery, and durable dead-lettering. The checked-in private broker
configuration passes subject-permission and file-storage restart smokes, migration `0004`
round-trips, and transport activity is queryable through the SQL operational projection.

### 5 — Manual Bridge and runners: implemented

- Guided and advanced Manual UI plus equivalent CLI
- Codex, conversation-wake, allowlisted command, and robot/server-client runners
- Explicit leases, retries, cancellation, result-before-ack, and side-effect idempotency

Gate: 83 Python tests and 15 frontend tests pass, including real JetStream execution,
expiration, cancellation, result projection, duplicate-delivery idempotency, and
persist-before-ack acceptance cases. Strict type/lint checks, the production web build,
dependency audit, CLI smoke, Compose structure, and migration `0005` round-trip pass.

### 6 — Coordinator intelligence: implemented

- Portfolio intake and per-work coordinators
- Manual, advise, delegate, and autonomous modes
- Durable role leases/fencing, context recovery, checkpoints, reports, and stale-rollup detection

Gate: 113 Python tests and 18 frontend tests pass, including normal-app portfolio intake,
background activation, Catalog/provider conversation mapping, lease-loss cancellation,
advise approval, bounded and idempotent Bridge dispatch, restart reconciliation, and
Manual failure isolation. Strict type/lint checks, production web and coordinator-enabled
API image builds, dependency audit, migration `0006` round-trip, and a live container
health/runtime smoke test pass.

### 7 — Flexible collaboration: implemented

- Direct, request/reply, capability, fan-out, room, planner/auditor, and ad hoc topologies
- Native subagent compatibility and optional AIWK executor integration

Gate: 127 Python tests and 18 frontend tests pass, including real-NATS durable
request/reply, competing capability workers, direct/peer/hierarchical traffic, explicit
fan-out, participant rooms, subscriber events, planner/auditor correlation, native
subagent boundaries, generic result routing, and optional AIWK execution. Strict checks,
web build/audit, migration `0007` round-trip, and broker configuration validation pass.

### 8 — Observability and hardening: implemented

- Node, role, execution, lease, retry, dead-letter, artifact, and topology views
- NATS health/advisories, structured logs, optional metrics, retention, backup, and restore
- Claude provider adapter after the Codex integration is stable

Gate: 143 Python tests and 20 frontend tests pass. Operational summary/list and
diagnostic views cover broker and consumer health, supervised services, nodes, roles and latest
checkpoints, stale rollups, pending executions, typed leases, retries, dead letters, artifacts,
and declared/observed topology. Retention, verified backup/restore, transcript deletion, credential
rotation, redaction, Prometheus/export hooks, and Claude Code discovery/native recovery are covered
by tests and runbooks. Strict checks, web build/audit, migration `0007` round-trip, and live-NATS
diagnostics pass.

### 9 — Legacy bridge removal: implemented

- Port ABB workflows to capability-addressed durable requests
- Remove the two-party HTTP mailbox, polling clients, and shared bearer token in one migration

Gate: the ABB simulator request fixture validates against the public contracts and routes to the
durable `bridge.v1.capability.robot-simulator-e2e` subject. The Windows runner capability executes
the existing fail-closed RobotStudio test and returns its structured JSON result before ACK. The
legacy HTTP server, PowerShell/Bash polling clients, shared-token tests, and lint exception are
removed; repository scans and migration acceptance tests prevent those protocol paths from
returning. The final repository gate passes 145 Python tests and 20 frontend tests, strict checks,
production build/audit, migration round-trip, and a live authenticated NATS smoke that creates the
runner's least-privilege capability, node-inbox, and control consumers.

## Autonomous development protocol

- The primary agent owns architecture, integration, verification, and milestone acceptance.
- Subagents receive bounded tasks; parallel writers use isolated worktrees or non-overlapping packages.
- Scope discoveries are deferred unless required by the current exit criterion.
- External deployment, credentials, destructive migration, and network exposure require user authority.
- Each milestone ends with tests, a usable demonstration, documentation, limitations, and a handoff.
- AIWK is not the development controller unless explicitly requested later.
