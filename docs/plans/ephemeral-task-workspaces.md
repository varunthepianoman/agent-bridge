# Ephemeral Task Workspaces and Stable Conversation CWDs

Status: proposed implementation specification  
Primary concern: isolate agent development without permanently checking out canonical PR or stack branches

## Decision summary

Agent Bridge should treat `main` and pull-request branches as canonical integration branches. Agents do not edit those branches directly. Every implementation, audit that needs an exact checkout, or other code-changing operation runs in an ephemeral Git worktree on a uniquely named task branch.

A permanent control checkout owns canonical branch integration and stack operations. The control checkout may itself be a linked Git worktree; it does not need to be the repository's original clone.

Completed worktrees may be deleted while preserving their original paths as lightweight tombstones. Before Agent Bridge resumes a conversation whose workspace was retired, it recreates a valid Git worktree at the same path. Codex therefore keeps a stable CWD without being asked to operate in a directory that contains only partial project infrastructure.

## Goals

- Isolate concurrent agents from one another and from canonical branches.
- Apply the same workspace lifecycle whether one agent or many agents are active.
- Keep canonical PR and stack branches available to stack-management tooling.
- Serialize integration without serializing independent development.
- Preserve a conversation's recorded CWD across retirement and rehydration.
- Avoid losing untracked task evidence or local project infrastructure during cleanup.
- Make abandoned, blocked, retired, and rehydrated workspaces visible and recoverable.

## Non-goals

- This design does not make arbitrary dirty worktrees safe to delete.
- This design does not allow the same branch to be checked out in multiple worktrees.
- This design does not make a tombstone directory an executable project workspace.
- This design does not define the general workflow engine, although that engine will invoke these lifecycle operations.

## Terminology and invariants

### Repository

The shared Git repository/object database. Multiple linked worktrees may share it.

### Control checkout

A permanent working directory designated for one project or stack. It owns:

- canonical local branches;
- `gh-stack` metadata and operations;
- final integration;
- stack propagation;
- final ancestry and remote verification.

The ARCI control checkout is expected to be:

```text
/home/varunkamat/dev/arci-v2/main/t_robotics_abb_arci_v2_gofa
```

It is a linked worktree of:

```text
/home/varunkamat/dev/t_robotics
```

That is valid. “Control clone” must be renamed to “control checkout” throughout the product and documentation.

### Canonical branch

`main`, a PR head branch, or another integration branch that represents externally visible project state. A canonical stack branch may be checked out only in its designated control checkout while stack operations are running. It must never be checked out in an agent task worktree.

### Task branch

A temporary branch belonging to exactly one Agent Bridge work item/execution workspace. Suggested format:

```text
agent-bridge/<work-key>/<role>/<execution-key>
```

Names must be URL-safe, collision-resistant, and traceable to catalog records. The opaque execution ID remains authoritative even if a friendly slug is included.

### Task worktree

An ephemeral worktree containing a task branch or an exact detached audit candidate. Every task worktree has exactly one owning workspace record. Every code-changing task branch has exactly one active owning worktree.

### Tombstone

A small directory left at a retired worktree's former path. It is metadata, not a valid workspace.

## Required persistent model

Introduce a first-class execution-workspace record rather than storing this only in extension JSON.

Minimum fields:

```text
workspace_id
repository_id
work_id
role_id                 nullable
conversation_id         nullable
node_id
control_checkout_path
workspace_path
workspace_kind          task | audit | control
state                   provisioning | active | integrating | retired | rehydrating | blocked | abandoned
target_branch
task_branch             nullable for detached audit workspaces
starting_target_sha
candidate_sha           nullable
integrated_sha          nullable
remote_sha              nullable
created_at
last_used_at
retired_at              nullable
retention_until         nullable
cleanup_attempts
blocked_reason          nullable
metadata
```

The catalog must enforce one active workspace per task branch and must reject a workspace path already owned by a different active record.

## Workspace lifecycle

### 1. Provision

1. Resolve the repository and designated control checkout.
2. Acquire a short repository/workspace-provisioning lock.
3. Fetch the target branch from its configured remote.
4. Record the exact starting target SHA.
5. Create a unique task branch at that SHA.
6. Create the task worktree at its durable workspace path.
7. Apply approved ignored-file infrastructure, preferably through a checked-in manifest such as `.worktreeinclude` or an Agent Bridge workspace template.
8. Verify the worktree's repository, branch, HEAD, node, and cleanliness.
9. Start or resume the agent turn with `cwd` equal to the task worktree path.

Provisioning is idempotent. Retrying the same request must either return the existing valid workspace or fail with an explicit identity mismatch; it must not create an untracked duplicate.

### 2. Execute

- All source edits and local commits occur on the task branch.
- The execution result records candidate SHA, tree SHA, commands, exit codes, test counts, and artifacts.
- The workspace remains associated with its conversation while active.
- Concurrent tasks targeting the same canonical branch may execute independently.

### 3. Integrate

Integration is serialized per canonical target branch.

1. Acquire the target-branch integration lock.
2. Fetch and verify the current remote target.
3. Refuse to continue if the expected remote has moved in an unexplained way.
4. Rebase the task branch onto the latest canonical target branch.
5. Treat conflicts as an explicit blocked state requiring resolution in the task worktree.
6. Run required verification against the post-rebase candidate.
7. Confirm that the canonical target can be fast-forwarded to the candidate.
8. Advance the canonical local branch from the control checkout/integration service.
9. Push using lease protection.
10. Verify the remote SHA.
11. If the target is below other active stack layers, request stack propagation.

The integration service, not the developer agent, owns canonical branch movement. Merge commits into canonical PR branches are forbidden unless a workflow explicitly declares a different integration policy.

