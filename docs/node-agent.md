# Node Daemon

`agent-bridge-node` is the unified daemon for one machine or dev-container environment. Every 10
seconds it discovers Codex and Claude chats, applies local exclusions before data leaves the node,
synchronizes candidates to the Hub, claims one fenced native command, reports its result, and
renews its heartbeat.

Required environment:

```text
AGENT_BRIDGE_HUB_URL=https://agent-bridge.example.ts.net
AGENT_BRIDGE_NODE_TOKEN=<one-time provisioned credential>
AGENT_BRIDGE_NODE_ID=work-laptop
AGENT_BRIDGE_ENVIRONMENT_ID=host
```

Optional controls include `AGENT_BRIDGE_NODE_INTERVAL` (default 10), provider binary paths,
transcript sync, and provider/repository/folder/conversation exclusions. Non-loopback Hub URLs must
use HTTPS.

Commands are executed only when their `environment_id` matches the daemon. There is no fallback to
another environment. Message turns and agent starts preserve provider configuration and approval
behavior; explicit native UI launch additionally requires `AGENT_BRIDGE_NATIVE_LAUNCH=1`.

Use `agent-bridge-node --once` from a Codex or Claude lifecycle hook to accelerate reconciliation.
The periodic loop remains the repair mechanism if hooks are missing or fail.

For a native Windows machine joining an existing private Hub through Tailscale, follow
[`windows-nuc-node-setup.md`](windows-nuc-node-setup.md).
