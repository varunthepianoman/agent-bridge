# ADR 0004: Separate durable mail from provider turns

Status: Accepted

## Context

Delivering an Agent Bridge message by simulating a provider user turn creates a second potential
writer for a Codex or Claude conversation. A human may already have the task open, and the provider
may already own an active turn. Retrying, queueing, or steering does not remove that ownership
ambiguity and can corrupt or duplicate conversational state.

Read-only observation has a different safety profile. Codex App Server `thread/read` can return the
stored task and committed turns without resuming or subscribing to it. Local session JSONL also
grows during active work, but includes sensitive tool and reasoning records and is not the public
cross-machine contract.

## Decision

Agent Bridge messages are durable mailbox records only. Sending mail cannot invoke provider
runtime, resume, steering, or turn-start code. Room membership uses `mailbox`, `notify`, or `digest`;
none of those modes starts a provider turn. `turn` and `steer` remain explicit provider operations
with no implicit fallback from messaging.

An agent opts into foreground listener mode by calling `wait_mailbox` with its exact Bridge
conversation ID. The blocking tool call holds the writer already owned by that turn. The Hub allows
one live listener per mailbox, uses a durable cursor and heartbeat, and returns ordered batches of
mail as structured tool context. Cancelling the provider turn or requesting `stop_listener` ends
the wait. An idle conversation is never automatically awakened.

Message transport and processing are independent:

- transport is `queued`, `published`, `delivered`, or `failed`;
- processing is `pending`, `received`, `succeeded`, `blocked`, or `failed`.

Receipt is atomic when a listener claims a batch. The agent later records one terminal processing
outcome, optionally with detail and a correlated reply. Received but unfinished mail is not
automatically replayed; after the grace period it creates one deduplicated attention item. Requeue
is always explicit.

Targeted transcript refresh routes a read-only command to the owning node. Codex nodes use
`thread/read(includeTurns=true)` and validate conversation, node, and environment identity before
updating the Hub projection. Only explicit user and assistant prose plus sanitized status are
returned; reasoning, tool calls, tool output, credentials, and raw JSONL metadata are excluded.

## Consequences

- Humans can keep ordinary conversations open without racing Bridge delivery.
- Agents receive mail only while intentionally listening; completion and attention notifications
  remain outside provider transcripts while the agent is idle.
- Mail acceptance, agent receipt, and work completion are independently observable and auditable.
- Targeted refresh improves cross-machine visibility but exposes committed items, not guaranteed
  token-level live deltas.
- Automatic wake, automatic listener restart, outer/inner relationships, and live delta relay are
  deferred.
