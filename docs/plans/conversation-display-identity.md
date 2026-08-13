# Conversation Numbers, Titles, and Provider Synchronization

Status: proposed implementation specification  
Purpose: make Agent Bridge conversations recognizable without weakening stable identity

## Decision summary

Keep the existing opaque `conv-...` identifier as the internal stable identity. Add a catalog-assigned immutable sequential conversation number and a generated friendly title. Normal UI surfaces show the number and title; opaque catalog and provider IDs move to an advanced technical-details section.

For Codex conversations managed by Agent Bridge, synchronize the friendly title to the native Codex task using app-server `thread/name/set`. User-edited titles must take precedence and must not be overwritten by later sync or role-stage changes.

## Goals

- Let users refer to conversations as “Chat 42” instead of by a long hash.
- Give each work/role conversation a concise, stable, meaningful title.
- Show the same useful title in Agent Bridge and Codex where supported.
- Preserve deterministic deduplication and distributed identity.
- Preserve user title edits.
- Make title provenance and provider-sync state explicit.

## Why the opaque ID remains

The current catalog ID is derived deterministically from provider, provider thread ID, node, and environment. It is suitable for foreign keys, URLs, broker messages, deduplication, and imports. Sequential integers alone are not safe natural identities across nodes or catalog restoration.

Therefore:

```text
conversation_id       internal stable identity: conv-...
conversation_number   human-facing catalog ordinal: 42
provider_thread_id    native Codex/Claude identity
```

The number is presentation identity, not provider identity.

## Number allocation

- Numbers are globally sequential within one authoritative catalog.
- Allocate at first insertion and never change or reuse a number.
- Allocation must be transactional and concurrency-safe.
- A failed insert must not produce two rows with the same number. Gaps are acceptable.
- Imports of an already-known natural conversation key retain the existing number.
- Existing rows are backfilled deterministically by `created_at`, then `last_activity_at`, then opaque `conversation_id` as a stable tie-breaker.
- If catalogs later support multi-writer federation, the catalog authority that owns the merged conversation index owns number allocation. Do not attempt node-local sequences that can collide.

Suggested database fields:

```text
conversation_number       integer, non-null, unique
title                     existing catalog title
title_source              generated | provider | user
generated_title           nullable text
provider_title            existing provider-observed title
provider_title_sync_state not_requested | pending | synced | failed | unsupported | diverged
provider_title_synced_at  nullable timestamp
provider_title_sync_error nullable text
```

A migration may use a dedicated catalog counter/sequence table rather than changing the existing opaque primary key.

## Display format

Agent Bridge should render the number separately from the title:

```text
Chat 42 · PR #1493 · Developer
Chat 43 · PR #1493 · Auditor
Chat 44 · Agent Bridge · Work Coordinator
```

Recommended stable generated title:

```text
<work item short title> · <role label>
```

The UI prepends `Chat <number> ·` rather than storing the number twice in the catalog title.

The native provider title should include the number because Codex does not otherwise know the Agent Bridge ordinal:

```text
Chat 42 · PR #1493 · Developer
```

Titles should describe the durable conversation purpose, not the current stage. A developer conversation reused for implementation, remediation, reply drafting, and publication should remain “PR #1493 · Developer” rather than being renamed at every transition.

## Title generation

Generate a title when Agent Bridge first attaches a conversation to a role/work item and no user-managed title exists.

Inputs, in priority order:

1. Work item short title or PR number.
2. Friendly role label.
3. Optional distinguishing sequence/descriptor when multiple conversations serve the same role.
4. Provider title or preview for unassociated discovered conversations.

Examples:

```text
PR #1493 · Developer
PR #1493 · Auditor
PR #1493 · Developer 2
Agent Bridge startup hardening · Implementer
Unassigned · Investigate reconnect behavior
```

Role labels come from a controlled display-name mapping, not raw underscored enum values.

## Title precedence

Use this precedence:

```text
user title > generated managed title > provider title > transcript preview > Untitled conversation
```

Rules:

- Editing a title in Agent Bridge sets `title_source=user`.
- Sync/import must never overwrite a user title.
- Provider title changes may update `provider_title` but not a generated/user catalog title.
- Attaching a previously unassigned provider conversation may generate a managed title only if the user has not named it.
- Role replacement or work-item reassociation does not silently rename a user-titled conversation.
- Regeneration is an explicit action when title source is generated.

## Codex native-title synchronization

Codex app-server supports `thread/name/set`. For an Agent Bridge-managed Codex conversation:

