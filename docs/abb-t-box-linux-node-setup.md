# ABB T-Box Ubuntu Node Setup

This runbook connects the Ubuntu ABB robot T-Box to the existing single-user Agent Bridge Hub. The
T-Box runs Acteris, the ARCI Adapter, and software that can control a physical robot, so the first
acceptance tests are intentionally read-only and must not command motion, write controller state,
restart robot services, or change robot software.

The existing Linux development computer remains the only Hub, catalog, web UI, and NATS server.
The T-Box runs only a native Agent Bridge node and provider CLIs. It makes outbound HTTPS requests
over Tailscale; it does not expose a port or receive NATS credentials.

## Where each phase runs

| Phase | Run on | Outcome |
| --- | --- | --- |
| A. Publish this runbook | Existing Hub computer | T-Box can clone the documented revision |
| B. Prepare the Hub | Existing Hub computer | Healthy Hub and one `abb-t-box` credential |
| C. Install the node | ABB T-Box | Codex and Agent Bridge installed for the robot user |
| D. Validate and persist | ABB T-Box, then Hub | Node heartbeat, candidate discovery, systemd persistence |
| E. Smoke test | Send from Hub; execute on T-Box | Existing-chat and new-chat correlated round trips |

Do not run a second Hub or NATS server on the T-Box.

## Stable identities and URLs

This runbook uses:

```text
Hub URL:       https://t-vkamat-01-t16g4-ubnt24.tail92f516.ts.net
Node ID:       abb-t-box
Node name:     ABB T-Box
Environment:   ubuntu-native
Environment kind: linux
```

The node ID and environment ID are durable routing identities. Changing the display name later is
harmless; do not casually change the IDs after conversations have synchronized.

Replace these placeholders:

| Placeholder | Meaning |
| --- | --- |
| `<tbox-user>` | Existing non-root Ubuntu user who owns the Codex sessions |
| `<node-credential>` | Credential returned once by Hub provisioning |
| `<safe-project-path>` | Existing T-Box code directory safe for a read-only smoke test |
| `<hub-source-conversation-id>` | Exact selected conversation on the Hub that should receive replies |
| `<tbox-conversation-id>` | Exact selected existing T-Box chat used for the test |

## Robot safety boundary

Agent Bridge appends incoming messages to a durable mailbox and never starts a provider turn.
Mailbox safety does not add a robot-specific authorization layer once an agent intentionally reads
and acts on mail. On this machine:

- run Codex and `agent-bridge-node` as the normal robot-development user, never as `root`;
- expect desktop deep links to fail as unsupported on the headless T-Box while explicit provider
  turns and foreground mailbox listeners remain available;
- use read-only smoke prompts until message routing is proven;
- do not ask the smoke agent to invoke Acteris, the ARCI Adapter, RobotWare, EGM, controller RPCs,
  fieldbus/I/O, safety signals, or robot motion;
- do not put credentials, controller secrets, or production data in Bridge messages;
- use local exclusion rules before first sync for any directory or conversation that must not be
  represented in the central catalog;
- treat Full Access as unrestricted OS access for that user, even though the node itself is not
  root.

These precautions constrain the acceptance test, not ordinary future development. Decide
separately when remote agent turns are trusted to interact with live robot processes.

## Phase A — Publish the runbook from the Hub computer

On the existing Hub computer, commit and push this document before cloning on the T-Box:

```bash
cd /home/varunkamat/dev/ai-infra/agent-bridge
git status --short --branch
git push third main
```

The T-Box will clone:

```text
https://github.com/varunthepianoman/agent-bridge.git
```

## Phase B — Prepare the existing Hub computer

Run this entire phase on the existing Hub computer.

### B1. Verify Hub, broker, UI, and Tailscale

