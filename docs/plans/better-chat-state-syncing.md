# Better Chat State Syncing and Role Result Projection

## Purpose

Make running Agent Bridge work visible before a Codex turn completes, and make every
role-bound execution update the durable role hierarchy consistently.

This is a handoff specification. It covers two related event consumers but keeps their
semantics distinct:

1. **Conversation projection** answers: “What is this agent doing right now, and where
   is its chat?”
2. **Role projection** answers: “What durable role state resulted, and what must its
   parent coordinator do next?”

`RoleReport` is not a substitute for live chat synchronization. It is a durable,
structured roll-up produced after a role checkpoint.

## Problems observed

### Conversations appear only after completion

For `new_execution`, `OpenAICodexClient` learns the provider thread ID immediately
after `thread_start()`, but returns it only in the terminal `RunnerOutput`. The Catalog
therefore cannot create or attach the conversation until the turn finishes.

For resumed developer/auditor turns, the existing conversation remains linked, but:

- the runner emits only coarse “Starting Codex SDK turn” and “completed” progress;
- progress does not update conversation status or preview;
- the conversation detail query does not poll;
- the conversation list does not poll;
- local session transcript discovery normally runs only during explicit Catalog sync;
- the final response is projected only when the terminal result arrives.

The result is a stale role/chat UI throughout long-running implementation and audit
turns.

### Role state is bypassed by normal executions

`CoordinatorEngine` emits a `RoleReport` only when it directly activates a child role,
commits that child’s `RoleCheckpoint`, and the role has a parent.

The current PR convergence controller does something different:

1. dispatch a normal Bridge execution containing `parameters.role_id`;
2. wait for a terminal execution result;
3. parse the final response;
4. update `extensions["agent_bridge.convergence"].state`;
5. dispatch the next normal execution.

It does not activate the worker role, commit a worker checkpoint, append a
`RoleReport`, update a roll-up, or reactivate the parent coordinator. This is also why
the smoke coordinator can dispatch successful work while continuing to say that it is
awaiting a child report.

### Dispatch completion is confused with objective completion

A coordinator activation currently completes its intake after its authorized Bridge
request is published. Publishing proves durable dispatch, not successful execution.
The coordinator checkpoint correctly records the delegation as active, but the intake
is presented as completed before result verification.

### UI queries disagree

Coordinator intakes and activations poll every five seconds, while role lists, reports,
and roll-ups do not. Conversation list/detail queries also do not poll. Consequently,
the page can show a new activation status beside an old checkpoint number and no child
reports until a navigation or reload.

Failed coordinator intakes with `attention_required` are also included in the approval
queue even when no approval is meaningful.

## Desired behavior

For a role-bound Codex execution:

1. Bridge accepts the request and records the role/execution relationship.
2. The node starts or resumes the provider thread.
3. As soon as the provider thread ID is known, the Catalog creates/updates the
   conversation, attaches it to the role and work item, and marks both execution and
   conversation active.
4. Safe progress summaries update execution and conversation state while the turn is
   running.
5. The UI updates without manual navigation or Sync.
6. A terminal result updates the conversation preview/transcript and status.
7. The terminal result commits exactly one role checkpoint.
8. If the role has a parent, it emits exactly one `RoleReport` referring to that
   checkpoint.
9. The parent roll-up becomes stale until incorporated.
10. A parent coordinator with a matching active delegation is automatically scheduled
    to consume the report, verify the result, and decide whether to complete, retry,
    escalate, or delegate again.
11. Only after that verification may the coordinator intake become completed.

## Protocol changes

### Add structured progress metadata

Extend `ExecutionProgress` with a closed optional metadata object, or introduce a
separate typed execution lifecycle event. At minimum support:

- `phase`: `accepted | provider_thread_started | running | finalizing`;
- `provider`: currently `codex`;
- `provider_thread_id`;
- `cwd`;
- `role_id`;
- `stage`;
- a safe, user-visible `summary`.

Do not put hidden reasoning, approval secrets, raw tool streams, or unrestricted model
events into this object. Conversation projection should retain only explicit user/agent
messages and intentionally published progress summaries.

The Codex runner must publish `provider_thread_started` immediately after
`thread_start()` or `thread_resume()` and before awaiting `thread.run()`.

If the installed SDK exposes safe incremental assistant/tool status events, they may be
adapted into additional progress summaries. The design must still work when the SDK
provides only thread-start and terminal-result events.

### Add a durable role-execution binding

Create a first-class record rather than repeatedly inferring ownership from free-form
parameters:

```text
RoleExecutionBinding
  execution_id (unique)
  role_id
  work_id
  parent_role_id (snapshot, nullable)
  stage (nullable)
  activation_id or lease/fencing identity
  checkpoint_version_before
  state: queued | running | terminal_projected
  created_at / updated_at
```

Creation must happen transactionally with role-bound dispatch, before publishing the
Bridge request. Continue reading legacy `parameters.role_id` and workflow extensions
for already-queued messages, but new code should use the first-class binding.

### Preserve checkpoint fencing

Do not let the result consumer append arbitrary checkpoints without a role lease. A
role-bound execution should acquire a role activation/lease before dispatch, and its
terminal projector should commit under that same fencing token.

If a role must support more than one simultaneous execution later, model that
explicitly. The first implementation may reject a second active execution for the same
role.

## Backend projection

### Conversation projector

On `provider_thread_started`:

- upsert by `(provider, provider_thread_id, node_id)`;
- set status to `active`;
- set `last_activity_at`;
- record execution ID, work ID, role ID, CWD, node, and stage;
- attach the conversation to the work item;
- attach or rotate it on the role according to the existing conversation policy.

