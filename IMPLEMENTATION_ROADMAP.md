# Agent Bridge Implementation Roadmap

Status: single-user core implemented
Updated: 2026-08-14

## Completed phases

1. **Product boundary:** replaced the orchestration thesis with directory, messaging, and attention;
   specified explicit keep/amend/remove API policy.
2. **Directory:** candidate discovery, explicit selection, stable chat numbers, alias/provider-title
   ownership, selected-only transcripts, Codex/Claude/native-subagent coverage, collections, and
   deterministic location-bound identity.
3. **Message fabric:** minimal envelope protocol, direct mailboxes, mailbox/notify/digest rooms,
   correlations, retries, dead letters, duplicate suppression, and explicit provider turns.
4. **Machines:** authenticated catalog sync, 10-second daemon cycle, environment ownership, remote
   open/message/start commands, fencing, and fail-closed location behavior.
5. **Attention and diagnostics:** Updates versus Needs Attention, NATS activity/issues/deliveries,
   stream/consumer diagnostics, export, retention, backup/restore, and transcript deletion.
6. **Interfaces:** conversation-first React UI, unified CLI, HTTP/OpenAPI, and local stdio MCP tools.
7. **Removal and migration:** deleted Work/Role/Coordinator/Execution/Capability/Fan-out packages,
   APIs, UI, tests, contracts, and deployment configuration; migration `0008` exports then removes
   their tables.
8. **Verification:** Python tests, Ruff, strict mypy, frontend tests/typecheck/build, minimal public
   schema, and fresh Alembic upgrade.
9. **Safe delivery:** mailbox-only messages, foreground listeners, separate transport/processing
   outcomes, explicit replay, and read-only owning-node transcript refresh.

## Deliberately deferred

- Real-world soak testing with multiple remote Codex and Claude nodes.
- Optional provider hook installers; `agent-bridge reconcile` and `agent-bridge-node --once` are the
  hook targets today, with polling as the repair mechanism.
- Automatic provider wake, listener restart, outer/inner relationships, and live token-delta relay.
- Three-node NATS deployment automation and quorum failure tests.
- Multi-user principals, workspaces, sharing, authorization, and federation, specified only in
  [docs/plans/shared-workspaces.md](docs/plans/shared-workspaces.md).

No general execution engine phase remains on this roadmap.