```bash
systemctl --user --no-pager --full status \
  agent-bridge-nats.service \
  agent-bridge-api.service \
  agent-bridge-web.service

curl --fail --silent --show-error \
  http://127.0.0.1:58080/api/v1/health

curl --fail --silent --show-error \
  https://t-vkamat-01-t16g4-ubnt24.tail92f516.ts.net/api/v1/health

tailscale serve status
/home/varunkamat/dev/ai-infra/agent-bridge/.venv/bin/agent-bridge nats
```

Required health properties are:

```json
{
  "status": "ok",
  "broker_configured": true,
  "broker_connected": true,
  "background": "healthy"
}
```

Tailscale Serve should proxy tailnet-only HTTPS to `http://127.0.0.1:58080`. Do not expose NATS
ports `4222` or `8222` to the network.

### B2. Decide initial catalog policy

For the first synchronization, disable automatic selection if it is currently enabled. This keeps
all historical T-Box chats from immediately entering the selected catalog:

```bash
curl --fail --silent --show-error \
  -X PATCH \
  -H 'Content-Type: application/json' \
  -d '{"auto_add_new_chats":false}' \
  http://127.0.0.1:58080/api/v1/settings
```

The T-Box configuration below also starts with transcript synchronization disabled. Conversation
metadata will synchronize, but transcript text will remain local until deliberately enabled.

### B3. Provision the ABB T-Box credential

First confirm that `abb-t-box` does not already exist:

```bash
/home/varunkamat/dev/ai-infra/agent-bridge/.venv/bin/agent-bridge nodes
```

If it is new, provision it through the Hub loopback API:

```bash
curl --fail --silent --show-error \
  -X POST http://127.0.0.1:58080/api/v1/nodes \
  -H 'Content-Type: application/json' \
  -d '{
    "node_id": "abb-t-box",
    "display_name": "ABB T-Box",
    "platform": "linux"
  }'
```

The response shows the node credential once. Transfer it directly into the T-Box environment file
in Phase C; do not commit it, paste it into a chat, or send it through Bridge.

If the node already exists, do not create another identity. Reuse the saved credential or rotate
the existing credential deliberately.

## Phase C — Install on the ABB T-Box

Run this entire phase on the Ubuntu T-Box as `<tbox-user>`, except commands explicitly marked
`sudo`.

### C1. Record the host baseline

```bash
hostnamectl
cat /etc/os-release
uname -a
id
tailscale status
python3 --version
git --version
```

Save the Ubuntu release and Python version. Agent Bridge requires Python 3.12 or newer. Do not
replace Ubuntu's system Python on a robot controller.

On Ubuntu 24.04, the normal packages are sufficient:

```bash
sudo apt-get update
sudo apt-get install --yes git curl ca-certificates python3.12 python3.12-venv
```

If this T-Box is on an older Ubuntu release without an approved Python 3.12 package, stop and choose
an isolated Python 3.12 installation appropriate for the controller image. Do not change
`/usr/bin/python3`, remove vendor packages, or upgrade the OS as part of Agent Bridge setup.

### C2. Join the tailnet

If Tailscale is not already installed, use the current official Linux installation instructions at
<https://tailscale.com/download/linux>. Then authenticate the T-Box into the same private tailnet
as the Hub.

Verify that the Hub is reachable without opening any inbound T-Box port:

```bash
tailscale status
curl --fail --silent --show-error \
  https://t-vkamat-01-t16g4-ubnt24.tail92f516.ts.net/api/v1/health
```

Do not continue unless the response reports a connected broker and healthy background service.

### C3. Install and authenticate Codex CLI

Use OpenAI's current standalone installer for macOS/Linux:

```bash
curl -fsSL https://chatgpt.com/codex/install.sh | sh
```

Open a new login shell, then verify the resolved executable and version:

```bash
exec "$SHELL" -l
command -v codex
readlink -f "$(command -v codex)"
codex --version
```

Run `codex` once and choose **Sign in with ChatGPT**, or another sign-in method offered by the CLI:

```bash
codex
```

