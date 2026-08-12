# ADR 0002: Fall back to persisted Codex sessions when App Server lists none

Status: Accepted

The installed Codex 0.114.0 App Server completed its documented initialization and
`thread/list` protocol but returned no threads while 53 native sessions remained present
and resumable under the same Codex home. A Catalog that accepted that response would fail
its first product requirement.

App Server remains the preferred adapter. If active and archived listing both return zero
and persisted sessions exist, the Codex adapter performs read-only discovery from the native
session JSONL and thread-name index. The fallback copies selected session metadata and only
explicit `user_message` and `agent_message` prose. It excludes commands, tool results,
reasoning, instructions, and configuration content. Provider identities remain unchanged,
so a future App Server release can take over without duplicating Catalog conversations.