On each progress event:

- update execution progress;
- update conversation status/activity and a separate `live_summary` field;
- do not overwrite the durable final preview with unrelated workflow text.

On terminal result:

- set status to `idle`, `blocked`, or terminal-error equivalent;
- save the final assistant response as preview/recent context;
- refresh explicit user/assistant turns from the local Codex session when available;
- preserve correct association when developer and auditor results arrive close together.

Use execution ID and provider thread ID as idempotency keys. A redelivered event must
not duplicate conversation attachments or messages.

### Role result projector

For every terminal result with a `RoleExecutionBinding`:

1. load the role, work item, execution, and active role activation;
2. validate the execution/work/role relationship and fencing token;
3. map transport status to role status:
   - successful stage with more workflow remaining: `active`;
   - successful terminal role objective: `completed`;
   - agent-declared or transport blocker: `blocked`;
   - retryable failure: retain `active` with blocker/retry recommendation;
4. create a checkpoint containing the exact requested objective, stage decision,
   execution ID, result summary, and evidence/artifacts;
5. commit the checkpoint transactionally;
6. if the role has a parent, append one `RoleReport` referencing that version;
7. mark the binding `terminal_projected` using `execution_id` as the idempotency key;
8. complete/release the role activation.

The full final response may remain on the execution/conversation. The `RoleReport`
should be concise and structured: outcome, decisions, evidence, blocker, and recommended
parent action.

### Parent coordinator wake-up

After a child report is committed:

- mark the child roll-up stale;
- find an open parent intake/checkpoint whose active delegation corresponds to the
  execution/action/child role;
- schedule a new parent activation with the child report in context;
- let the coordinator decide completion/retry/escalation under the original remaining
  authority and budget;
- record incorporation through `record_rollup`.

Do not have the result projector itself declare the parent objective complete.

### Intake lifecycle

Introduce or consistently use a state such as `executing`/`awaiting_results` after
successful dispatch. Reserve `completed` for a checkpoint that has no active
delegations or unresolved dependencies and has incorporated all required terminal
reports.

## UI behavior

### Near-term polling implementation

Until a shared event stream exists:

- poll selected conversation detail every 2 seconds while active;
- poll conversation list every 5 seconds while any visible conversation is active;
- poll role list, reports, roll-ups, and activations together every 5 seconds on the
  Coordinator page;
- poll work detail and relevant executions while a workflow is non-terminal;
- invalidate all related query keys when a terminal transition is observed.

This is sufficient for functional correctness and can later be replaced by SSE.

### Preferred event-driven implementation

Add one Catalog SSE stream carrying versioned invalidation events such as:

- `execution.updated`;
- `conversation.upserted`;
- `conversation.message_added`;
- `role.updated`;
- `role.checkpoint_committed`;
- `role.report_added`;
- `coordinator.intake_updated`.

The browser should invalidate the corresponding React Query keys. Keep periodic slow
polling as recovery for dropped connections.

### Presentation requirements

- Show the linked developer/auditor conversation as soon as its provider thread starts.
- Show running phase, last progress summary, node, and elapsed time.
- Keep “Recent context” scoped to the selected conversation; never copy another role’s
  final response into it.
- Refresh checkpoint number and reports without role reselection.
- Show `awaiting results` distinctly from `completed`.
- Only render Approve/Reject for `awaiting_approval`; failed historical intakes should
  offer retry/inspect/dismiss instead.

## Compatibility and migration

- Existing executions without bindings remain visible but do not synthesize a role
  checkpoint unless role ownership can be proven unambiguously.
- Existing `parameters.role_id`, `agent_bridge.workflow.role_id`, and
  `agent_bridge.coordinator.role_id` remain read-compatible during migration.
- New convergence dispatches must create bindings.
- Existing failed test intakes remain historical records; the UI must not treat them as
  approval requests.

## Acceptance tests

1. A new developer execution appears under its role before the Codex turn completes.
2. A resumed developer conversation changes to active and shows safe progress before
   completion.
3. Simultaneous developer and auditor executions never exchange previews or recent
   context.
4. A successful terminal execution updates the correct conversation without explicit
   Catalog Sync.
5. Exactly one checkpoint and, where applicable, one `RoleReport` are produced despite
   duplicate result delivery.
6. The parent coordinator is reactivated after the child report and incorporates its
   roll-up.
7. The coordinator intake remains `awaiting_results` after dispatch and becomes
   `completed` only after result verification.
8. A blocked child produces a blocked checkpoint/report and parent attention rather
   than false completion.
9. UI checkpoint counts, reports, activation state, and recent context update while the
   page remains open.
10. Failed historical intakes do not display Approve/Reject controls.
11. No hidden reasoning or raw sensitive tool output is persisted as chat transcript.

## Recommended implementation order

1. Add role-execution binding and transactional dispatch ownership.
2. Publish early provider-thread lifecycle metadata.
3. Project live conversation identity/status.
4. Project terminal checkpoint and `RoleReport` idempotently.
5. Reactivate parent coordinators and correct intake lifecycle.
6. Add coordinated UI polling and query invalidation.
7. Add SSE as an optimization after the durable event semantics are stable.

## Non-goals

- Recreating hidden Codex reasoning or every internal tool event.
- Treating free-form chat transcript as authoritative workflow state.
- Adding convergence-specific callbacks that bypass the generic role execution model.
- Marking coordinator work complete merely because a request was published.