The current official Codex CLI guidance is at
<https://learn.chatgpt.com/docs/codex/cli>.

The absolute path returned by `readlink -f` becomes `<codex-bin>` below. Pinning the absolute path
prevents a systemd PATH or confined package from using a different Codex installation/session
store.

### C4. Configure Codex and Agent Bridge guidance

Ensure `$HOME/.codex/config.toml` retains any existing settings and includes the requested defaults:

```toml
model = "gpt-5.6-sol"
model_reasoning_effort = "medium"
approval_policy = "never"
sandbox_mode = "danger-full-access"
```

Full Access removes Codex filesystem/process restrictions for this non-root user. On a live robot
controller, do not assume that non-root means inconsequential: this user may still own robot
services, configuration, sockets, or deployment files.

After Agent Bridge is installed in C5, add its local MCP server to the same file:

```toml
[mcp_servers.agent_bridge]
command = '/home/<tbox-user>/dev/agent-bridge/.venv/bin/agent-bridge-mcp'
env = { AGENT_BRIDGE_API_URL = 'https://t-vkamat-01-t16g4-ubnt24.tail92f516.ts.net/api/v1' }
```

Append the following section to `$HOME/.codex/AGENTS.md`, preserving existing instructions:

```markdown
# Agent Bridge

Agent Bridge is the optional address book and durable message fabric for Codex and Claude
conversations across machines. Use it when the user asks, when an authenticated Agent Bridge
message arrives, or when another conversation needs a concrete update about shared work.

- Prefer the Agent Bridge MCP tools. Otherwise use
  `/home/<tbox-user>/dev/agent-bridge/.venv/bin/agent-bridge`.
- Before sending, use `agent-bridge chats` and, when needed,
  `agent-bridge show <conversation-id>` to resolve the recipient. Never guess an ID from a title.
- Send directly with `agent-bridge message --chat <target-id> --from-chat <source-id>
  --operation <operation> --correlation-id <correlation-id> "<message>"`.
- Enter listener mode only when asked by calling `wait_mailbox` (or `agent-bridge wait`) with this
  chat's exact Bridge conversation ID. Process every returned item, record `succeeded`, `blocked`,
  or `failed`, then wait again until the foreground turn is cancelled.
- Treat From, Message, Correlation, and Operation as delivery metadata. Reply to From with operation
  `reply` and the same correlation ID. Do not send secrets. Hub acceptance, receipt, and completion
  are separate states; never assume accepted mail has been processed.
```

Restart newly launched Codex processes after changing `config.toml`; existing processes do not
automatically acquire a newly configured MCP server.

### C5. Clone and install Agent Bridge

Choose a normal user-owned development location:

```bash
mkdir -p "$HOME/dev"
git clone https://github.com/varunthepianoman/agent-bridge.git \
  "$HOME/dev/agent-bridge"
cd "$HOME/dev/agent-bridge"
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
```

Verify the installed commands:

```bash
.venv/bin/agent-bridge --help
.venv/bin/agent-bridge-node --help
test -x .venv/bin/agent-bridge-mcp
```

### C6. Configure the native node

Create and protect its configuration directory:

```bash
mkdir -p "$HOME/.config/agent-bridge"
chmod 700 "$HOME/.config/agent-bridge"
```

Create `$HOME/.config/agent-bridge/abb-t-box.env` with the following contents. Replace
`<node-credential>`, `<tbox-user>`, and `<codex-bin>`:

