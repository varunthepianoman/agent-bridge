# ADR 0001: Keep Codex App Server local over stdio

Status: Accepted

The Codex provider adapter supervises `codex app-server` as a local subprocess
and communicates using its newline-delimited stdio protocol. Catalog services do
not expose or depend on App Server's experimental remote WebSocket transport.

This keeps provider credentials and session state on their owning node, gives the
Catalog structured thread operations, and leaves cross-machine transport to the
Catalog synchronization and Agent Bridge protocols. Programmatic agent turns will
use the Codex SDK in the later runner milestone. Explicit human handoff continues
to use the supported `codex resume` command.

