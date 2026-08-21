# ADR 0003: Use guarded local Codex owner IPC for active-turn steering

Status: Accepted

Codex App Server supports `turn/steer`, but the App Server process supervised by Agent Bridge does
not own tasks currently running in Codex desktop or VS Code. Starting another turn therefore fails
with an active-writer error and cannot directly steer the owning process.

For opt-in `steer-or-queue` messages, Agent Bridge connects to the current user's Codex IPC socket,
uses version-1 `thread-owner-discovery` to find the native owner, and sends version-1
`thread-follower-steer-turn` to that exact client. The Bridge message ID is reused as Codex's client
user-message ID for outcome verification and duplicate avoidance.

This IPC surface is versioned by Codex but is not currently described in public OpenAI
documentation. The adapter is therefore isolated behind a provider-neutral contract, validates the
socket and framing defensively, and treats missing owners, version rejection, non-steerable turns,
and unavailable IPC as definitive queue fallbacks. A disconnect or timeout after dispatch is
ambiguous: Bridge checks the transcript for the stable client ID and otherwise records
`delivery_uncertain` instead of issuing a potentially duplicate queued turn.

The first implementation is Unix-only and local-Codex-only. Remote Codex nodes and Claude retain
normal durable queue delivery until their provider adapters are designed separately.