```bash
AGENT_BRIDGE_HUB_URL=https://t-vkamat-01-t16g4-ubnt24.tail92f516.ts.net
AGENT_BRIDGE_API_URL=https://t-vkamat-01-t16g4-ubnt24.tail92f516.ts.net/api/v1
AGENT_BRIDGE_NODE_TOKEN=<node-credential>

AGENT_BRIDGE_NODE_ID=abb-t-box
AGENT_BRIDGE_NODE_NAME='ABB T-Box'
AGENT_BRIDGE_ENVIRONMENT_ID=ubuntu-native
AGENT_BRIDGE_ENVIRONMENT_KIND=linux

AGENT_BRIDGE_NODE_INTERVAL=10
AGENT_BRIDGE_HTTP_TIMEOUT=30
AGENT_BRIDGE_CODEX_BIN=<codex-bin>
AGENT_BRIDGE_CLAUDE_BIN=claude

AGENT_BRIDGE_EXCLUDE_PROVIDERS=
AGENT_BRIDGE_EXCLUDE_REPOSITORIES=
AGENT_BRIDGE_EXCLUDE_FOLDERS=
AGENT_BRIDGE_EXCLUDE_CONVERSATIONS=
AGENT_BRIDGE_SYNC_TRANSCRIPTS=0
```

Then:

```bash
chmod 600 "$HOME/.config/agent-bridge/abb-t-box.env"
```

Important behavior:

- `AGENT_BRIDGE_HUB_URL` is the HTTPS origin; the node appends `/api/v1/...`.
- `AGENT_BRIDGE_API_URL` includes `/api/v1` and is inherited by Bridge-launched providers.
- The absolute Codex path must point to the installation authenticated by `<tbox-user>`.
- Desktop open actions fail based on the headless platform's actual launcher availability. There is
  no separate native-launch policy switch; message delivery and provider starts still work.
- Transcript sync remains off until the catalog and privacy boundary are verified.
- Exclusions are applied locally before data leaves the T-Box.

## Phase D — Validate and persist the T-Box node

### D1. Run one manual cycle on the T-Box

```bash
set -a
. "$HOME/.config/agent-bridge/abb-t-box.env"
set +a

cd "$HOME/dev/agent-bridge"
.venv/bin/agent-bridge-node --once
```

Expected output is a JSON object containing discovery, synchronization, exclusion, command, and
failure counts. The first run should have zero commands and zero command failures.

### D2. Verify from the Hub computer

Return to the existing Hub computer:

```bash
/home/varunkamat/dev/ai-infra/agent-bridge/.venv/bin/agent-bridge nodes
/home/varunkamat/dev/ai-infra/agent-bridge/.venv/bin/agent-bridge \
  candidates --node abb-t-box
```

Confirm:

- node `abb-t-box` is reachable;
- environment `ubuntu-native` is available;
- discovered T-Box chats appear only as candidates;
- transcript text is absent because initial transcript sync is disabled.

Select one expendable existing T-Box chat for the smoke test using the UI's **Add chats** dialog or
its exact candidate ID:

```bash
/home/varunkamat/dev/ai-infra/agent-bridge/.venv/bin/agent-bridge \
  add <tbox-conversation-id>
```

### D3. Install a direct systemd user service on the T-Box

Create `$HOME/.config/systemd/user/agent-bridge-node.service`:

```ini
[Unit]
Description=Agent Bridge node for ABB T-Box
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/dev/agent-bridge
EnvironmentFile=%h/.config/agent-bridge/abb-t-box.env
ExecStart=%h/dev/agent-bridge/.venv/bin/agent-bridge-node
Restart=always
RestartSec=5
TimeoutStopSec=30
KillMode=control-group

[Install]
WantedBy=default.target
```

Enable it:

```bash
systemctl --user daemon-reload
systemctl --user enable --now agent-bridge-node.service
systemctl --user --no-pager --full status agent-bridge-node.service
journalctl --user -u agent-bridge-node.service --since today --no-pager
```

The service launches the node executable directly. Do not insert a shell, `tee`, or another logging
pipeline between systemd and the node. Logs belong in the systemd journal.

If the node must start at boot and remain available without an interactive login, enable lingering
for `<tbox-user>`:

```bash
sudo loginctl enable-linger <tbox-user>
loginctl show-user <tbox-user> -p Linger
```

### D4. Reboot recovery check