### 4. Retire

A workspace is eligible for retirement only when:

- no execution is running;
- no background terminal remains;
- tracked changes are committed or explicitly discarded under an authorized abandonment operation;
- untracked/ignored files have been classified;
- integration and remote state are recorded, or the workspace is explicitly marked abandoned;
- the retention grace period has expired, unless the user requests immediate cleanup.

Retirement flow:

1. Capture a manifest of Git status, task SHA, target SHA, and relevant artifacts.
2. Archive approved untracked evidence outside the worktree.
3. Remove the Git worktree using a validated exact path.
4. Delete the temporary task branch only when its commit is integrated or preserved by another durable ref.
5. Recreate the former directory as a tombstone.
6. Mark the workspace retired.

Never recursively delete a workspace based only on an unresolved variable or catalog string. Resolve the registered Git worktree path and compare it to the exact workspace record first.

## Tombstone format

Use a single machine-readable file:

```text
.agent-bridge-retired.json
```

Example:

```json
{
  "schema_version": "agent-bridge/workspace-tombstone/v1",
  "workspace_id": "workspace-...",
  "state": "integrated",
  "work_id": "work-...",
  "conversation_id": "conv-...",
  "repository_id": "repo-...",
  "target_branch": "varun/abb-arci-v2-pr3b-data-plane-tcp",
  "integrated_sha": "42170eed2a622d35463163574343c5b456eb3a83",
  "remote_sha": "42170eed2a622d35463163574343c5b456eb3a83",
  "retired_at": "2026-08-13T00:00:00Z",
  "rehydration_required": true
}
```

An optional `README.md` may explain that the environment was retired and must be reopened through Agent Bridge. Do not preserve only `.codex`, `.agents`, or `AGENTS.md` and treat the result as a functional project: without the repository, source tree, and project root, Codex would load incomplete context and Git/tool operations would be wrong.

## Rehydration

Agent Bridge must rehydrate before sending a new turn or opening a retired conversation through its UI.

1. Resolve the conversation's workspace record.
2. Detect and validate the tombstone.
3. Acquire the workspace and target-branch locks.
4. Move the tombstone to a temporary safe location.
5. Create a fresh task branch from the current canonical target unless the workflow explicitly requests the historical integrated commit.
6. Create a Git worktree at the exact original workspace path.
7. Restore approved local infrastructure and archived task artifacts.
8. Verify project-root discovery and Git identity.
9. Update the workspace record to active with the new starting SHA/task branch.
10. Resume the Codex conversation with the unchanged CWD.
11. Remove the temporary tombstone backup after successful validation.

If rehydration fails, restore the tombstone and report a recoverable blocked state.

### Direct Codex opening caveat

A user can open a Codex task directly from the Codex sidebar, bypassing Agent Bridge. Until Agent Bridge has an interception or native managed-worktree integration, use all three mitigations:

- retain completed worktrees for a configurable grace period (recommended default: 48 hours);
- leave a tombstone `README.md`/`AGENTS.md` that instructs the task not to modify anything and points to Agent Bridge rehydration;
- make Agent Bridge's “Open in Codex” action rehydrate first.

The tombstone instruction is a safety stop, not a substitute for rehydration.

## Locking

Use locks keyed by the shared Git common directory, not only the visible worktree path.

- Repository mutation lock: worktree registration/removal and metadata-sensitive operations.
- Stack mutation lock: cascade rebase/sync/push for one stack.
- Target integration lock: advancement of one canonical branch.
- Workspace lock: lifecycle changes for one task workspace.

Independent development inside already-provisioned task worktrees does not require the repository mutation lock.

## Cleanup policy

Suggested defaults:

- Successful integrated workspace: retire after 48 hours.
- Pinned conversation: retain until unpinned or explicitly retired.
- Running workspace: never clean automatically.
- Blocked/conflicted workspace: retain until resolved or explicitly abandoned.
- Failed workspace with uncommitted files: retain and alert.
- Abandoned clean workspace: retire immediately after explicit authorization.

Cleanup must support dry-run output listing exact paths, branches, SHAs, state, disk usage, and recoverability.

## Migration from permanent PR worktrees

Migration is a separate user-guided operation and must not be performed as part of implementing this specification.

For every current permanent PR worktree:

1. Inventory path, branch, HEAD, dirty state, untracked files, upstream, and ahead/behind counts.
2. Verify that work is committed and safely referenced.
3. Resolve or preserve dirty work.
4. Remove the permanent worktree while retaining its canonical branch.
5. Designate and validate the control checkout.
6. Import the GitHub stack into local stack metadata.
7. Test a two-layer cascade before enabling whole-stack automation.

## Acceptance criteria

- Two agents can work concurrently against the same canonical PR branch without sharing a worktree or branch.
- No task worktree checks out a canonical stack branch.
- Integration rebases onto the latest target and verifies the rebased result.
- Concurrent integration attempts on the same target serialize safely.
- A completed workspace can be retired, leaving a valid tombstone.
- Opening through Agent Bridge rehydrates the same path before starting a turn.
- A failed rehydration restores the tombstone and does not lose evidence.
- Dirty, running, pinned, blocked, and conflicted workspaces are not automatically deleted.
- Cleanup has a dry-run mode and never accepts an unvalidated broad path.
- Stack tooling can switch among canonical branches from the control checkout after migration.

## Open decisions

- Whether to use Codex-managed worktrees or Agent Bridge-managed Git worktrees initially.
- Exact retention periods and disk-pressure behavior.
- Which ignored files may be copied automatically and how secrets are classified.
- Whether audit workspaces use detached HEADs or named temporary branches.
- How direct Codex-sidebar resume should trigger or advertise rehydration.

