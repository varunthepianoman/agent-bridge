# General Agent Bridge Execution Engine

Status: proposed architecture and implementation specification  
Purpose: replace workflow-specific orchestration code with a provider-neutral, durable, validated workflow runtime

## Problem statement

Agent Bridge currently proves multi-stage PR remediation through a hard-coded `ConvergenceController`. The controller recognizes a fixed set of stage strings, embeds prompts in Python, parses an auditor's first response line, implements one loop, and positions two specific human gates.

That implementation validated the product concept, but it does not scale. Adding a different workflow currently risks requiring new custom Python control flow. The earlier AIWK system avoided some of this by generating Claude-specific JavaScript workflows for Claude's Workflow Runner. Agent Bridge needs the corresponding capability as provider-neutral infrastructure rather than as Claude-specific generated runtime code.

## Goals

- Represent multi-stage workflows declaratively.
- Validate definitions before execution.
- Execute Codex, Claude, commands, verifiers, coordinators, and future providers through the same control plane.
- Support human gates, machine gates, bounded convergence loops, retries, timeouts, cancellation, fan-out, and subworkflows.
- Store durable, queryable run state and event history.
- Make every remote-write authorization explicit and auditable.
- Recover safely after process restarts, duplicate events, and partial delivery.
- Surface high-level state and per-stage conversations/artifacts in the UI.
- Reproduce the existing PR review-update workflow as the first golden workflow.
- Provide a migration path for useful AIWK/Claude workflow concepts without preserving provider-specific execution assumptions.

## Non-goals for the first release

- A general-purpose programming language embedded in workflow definitions.
- Unbounded or dynamically generated loops.
- Arbitrary JavaScript execution inside the catalog service.
- Replacing provider adapters or node execution runners.
- Allowing coordinators or agents to silently grant themselves new authority.
- Perfect visual workflow authoring in the first milestone; validated YAML/JSON plus a read-only UI is sufficient.

## Architectural separation

### Workflow engine

Owns durable workflow state. It determines which stages are eligible, records transitions and gates, resolves retry/loop policy, and dispatches concrete operations.

### Coordinator

Performs planning, delegation, synthesis, and exception handling when a workflow assigns those responsibilities. A coordinator is an actor in a workflow, not the workflow runtime itself.

### Execution runner

Executes one concrete operation on one node. It does not decide the next workflow stage.

### Provider adapter

Starts or resumes a provider conversation and normalizes provider events/results.

### Verification executor

Runs declared commands/checks against an exact candidate identity and returns structured evidence.

### Integration service

Owns canonical branch advancement, protected remote writes, stack propagation, and exact repository/branch verification.

## First-class domain objects

### WorkflowDefinition

Immutable, versioned definition containing:

```text
workflow_definition_id
name
version
status                  draft | active | deprecated
input_schema
output_schema
stages
transitions
policies
created_at
created_by
definition_digest
```

Runs bind to an immutable definition version. Editing a definition creates a new version; it never changes the semantics of an active historical run.

### WorkflowRun

```text
workflow_run_id
workflow_definition_id
workflow_definition_version
work_id
status
input
output
current_stage_ids
started_at
completed_at
cancelled_at
failure
definition_snapshot_digest
```

### StageRun

One runtime instance of a stage. Loop iterations and retries create distinct stage-run or attempt records rather than overwriting history.

```text
stage_run_id
workflow_run_id
stage_key
iteration
status
resolved_actor
resolved_node
conversation_id
workspace_id
input
output
started_at
completed_at
failure
```

### ExecutionAttempt

One delivery/execution attempt with its request envelope, idempotency key, acknowledgements, execution ID, result envelope, timing, and error classification.

### GateDecision

Append-only decision record:

```text
gate_decision_id
stage_run_id
gate_key
decision                approved | rejected | changes_requested | expired | cancelled
actor
comment
approved_scope
evidence_digest
created_at
```

Approval is scoped to the exact candidate/evidence digest. Mutating the candidate invalidates a candidate-bound approval.

### Artifact

Durable reference to plans, review documents, patches, build logs, test reports, candidate manifests, drafted replies, published links, and other stage outputs.

### WorkflowEvent

Append-only event with monotonic sequence per run. Projected run state may be rebuilt from events or checked against them.

## Stage kinds

The initial engine should support:

### `agent`

Start or resume a provider conversation. Declares actor selection, conversation policy, workspace policy, prompt template, structured output schema, timeout, and authority.

### `coordinator`