When a reboot is operationally safe for the robot cell, reboot the T-Box and verify:

```bash
systemctl --user is-active agent-bridge-node.service
journalctl --user -u agent-bridge-node.service -b --no-pager
```

On the Hub, confirm `abb-t-box` becomes reachable again. Do not reboot merely to finish the initial
message smoke test if doing so would disrupt robot work; this check may be deferred.

## Phase E — Cross-machine smoke test

Send all test messages from the existing Hub computer. Run them only after the T-Box node is
reachable and one existing T-Box chat has been selected.

### E1. Resolve both conversation identities

Never guess IDs from titles or UI order:

```bash
/home/varunkamat/dev/ai-infra/agent-bridge/.venv/bin/agent-bridge \
  chats --node abb-t-box
/home/varunkamat/dev/ai-infra/agent-bridge/.venv/bin/agent-bridge \
  show <tbox-conversation-id>

/home/varunkamat/dev/ai-infra/agent-bridge/.venv/bin/agent-bridge \
  chats --query '<Hub source chat title>'
/home/varunkamat/dev/ai-infra/agent-bridge/.venv/bin/agent-bridge \
  show <hub-source-conversation-id>
```

### E2. Existing T-Box chat mailbox round trip

Open the existing T-Box chat yourself and ask it to enter foreground listener mode for its exact
Bridge conversation ID. The pending listener tool call owns that existing turn's writer; Agent
Bridge will not inject a competing turn. Then send mail with a unique correlation ID:

```bash
/home/varunkamat/dev/ai-infra/agent-bridge/.venv/bin/agent-bridge message \
  --chat <tbox-conversation-id> \
  --from-chat <hub-source-conversation-id> \
  --operation request \
  --correlation-id abb-t-box-existing-001 \
  'Read-only Agent Bridge smoke test. Do not modify files, invoke robot/controller APIs, restart services, or command motion. Reply to the From conversation through Agent Bridge using operation reply and preserve this correlation ID. Include hostname, current working directory, Codex version, and exact text ABB_T_BOX_EXISTING_PONG.'
```

Pass criteria:

1. The T-Box node remains reachable while the foreground listener runs.
2. The request reaches transport state `delivered`, then processing state `received` and
   `succeeded`.
3. The T-Box agent reports the expected hostname and cwd.
4. The Hub receives `ABB_T_BOX_EXISTING_PONG` with correlation `abb-t-box-existing-001`.
5. No files, services, controller state, or robot state change.

Cancel the listener turn or run `agent-bridge stop-listener <tbox-conversation-id>` and verify its
listener becomes offline before opening another provider turn.

### E3. Start a new T-Box Codex chat

Use an existing user-owned code directory. The path is interpreted on the T-Box:

```bash
/home/varunkamat/dev/ai-infra/agent-bridge/.venv/bin/agent-bridge start \
  --provider codex \
  --node abb-t-box \
  --environment ubuntu-native \
  --cwd '<safe-project-path>' \
  --alias 'ABB T-Box cross-machine smoke' \
  --model gpt-5.6-sol \
  --effort medium \
  'Read-only smoke test. Do not modify files, invoke robot/controller APIs, restart services, or command motion. Report hostname, current working directory, git branch, and Agent Bridge reachability. Reply to the requesting Hub conversation only if its exact conversation ID and correlation are provided.'
```

Pass criteria:

1. The command routes only to `abb-t-box/ubuntu-native`.
2. Codex runs as `<tbox-user>` in `<safe-project-path>`.
3. The returned provider thread is reconciled into the central catalog.
4. The chat appears in candidates, or in Conversations if auto-add is enabled later.
5. After the new chat enters listener mode, fresh correlated mail and a reply work without
   restarting the node.

### E4. Inspect diagnostics

On the Hub:

```bash
/home/varunkamat/dev/ai-infra/agent-bridge/.venv/bin/agent-bridge attention
/home/varunkamat/dev/ai-infra/agent-bridge/.venv/bin/agent-bridge nodes
/home/varunkamat/dev/ai-infra/agent-bridge/.venv/bin/agent-bridge nats
```

On the T-Box:

```bash
systemctl --user --no-pager --full status agent-bridge-node.service
journalctl --user -u agent-bridge-node.service --since '30 minutes ago' --no-pager
```

## Enable normal catalog behavior

After both smoke tests pass:

1. Add local exclusion patterns for projects, folders, or conversations that must remain private.
2. Decide whether centralized transcript search is worth enabling
   `AGENT_BRIDGE_SYNC_TRANSCRIPTS=1`.
3. Enable **Auto-add new chats** in the Hub UI if every future T-Box chat and native subagent should
   become selected automatically.
4. Restart the node after environment-file changes:

```bash
systemctl --user restart agent-bridge-node.service
```

## Current reliability limitation

The current node is workable for cataloging, attention, mailbox exchange, and occasional explicit
remote provider commands. A Linux systemd service with `Restart=always` should recover the daemon
after process failure without manual intervention.

If the Hub/Tailscale connection or provider process disappears after an explicit `start`, `turn`, or
refresh command is claimed, that command may require inspection or a fresh request. This limitation
does not make mailbox delivery start a provider turn. A stuck claimed command does not prevent later
commands once the node is healthy. Do not depend on the current build for guaranteed exactly-once
execution of unattended consequential robot actions.

The planned durable fix is documented in
[`plans/remote-command-reliability.md`](plans/remote-command-reliability.md).

## Troubleshooting

### Node is unreachable

```bash
systemctl --user status agent-bridge-node.service
journalctl --user -u agent-bridge-node.service -n 200 --no-pager
curl --fail --silent --show-error \
  https://t-vkamat-01-t16g4-ubnt24.tail92f516.ts.net/api/v1/health
```

Restart only after capturing logs:

```bash
systemctl --user restart agent-bridge-node.service
```

### Hub reports `no rollout found`

Confirm that the service uses the exact Codex binary and same user/profile that created the chat:

```bash
systemctl --user show agent-bridge-node.service -p Environment -p ExecStart
command -v codex
readlink -f "$(command -v codex)"
codex --version
printf 'HOME=%s\nCODEX_HOME=%s\n' "$HOME" "${CODEX_HOME:-<unset>}"
```

Correct `AGENT_BRIDGE_CODEX_BIN` in `abb-t-box.env`, then restart the service.

### Conversation history is blank

This is expected while `AGENT_BRIDGE_SYNC_TRANSCRIPTS=0`. Metadata, titles, status, paths, and
message history can still synchronize. Set it to `1` only after deciding to centralize transcript
text.

For a one-off safe read, request an owning-node refresh from the Hub:

```bash
/home/varunkamat/dev/ai-infra/agent-bridge/.venv/bin/agent-bridge \
  refresh <tbox-conversation-id> --wait-seconds 30
```

The T-Box node must use a read-only provider operation and return only sanitized status plus
explicit user/assistant prose. It must not resume the chat, subscribe to its live stream, acquire
its writer, or relay tool/reasoning records.

### Mail remains `pending`

Check the correlation and listener health in the Hub UI. `pending` means no listener has received
the item; it does not mean a provider turn failed. Confirm the target agent is waiting on the exact
conversation ID. Do not requeue a `received` item automatically: inspect its outcome/attention and
use explicit `requeue` only after deciding replay is safe.

## Rollback

On the T-Box:

```bash
systemctl --user disable --now agent-bridge-node.service
```

If lingering was enabled only for Agent Bridge:

```bash
sudo loginctl disable-linger <tbox-user>
```

Removing the service does not delete provider chats, Hub catalog records, or durable message
history. Keep the credential file until deciding whether to re-enroll; rotate the credential if it
may have been exposed.
