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
another environment. Codex starts and turns use one supervised `codex app-server` process; Claude
retains its subprocess implementation. The node reports a new Codex task after `thread/start` and
`turn/start` are accepted, then reports initial-turn completion separately. Native UI and terminal
launches are always permitted when the host supports them and still pass scheme, platform, path,
and argv validation.

Use `agent-bridge-node --once` from a Codex or Claude lifecycle hook to accelerate reconciliation.
The periodic loop remains the repair mechanism if hooks are missing or fail.

For a native Windows machine joining an existing private Hub through Tailscale, follow
[`windows-nuc-node-setup.md`](windows-nuc-node-setup.md).
For the Ubuntu ABB robot controller machine, follow
[`abb-t-box-linux-node-setup.md`](abb-t-box-linux-node-setup.md).

Remote-command lease recovery, node-side result journaling, and resilient long-turn supervision are
specified as a follow-on in
[`plans/remote-command-reliability.md`](plans/remote-command-reliability.md).
