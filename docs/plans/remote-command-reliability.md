# Remote Command Reliability Follow-On

Status: follow-on reliability plan; current single-user baseline remains usable
Updated: 2026-08-21

## Framing

Agent Bridge remote nodes execute provider turns on another machine after claiming a fenced Hub
command. The first Windows NUC test proved that catalog synchronization, routing, provider resume,
and correlated replies work across Tailscale. It also exposed an incomplete failure model:

- a command changes from `queued` to `claimed` once and has no renewable claim lease;
- a node that disappears before posting its result leaves the command claimed indefinitely;
- the associated message remains `queued_remote`, even when other evidence shows that the provider
  processed it;
- one exception in the busy-heartbeat coroutine stops further busy heartbeats;
- the node's outer service loop does not recover from a failed cycle;
- provider subprocess and wrapper failures do not yet leave sufficient durable local evidence;
- provider executable resolution can select a different installation and `CODEX_HOME` from the one
  that owns the cataloged conversation.

This is a reliability improvement to the existing address-book and message-fabric architecture. It
does not introduce workflows, roles, coordinators, or a general execution engine.

## Incident evidence

The Windows test produced two independent outcomes:

1. A pre-fix command for a chat rooted at `C:\Windows\system32` completed as `failed` because Bridge
   omitted Codex's `--skip-git-repo-check` option. That defect is fixed by commit `18f5124`.
2. A post-fix command was claimed and the target agent sent a correlated reply, proving that the
   provider turn ran. The node then stopped heartbeating before it posted the command result. The
   Hub retained the command as `claimed` and the request as `queued_remote`.

The Windows scheduled task remained reported as running because its PowerShell wrapper survived;
the actual node/provider child processes were gone. The wrapper's `Tee-Object` pipeline did not
produce a diagnostic log.

A separate local return-delivery failure had a distinct cause: the Linux Hub's systemd service
resolved `codex` to a confined Snap build whose `CODEX_HOME` could not see the standalone Codex
rollout. The local service has been pinned to the correct absolute Codex binary. The product should
make this provider-runtime identity observable and validate it at startup.

## Goals

1. Never leave a remote command silently claimed forever.
2. Do not automatically duplicate a provider turn merely because its acknowledgement was lost.
3. Keep node and claim heartbeats alive through transient Hub/Tailscale failures.
4. Recover a known provider result after node restart without rerunning the provider turn.
5. Represent uncertain outcomes explicitly and bring them to attention.
6. Make provider executable, version, profile root, command lifecycle, and failure logs observable.
7. Keep the common single-node and cross-machine setup lightweight.

## Non-goals

- General distributed workflow execution.
- Transparent exactly-once execution for arbitrary provider CLIs. Bridge cannot prove exactly-once
  behavior across every crash boundary without provider-side idempotency support.
- Automatic retry of a turn whose provider acceptance is unknown.
- NATS federation or multi-user authorization.
- Keeping provider agents alive indefinitely as Bridge-owned workers.

## Safety invariant

Loss of a command acknowledgement is not proof that the provider turn did not run.

Bridge must prefer an explicit `indeterminate` result and human-visible recovery choice over
silently executing a potentially consequential prompt twice. Automatic retry is allowed only when
durable evidence proves that provider execution never started.

## Command state model

Replace the implicit queued/claimed terminal model with these explicit states:

| State | Meaning |
| --- | --- |
| `queued` | Command exists but no node has claimed it. |
| `claimed` | A node owns a renewable claim lease but has not recorded provider start. |
| `running` | The node durably recorded that provider execution started. |
| `succeeded` | Provider completed and the Hub accepted the fenced result. |
| `failed` | Provider returned a definite failure and the Hub accepted it. |
| `expired` | The command lifetime ended before provider execution started. |
| `indeterminate` | The execution lease was lost after provider start and the outcome is unknown. |
| `cancelled` | A requested cancellation was confirmed before or during supervised execution. |

`expires_at` remains the overall command lifetime. Add a separate `lease_expires_at` for claim
ownership. A valid claim token may renew only its own lease. A stale token may not complete,
renew, cancel, or mutate a newly claimed command.

### Recovery rule

- An expired `claimed` command with durable node evidence that execution never started may return
  to `queued`, incrementing its attempt and issuing a new fencing token.
- An expired `running` command becomes `indeterminate`; it is never automatically replayed.
- A node restart may replay a durably journaled terminal result to the Hub using its original claim
  token while the Hub still recognizes that claim.
- If the Hub already marked the command indeterminate, a late matching terminal result may resolve
  it only when the fencing token and execution identity still match.

## Node-side command journal

Each node gets a small local SQLite journal in its Agent Bridge state directory. Before provider
execution it records:

- command ID, claim token verifier or protected token, message ID, and correlation ID;
- provider, provider thread ID, environment, workspace, and prompt digest;
- claim time, lease deadline, and attempt;
- lifecycle markers: `claimed`, `provider_starting`, `provider_started`, `provider_finished`, and
  `result_acked`;
- bounded stdout/stderr tail, provider exit code, completion time, and result payload.

The journal is not a second catalog or message store. It exists only to answer, after restart,
whether a claimed native command never started, may have started, or completed with a result that
still needs acknowledgement.

On startup the node reconciles journal entries before claiming new work:

1. resend known unacknowledged terminal results;
2. report never-started claims as safely recoverable;
3. report started-without-result entries as indeterminate;
4. retain evidence until the Hub acknowledges the terminal state and local retention expires.

## Lease and heartbeat protocol

Add an authenticated endpoint such as:

```text
POST /api/v1/node/commands/{command_id}/lease
```

The request includes node ID, claim token, execution phase, and requested TTL. The response returns
the authoritative command state and lease deadline.

Node reachability heartbeat and command lease renewal are related but distinct:

- node heartbeat says the daemon/environment can currently communicate;
- claim lease says one exact command is still owned by one exact attempt;
- provider execution continues in a supervised worker while the async control loop renews both;
- transient HTTP failures use bounded exponential backoff and do not terminate the renewal loop;
- after connectivity returns, the node reconciles the authoritative command state before posting
  or rerunning anything.

The busy-heartbeat task must catch and record transport exceptions inside its loop. The outer node
service loop must also catch cycle failures, log them, back off, and continue unless configuration
or authentication is definitively invalid.

## Provider process supervision

Replace opaque long-lived `subprocess.run` calls with a supervised process abstraction that:

- records `provider_starting` before process creation and `provider_started` immediately after;
- drains stdout and stderr continuously into bounded buffers and durable log files;
- reports PID, start time, provider executable, version, and resolved profile root;
- enforces the existing timeout and terminates the process tree when cancellation/timeout is
  confirmed;
- distinguishes process creation failure, provider non-zero exit, timeout, node shutdown, and
  unknown disappearance;
- records the terminal result locally before trying to acknowledge it to the Hub.

On Windows, use a process-tree mechanism appropriate for native child processes, such as a Job
Object or an equivalent supervised launcher. On Unix, use a process group. Do not treat killing a
wrapper shell as proof that all provider descendants stopped.

## Provider runtime identity

Node and Hub diagnostics must expose, without secrets:

- configured provider executable path;
- resolved executable path and version;
- effective `HOME`, `CODEX_HOME`, or Claude profile root;
- whether the provider can enumerate each selected local thread;
- whether the daemon user matches the user that owns provider sessions.

Add a `doctor` command or startup diagnostic that fails clearly when the configured provider sees a
different session store from cataloged conversations. Deployment examples should use absolute
provider paths instead of relying on a service manager's PATH.

## Message-state projection

Message state must be derived from the authoritative command lifecycle, not merely from broker
acknowledgement:

| Command evidence | Message state |
| --- | --- |
| Remote command queued | `queued_remote` |
| Claim acquired/provider running | `running_remote` |
| Fenced success result accepted | `delivered` |
| Definite provider failure | `failed` |
| Command expired before start | `expired` |
| Lease lost after provider start | `indeterminate` |

NATS acknowledgement remains broker telemetry; it means the Hub consumed the envelope, not that
the remote provider completed the turn.

A correlated reply is strong operational evidence that the target processed a request, but a bare
correlation ID is not sufficient to mutate the request state automatically. Add reply causation so
agents and interfaces can set `causation_id` to the triggering message ID. A causally linked reply
may annotate the original request as `response_observed`; the command result remains the authority
for `delivered` unless a deliberate reconciliation rule is added.

The UI should show both message and remote-command states when they diverge, including claim age,
node reachability, last lease renewal, and recovery actions.

## Operational wrappers and logging

The Windows baseline wrapper should avoid a `Tee-Object` native-process pipeline. Use
`Start-Process -Wait -PassThru` with distinct stdout/stderr files, propagate the child exit code,
and let Task Scheduler restart a failed wrapper. The node itself should emit startup identity,
cycle failures, claim transitions, provider start/finish, and shutdown as structured log lines.

Linux should run the node directly under a user systemd service:

```ini
[Service]
EnvironmentFile=%h/.config/agent-bridge/node.env
ExecStart=/absolute/path/to/agent-bridge-node
Restart=always
RestartSec=5
```

Use `journalctl --user -u agent-bridge-node` for diagnostics. Avoid a shell pipeline between
systemd and the node process.