A specialized agent stage targeting a coordinator role and expecting a structured plan, delegation, or synthesis result.

### `command`

Run an explicit command or approved command template on a node/workspace. Return structured exit status and artifacts.

### `verification`

Verify an exact candidate SHA/tree using a configured verification profile. Verification results cannot be reused for a different tree unless the profile explicitly declares a safe equivalence rule.

### `human_gate`

Pause until an authorized user decides. The stage displays the exact plan, candidate, evidence, or remote action being approved.

### `machine_gate`

Evaluate structured conditions such as verification success, required checks, artifact presence, candidate identity, or policy compliance.

### `integration`

Rebase a task candidate onto the current canonical branch, verify it, and fast-forward the canonical branch under a target lock.

### `remote_action`

Perform narrowly declared external mutations such as push, GitHub reply, review resolution, or PR update. Each action has an explicit authority requirement and idempotency key.

### `fan_out` / `fan_in`

Launch independent child stages and join them under declared completion semantics. First release may restrict fan-out to a static list.

### `subworkflow`

Invoke a versioned workflow definition with mapped inputs and outputs.

## Actor and execution placement

A stage must not hard-code a provider thread ID. It selects an actor using a validated selector:

```text
role_id
role_type + work scope
capability requirements
provider constraints
conversation policy
node constraints
workspace policy
```

Resolution produces an immutable stage-run placement record before dispatch.

Conversation policies:

- `resume_role_current`: resume the role's current conversation when available.
- `new_for_stage`: create a new conversation.
- `new_for_iteration`: reuse within an iteration but replace on the next loop.
- `explicit`: use a specified conversation only.

Workspace policies:

- `task_workspace`: provision/resume the work item's ephemeral task worktree.
- `exact_candidate`: read-only or non-modifying checkout of an exact candidate.
- `control_checkout`: integration-only operations.
- `none`: non-filesystem operation.

## Structured inputs and outputs

Workflow transitions must not depend primarily on parsing prose such as `VERDICT: accepted`.

Every stage declares an output JSON Schema. The provider adapter requests structured output when supported and validates the result before completing the stage.

Example audit output:

```json
{
  "verdict": "accepted",
  "candidate_sha": "...",
  "candidate_tree": "...",
  "findings": [],
  "verification_artifact_ids": ["artifact-..."]
}
```

Human-readable narrative may accompany the structured result, but transitions use validated fields.

## Transitions and conditions

Transitions are explicit edges:

```text
from
to
condition
priority
```

Use a small safe expression language over immutable stage outputs and run context. CEL or a similarly constrained expression evaluator is preferable to Python/JavaScript evaluation. The first milestone may implement a minimal operator set:

- equality/inequality;
- boolean operations;
- numeric comparison;
- presence checks;
- collection empty/non-empty;
- references to run input, stage output, gate decision, and policy.

Definition validation must reject missing references, ambiguous unconditional transitions, unreachable stages, and transitions whose types do not match their schemas.

## Loops and convergence

Cycles are forbidden unless enclosed in an explicit bounded loop definition.

```text
loop_key
entry_stage
continue_condition
exit_condition
max_iterations
on_limit
iteration_state_schema
```

The developer/auditor loop becomes:

```text
implementation -> verification -> audit
audit.accepted -> exit
audit.changes_requested -> next implementation iteration
```

Every iteration records its candidate SHA, findings, conversations, verification evidence, and attempt count. Reaching the limit produces a declared blocked/escalated outcome, never an implicit infinite loop.

## Gates and authority

Gates are stateful authorization boundaries, not UI booleans or prompt sentences.

Each gate declares:

```text
gate_key
required_authority
subject_type            plan | candidate | remote_action_set | exception
subject_digest
allowed_decisions
expires_after
on_reject
```

Remote actions must declare the exact permitted action set. For example, approval to push a branch and post six prepared replies does not authorize resolving threads, submitting a review, changing PR metadata, or rewriting unrelated branches.

Gate decisions and remote actions are append-only and auditable. Revisions that change the approved subject digest require a new approval.

## Verification model

Verification must be a first-class stage and artifact, not prose in an agent prompt.

A verification profile declares:

```text
profile_id
repository selector
candidate mode          head | tree | synthetic_merge
base selector
commands
environment requirements
expected test/report formats
required checks
timeouts
artifact retention
```

Result identity includes:

- repository;
- candidate SHA and tree SHA;
- base SHA when relevant;
- synthetic merge SHA/tree for stacked PR verification;
- exact command and environment;
- exit code;
- test counts and failures;
- artifact digests;
- timestamps.

