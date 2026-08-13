# Coordinator runtime

The coordinator is an optional intelligence layer over the durable Catalog and
Bridge contracts. Manual Bridge traffic does not call it and remains available
when model activation fails or is disabled.

The private-hub image installs the `codex` package extra. This supplies the
stable `openai-codex` Python SDK and its pinned Codex runtime. A local base
development install can omit that extra when only Catalog or Manual Bridge is
needed:

```bash
pip install -e .
```

Install coordinator execution support explicitly with:

```bash
pip install -e '.[codex]'
```

Coordinator SDK turns use strict JSON Schema output and default to
`Sandbox.full_access` with `ApprovalMode.deny_all`. Agent Bridge therefore runs
headless Codex agents without sandbox approval prompts unless a runner or
coordinator explicitly selects a narrower policy.

Autonomous intake fails closed unless it carries an explicit deadline, bounded
scope, allowed capabilities, and at least one finite token or cost budget.
Delegate mode executes only in-scope authorized actions and surfaces meaningful
scope expansion for approval. Advise mode persists recommendations but executes
nothing. Manual mode bypasses coordinator inference and routing completely.

## Durable control plane

The Catalog stores coordinator intake, append-only intake events, activations, authority usage,
role checkpoints, child reports, and parent rollup positions. A stable
`role-portfolio-coordinator` is bootstrapped on a fresh hub so unassigned objectives have a durable
intake role even before any work-specific topology exists.

Coordinator activation is fenced by the existing role lease. A model turn or action must renew and
revalidate its activation before side effects, and checkpoint commit validates the holder, fencing
token, next checkpoint version, durable charter, authority profile, and delegation consistency.
Expired or superseded activations cannot publish checkpoints or consume additional budget.

The context snapshot contains the role charter, selected work and intake, latest checkpoint,
unresolved questions and blockers, structured child reports, rollup freshness, conversation
history, and the current provider locator. Raw token and tool streams are not copied into
coordinator context.

Parent rollups store the exact child checkpoint version they incorporated. The rollup list compares
that position with each child's current durable checkpoint, so the UI can mark a portfolio or work
summary stale without inspecting model transcripts.

## HTTP surface

- `POST /api/v1/coordinator/intake` accepts advise, delegate, and autonomous objectives. Manual is
  rejected with a pointer to `/api/v1/bridge/requests`.
- `GET /api/v1/coordinator/intake` and `GET /api/v1/coordinator/intake/{id}` expose durable status,
  authority, proposed actions and topology, routing, approval state, and attention.
- `POST /api/v1/coordinator/intake/{id}/decision` records an explicit approve or reject decision.
- Role context, activation history, and stale child rollups are queryable beneath
  `/api/v1/coordinator/roles/{role_id}`.

The server generates intake identity and owns runtime holder identity. Browser clients do not
submit lease holders, fencing tokens, checkpoint commits, usage mutations, or completion state.

Autonomous HTTP intake is stricter than the protocol defaults: it requires explicitly supplied
parallel and retry limits, allowed capabilities, a bounded work or role scope, a future deadline,
and a finite token or cost budget. Initial autonomous intake cannot grant organic scope expansion;
that requires a later user approval decision.