1. Ensure the provider thread exists on the owning node.
2. Compute the desired native title including `Chat <number>`.
3. Dispatch `thread/name/set` to the owning Codex app-server/adapter.
4. Record success/failure and the synchronized value.
5. Refresh discovery so `provider_title` reflects native state.

Synchronization policy:

- New Agent Bridge-created conversation: synchronize automatically after number/title assignment.
- Generated-title change: synchronize automatically when the thread is reachable.
- User edits catalog title: default to synchronizing, with an explicit UI indication.
- Discovered conversation not managed by Agent Bridge: do not rename automatically.
- Native title differs after successful sync: mark `diverged`; do not fight the user's provider-side rename in a loop.
- Unsupported/offline provider: preserve catalog title and expose retry.

Provider title synchronization must be a provider capability, not Codex-specific branching spread across catalog code:

```text
conversation.read_title
conversation.set_title
```

Claude and future adapters can report unsupported until they implement it.

## API changes

Conversation responses add:

```json
{
  "conversation_id": "conv-...",
  "conversation_number": 42,
  "title": "PR #1493 · Developer",
  "title_source": "generated",
  "provider_title": "Chat 42 · PR #1493 · Developer",
  "provider_title_sync": {
    "state": "synced",
    "synced_at": "...",
    "error": null
  }
}
```

Metadata update supports explicit title behavior:

```json
{
  "title": "PR #1493 · Developer follow-up",
  "sync_provider_title": true
}
```

Recommended additional operations:

```text
POST /api/v1/conversations/{id}/regenerate-title
POST /api/v1/conversations/{id}/sync-provider-title
```

Both operations are idempotent.

## UI changes

### Conversation list

Show:

```text
Chat 42
PR #1493 · Developer
<preview>
```

Do not show `conv-...` in the normal row.

### Conversation detail

- Heading includes `Chat 42` and friendly title.
- Editing clearly indicates whether the title will also be sent to Codex.
- Provider sync state is visible when failed/diverged.
- Native thread ID, opaque catalog ID, node, environment, and copy buttons live under “Technical identity.”

### Role cards and work-item associated conversations

Replace opaque IDs with:

```text
Chat 42 · PR #1493 · Developer
```

The button still routes internally using `conversation_id`.

### Search

Search accepts:

- `42`;
- `Chat 42`;
- title words;
- opaque catalog ID;
- provider thread ID.

Numbers should not be mistaken for PR numbers when the query explicitly includes `Chat`.

## Migration and compatibility

1. Add nullable fields and counter infrastructure.
2. Backfill existing rows deterministically in one migration transaction or resumable maintenance job.
3. Add uniqueness/non-null enforcement after backfill.
4. Mark existing explicitly customized catalog titles as `user` where distinguishable.
5. Mark titles equal to the observed provider title as `provider`; otherwise use conservative `user` classification.
6. Do not automatically rename all historical Codex tasks. Offer a controlled backfill/sync operation with preview.
7. Update protocol and generated TypeScript types.
8. Update graph labels, role cards, work-item links, search, tests, and accessibility labels.

## Failure handling

- Number-allocation failure rolls back conversation insertion.
- Provider title failure does not fail conversation creation.
- Offline node leaves sync pending/retryable.
- Permission or unsupported API marks sync failed/unsupported with a useful reason.
- Provider-side rename after sync marks divergence; it does not trigger an infinite rename loop.
- Two concurrent title edits use optimistic concurrency/version checking or a documented last-writer policy, with user edits winning over generated updates.

## Acceptance criteria

- Every conversation has a unique immutable sequential number.
- Existing opaque IDs remain stable and all current relationships continue to work.
- Normal UI surfaces no longer display long opaque IDs as the primary label.
- Work/role conversations receive useful generated titles.
- A user edit persists across subsequent synchronization and role/work updates.
- New managed Codex conversations receive matching native Codex titles through `thread/name/set`.
- An offline or unsupported provider does not prevent catalog use and exposes retryable status.
- Existing conversations are backfilled deterministically.
- Search works by conversation number, friendly title, catalog ID, and provider ID.
- Role and associated-conversation links display `Chat <number> · <title>` while navigating by stable ID.

## Suggested implementation split

1. Database migration, number allocator, and protocol fields.
2. Deterministic backfill and repository tests.
3. Generated title service and title provenance.
4. UI display conversion and technical-identity disclosure.
5. Provider capability and Codex `thread/name/set` implementation.
6. Sync-status UX, retry, divergence handling, and end-to-end tests.

