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

Optional controls include `AGENT_BRIDGE_NODE_INTERVAL` (default 10),
`AGENT_BRIDGE_MAX_PROVIDER_CONCURRENCY` (default 4), provider binary paths, transcript sync, and
provider/repository/folder/conversation exclusions. Non-loopback Hub URLs must use HTTPS.

Commands are executed only when their `environment_id` matches the daemon. There is no fallback to
another environment. Provider commands are serialized per conversation and bounded by the node-wide
provider concurrency limit. Codex starts and turns use one supervised `codex app-server` process; Claude
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

## Codex writer release and live inspection

Use Codex **0.154.0 or newer** on every machine that executes Bridge turns. Bridge launches
its App Server with `-c thread_unload_delay_secs=0`. After a turn finishes, Bridge unsubscribes
and Codex unloads that idle thread without an inactivity grace period, releasing its writer
for `codex resume`. Unloading remains asynchronous; active turns and subscribed threads stay
loaded. Other conversations do not require a server restart.

Codex 0.153.4 hard-codes a 30-minute no-subscriber inactivity delay and does not support this
setting. Passing the override to an older binary is insufficient. Upgrade the actual executable
in `AGENT_BRIDGE_CODEX_BIN`, not just a different `codex` found on an interactive shell's PATH.
Restart the node service once, after its turns and any needed server-owned background processes
are finished, so it launches the upgraded binary with the new startup setting.

For an older Bridge installation, a deployment can set `thread_unload_delay_secs = 0` at the
**top level** of the service user's Codex `config.toml` after upgrading Codex. Preserve existing
settings, place it before any TOML table, and restart the idle node. The current Bridge launch
override makes this manual setting unnecessary. Retain the previous binary/configuration for
rollback; rolling back below 0.154.0 restores the writer-delay limitation.

A running chat still has one writer: opening a second `codex resume` TUI cannot attach as a
read-only viewer. To inspect it without interrupting it, use:

```bash
agent-bridge refresh <conversation-id>
agent-bridge refresh <conversation-id> --last-message-only
```

Refresh uses `thread/read`, which does not acquire the writer. After completion, use the normal
resume command. Never delete rollout or lock files to force a handoff.

The opt-in regression test runs the real Codex binary against a local mock model with an isolated
Codex home and no credentials or external model requests:

```bash
AGENT_BRIDGE_TEST_CODEX_BIN=/absolute/path/to/codex PYTHONPATH=src \
  python -m pytest -q tests/test_codex_unload_integration.py
```

It verifies active-turn preservation, read-only inspection while the writer is held, writer
release on completion, resume by a second server, and preservation of a separate subscribed thread.