For stacked PRs, candidate verification must support GitHub-style synthetic merge testing against the current PR base. Final verification occurs after any rebase, history rewrite, or integration operation.

## Durability, idempotency, and recovery

- Every dispatch has a stable idempotency key derived from workflow run, stage run, and attempt.
- Duplicate result envelopes do not advance a stage twice.
- Transitions and outbox dispatch are committed atomically where possible.
- Use a transactional outbox for broker publication.
- Stage/run leases use fencing tokens so a stale engine cannot continue after losing ownership.
- Process restart reconstructs runnable/waiting stages from durable records.
- Timeouts create explicit events and follow declared retry/escalation policy.
- Retry distinguishes delivery failure, infrastructure failure, provider failure, invalid structured output, verification failure, and business rejection.
- Partial remote success is recorded per action and reconciled before retry. Never blindly repeat a possibly completed GitHub mutation.

## Definition validation

Before activation, validate:

- unique stage and transition keys;
- input/output schema validity;
- all references and actor selectors;
- reachability and terminal paths;
- bounded cycles only;
- gate placement for authority-requiring stages;
- remote actions have explicit authority contracts;
- retry and timeout bounds;
- required node capabilities;
- workspace policy compatibility;
- artifact references;
- no secret literals in definitions;
- definition digest and version immutability.

Provide `agent-bridge workflow validate <file>` and an API validation endpoint before providing a visual authoring UI.

## Example declarative shape

The final schema may differ, but definitions should resemble:

```yaml
apiVersion: agent-bridge/workflow/v1
kind: WorkflowDefinition
metadata:
  name: pr-review-update
  version: 1
spec:
  inputSchema: pr-review-update-input-v1
  stages:
    - id: intake
      kind: agent
      actor: {roleType: review_intake}
      conversation: resume_role_current
      outputSchema: review-plan-v1

    - id: approve_plan
      kind: human_gate
      subject: ${stages.intake.output}

    - id: implement
      kind: agent
      actor: {roleType: developer}
      workspace: task_workspace
      outputSchema: candidate-report-v1

    - id: verify_candidate
      kind: verification
      profile: ${run.input.verification_profile}
      candidate: ${stages.implement.output.candidate_sha}

    - id: audit
      kind: agent
      actor: {roleType: auditor}
      workspace: exact_candidate
      outputSchema: audit-verdict-v1

  loops:
    - id: remediation
      entry: implement
      continueWhen: ${stages.audit.output.verdict == "changes_requested"}
      exitWhen: ${stages.audit.output.verdict == "accepted"}
      maxIterations: 2
```

## Golden PR review-update workflow

The existing PR workflow must be migrated to this exact conceptual sequence:

1. **Review intake**
   - Fetch authoritative GitHub review threads.
   - Update three canonical review documents.
   - Produce a structured implementation proposal.

2. **Implementation-plan human gate**
   - User approves, rejects, or requests changes to the recorded plan.

3. **Task workspace provisioning**
   - Create/resume ephemeral developer workspace from current PR head.

4. **Implementation**
   - Update code and review documents.
   - One commit per review-thread topic.
   - Produce after-state summaries.

5. **Candidate verification gate**
   - Compile/test/lint exact candidate and, when applicable, its synthetic merge with the current stacked base.
   - Failed verification returns to developer or blocks according to policy; audit does not begin.

6. **Audit**
   - Auditor inspects exact candidate, diff, documents, and verification evidence.
   - Structured verdict: accepted or changes requested.

7. **Bounded convergence**
   - On changes requested, return findings to the same developer conversation.
   - Repeat implementation, verification, and audit up to configured maximum rounds.

8. **Publish-package preparation**
   - Reconcile commit structure.
   - Draft exact replies with commit links.
   - Record intended remote action set.

9. **Publish human gate**
   - User approves the exact candidate and remote action set, unless task policy explicitly permits automatic publication.

10. **Final integration and verification**
    - Rebase candidate onto current canonical PR branch.
    - Verify post-rebase candidate.
    - Fast-forward canonical branch under lock.

11. **Push**
    - Push with lease protection and verify remote head.

12. **Required CI gate**
    - Wait for configured GitHub checks against the pushed head.
    - Do not post success replies while required checks are failing or refer to another SHA.

13. **Post review replies**
    - Post only approved replies, idempotently.
    - Record URLs and action receipts.