## API and storage changes

- Add `lease_expires_at`, `execution_started_at`, and explicit state constraints to node commands.
- Add node command-attempt records if retry history cannot remain lossless in one row.
- Add lease-renewal and recovery-report endpoints with claim-token fencing.
- Add `running_remote`, `expired`, and `indeterminate` message states.
- Add causal reply support to CLI, MCP, HTTP, and message records.
- Add an attention item for indeterminate commands with safe actions: inspect evidence, mark
  delivered, mark failed, or explicitly retry as a new command.
- Add retention for acknowledged node-journal evidence and completed command attempts.
- Migrate existing indefinitely claimed commands to `indeterminate`, not `queued`.

## Delivery phases

### Phase 0: operational hardening

- Pin absolute provider binaries in Hub/node service configuration.
- Replace the Windows `Tee-Object` wrapper and document stdout/stderr locations.
- Add startup logging for node ID, environment, provider paths/versions, Hub URL origin, and sync
  policy without printing credentials.
- Add a `doctor`/`--once` runbook for Windows and Linux.

### Phase 1: truthful terminal states

- Add `running_remote`, `expired`, and `indeterminate` states.
- Detect stale claimed commands and surface attention without automatic replay.
- Show command/message divergence in API, UI, CLI, and NATS diagnostics.
- Provide an explicit administrative resolution operation with an audit record.

### Phase 2: renewable leases and resilient control loop

- Add fenced claim-lease renewal.
- Make busy heartbeats and the outer node loop survive transient transport errors.
- Preserve node reachability during long provider turns.
- Reject late results from superseded claim tokens.

### Phase 3: durable node journal and supervised providers

- Add the local command journal and restart reconciliation.
- Supervise provider processes and capture bounded output.
- Replay known terminal results without rerunning provider work.
- Classify unknown crash windows as indeterminate.

### Phase 4: causal response evidence and recovery UX

- Carry causation IDs through agent replies.
- Show response-observed evidence beside command state.
- Add inspect/resolve/retry controls and corresponding attention records.

## Verification

Unit and integration tests must cover:

- a transient heartbeat failure during a long provider turn followed by recovery;
- repeated claim renewal and rejection of an incorrect or stale claim token;
- node termination before provider start, allowing safe requeue;
- node termination after provider start, producing `indeterminate` without replay;
- provider completion followed by network loss, then result replay from the node journal;
- a causally linked reply arriving before command completion;
- a wrapper process remaining alive after its child exits;
- absolute Codex path/profile validation against a known local thread;
- migration of an old claimed command to indeterminate;
- new commands continuing after an unrelated indeterminate command;
- no duplicate provider turn under lease expiry, node restart, or NATS redelivery.

Cross-machine acceptance should run on both Windows and Linux:

1. normal request and correlated reply;
2. non-Git cwd delivery;
3. temporary Tailscale loss during a long turn;
4. node process termination before and after provider start;
5. Hub restart after provider completion but before result acknowledgement;
6. node restart and journal reconciliation;
7. explicit operator resolution of an indeterminate command.

## Workable baseline before implementation

The current build is usable for development and testing when occasional manual intervention is
acceptable:

- normal remote turns and replies work;
- the Windows non-Git cwd defect is fixed;
- an old stuck claimed command does not prevent the node from claiming later queued commands after
  the node itself is healthy;
- failed/stuck messages can be diagnosed by correlation ID and replaced with a fresh message;
- a Linux systemd service with `Restart=always` is less exposed to the PowerShell-wrapper failure.

The residual cross-platform risk is a Hub/Tailscale interruption or node/provider crash after a
claim: that one message may remain `queued_remote`/claimed and require inspection or a fresh send.
This should not require restarting Agent Bridge after every message. It does make the current build
unsuitable for unattended consequential actions where duplicate avoidance and definitive terminal
state are mandatory.

For near-term remote Linux testing, proceed with the existing node if Bridge is primarily used for
cataloging, attention, and occasional messages and the user can intervene. Pin the provider binary,
run the node directly under systemd with `Restart=always`, verify one round trip, and keep native
provider work as the authority. Complete this reliability plan before depending on Bridge for
unattended overnight command execution or guaranteed delivery accounting.

## Acceptance

- Every claimed command either renews its lease or reaches a truthful terminal/indeterminate state.
- No expired running command is automatically replayed.
- A restarted node can acknowledge a previously completed provider result without rerunning it.
- Transient heartbeat failures do not silently stop lease renewal.
- Provider runtime identity is visible and mismatched session stores fail diagnostics clearly.
- Message history distinguishes broker acknowledgement, remote execution, and response evidence.
- Windows and Linux node services recover from process exit and leave actionable logs.
