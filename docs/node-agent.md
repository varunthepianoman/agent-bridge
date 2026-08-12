# Native node agent

The native node process discovers local provider conversations, applies local
exclusions before transmission, synchronizes with the private Catalog hub over
authenticated HTTPS, sends reachability heartbeats, and handles explicit native
open/resume requests for its own environment.

```bash
export AGENT_BRIDGE_HUB_URL=https://agent-bridge.example.ts.net
export AGENT_BRIDGE_NODE_TOKEN='<per-node token>'
export AGENT_BRIDGE_NODE_ID=workstation-wsl
export AGENT_BRIDGE_ENVIRONMENT_ID=ubuntu-dev
export AGENT_BRIDGE_ENVIRONMENT_KIND=wsl
export AGENT_BRIDGE_NATIVE_LAUNCH=1
agent-bridge-node
```

Use `agent-bridge-node --once` for diagnostics or a scheduled collector. The
normal command is a long-lived service and does not require a foreground model
to poll for work.

Local comma-separated exclusions are evaluated before data leaves the node:

- `AGENT_BRIDGE_EXCLUDE_PROVIDERS`
- `AGENT_BRIDGE_EXCLUDE_REPOSITORIES`
- `AGENT_BRIDGE_EXCLUDE_FOLDERS`
- `AGENT_BRIDGE_EXCLUDE_CONVERSATIONS`
- `AGENT_BRIDGE_SYNC_TRANSCRIPTS=0` to withhold transcript text

[`node-agent.env.example`](node-agent.env.example) contains the complete service
configuration surface for both WSL/Linux and Windows installations.

Codex and Claude Code sessions are collected by default when their local records are present.
`AGENT_BRIDGE_CODEX_BIN` and `AGENT_BRIDGE_CLAUDE_BIN` select the native executables used for an
explicit recovery launch. See [`claude-catalog.md`](claude-catalog.md) for the Claude session
privacy and recovery contract.

Non-loopback hubs must use HTTPS. The token is sent only as an Authorization
Bearer credential. Native actions default off, validate their target environment
and path, and report an explicit failure rather than opening a replacement
environment.