14. **Stack propagation**
    - If policy is `on_publish`, cascade-rebase descendants, verify affected layers, and push with lease protection.
    - A propagation failure marks the PR published but the stack propagation blocked; it must be conspicuous.

15. **Complete and workspace retention**
    - Record final artifacts and links.
    - Begin task-workspace retention/retirement policy.

Configurable gates must include at least:

- implementation-plan gate on/off;
- publish gate on/off;
- stack propagation `on_publish | on_merge | manual`;
- maximum convergence rounds;
- verification profile and required CI checks.

## AIWK and Claude workflow migration

1. Inventory existing AIWK-generated workflows and the Claude Workflow Runner primitives they depend on.
2. Classify each primitive as stage, transition, gate, loop, artifact, actor selection, retry, or provider-specific behavior.
3. Define provider-neutral equivalents.
4. Preserve prompts/templates as versioned assets only where they express domain behavior.
5. Replace implicit Claude runtime behavior with explicit engine semantics.
6. Build an import/report tool that identifies unsupported constructs; do not silently approximate them.
7. Run golden fixtures comparing legacy expected sequence/results with Agent Bridge workflow runs.

## API requirements

Minimum API surface:

```text
POST   /api/v1/workflow-definitions/validate
POST   /api/v1/workflow-definitions
GET    /api/v1/workflow-definitions/{id}/versions/{version}
POST   /api/v1/workflow-runs
GET    /api/v1/workflow-runs/{id}
GET    /api/v1/workflow-runs/{id}/events
POST   /api/v1/workflow-runs/{id}/cancel
POST   /api/v1/stage-runs/{id}/retry
POST   /api/v1/gates/{id}/decisions
```

All mutating endpoints require idempotency keys and return the durable record representing the accepted action.

## UI requirements

- Workflow definition/version shown on each work item.
- Ordered stage timeline with active, waiting, blocked, failed, and completed states.
- Actor, conversation, node, workspace, candidate SHA, attempts, and duration per stage.
- Gate cards showing exact approval subject and consequences.
- Loop iteration count and limit.
- Verification evidence with commands, SHA/tree, test results, and logs.
- Remote action receipts and links.
- Clear distinction between “current PR published” and “upstack propagation completed.”
- No requirement to scroll through full conversation transcripts to monitor execution.

## Implementation sequence

### Milestone 0: behavioral baseline

- Preserve tests for the existing convergence controller.
- Run a minimal end-to-end coordinator test before refactoring.
- Capture the current PR workflow as golden fixtures/events.

### Milestone 1: definitions and validator

- Protocol models, JSON Schemas, versioning, digesting, storage, and CLI/API validation.
- No execution yet.

### Milestone 2: durable run kernel

- WorkflowRun, StageRun, events, leases, outbox, idempotent transitions, restart recovery.
- Linear agent/command/human-gate workflows only.

### Milestone 3: structured provider stages

- Actor resolution, conversation policy, output-schema validation, retry/error taxonomy.

### Milestone 4: verification and integration

- Exact candidate identities, verification profiles, ephemeral workspaces, canonical integration locks.

### Milestone 5: loops and convergence

- Explicit bounded loops and developer/auditor migration.

### Milestone 6: remote actions and CI gates

- Scoped authorization, action receipts, GitHub checks, reply idempotency.

### Milestone 7: stack propagation and UI

- Worktree-aware propagation and comprehensive run timeline.

### Milestone 8: legacy migration

- Remove the hard-coded controller after golden parity and migrate AIWK workflow definitions.

## Acceptance criteria

- The PR review workflow is expressed without workflow-specific Python transitions.
- Restarting the catalog during any waiting/running stage does not lose or duplicate progress.
- Duplicate broker results do not dispatch the next stage twice.
- Auditor verdicts are structured and schema-validated.
- Loops are bounded and iteration history is visible.
- Human approval is bound to exact plan/candidate/action digests.
- Verification blocks audit or publication when candidate identity or tests fail.
- Remote actions are scoped, idempotent, and auditable.
- A failed stack propagation is distinguishable from a failed PR publication.
- Coordinator stages work through the same engine rather than bypassing it.
- At least one non-PR workflow demonstrates that the engine is genuinely general.

## Open decisions

- CEL versus a smaller custom condition evaluator.
- Event-sourced authoritative state versus relational authoritative state plus append-only audit events.
- Initial fan-out/fan-in semantics.
- Artifact storage backend and retention.
- How workflow definitions are packaged and promoted between environments.
- Whether coordinators may propose workflow patches, and what human validation is required before activation.

