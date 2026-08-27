# Windows NUC Node Setup

This runbook connects a Windows NUC to the existing single-user Agent Bridge Hub for the first
cross-machine test. The existing Linux computer remains the only Hub, catalog, web UI, and NATS
server. The NUC runs only a native Agent Bridge node and the provider CLIs.

The node makes outbound HTTPS requests over Tailscale. It does not need an inbound firewall rule,
a public port, a local NATS server, or NATS credentials.

## Target topology

| Component | Linux Hub computer | Windows NUC |
| --- | --- | --- |
| Catalog/API and web UI | Yes | No |
| NATS JetStream | Yes | No |
| `agent-bridge-node` | Existing local discovery, if desired | Yes |
| Codex/Claude execution | Yes | Yes |
| Tailscale | Serves the Hub over tailnet-only HTTPS | Connects as an HTTPS client |

Use native Windows for this first baseline, especially when the work involves Windows paths,
RobotStudio, or Windows-native development tools. If a later project runs inside WSL, treat WSL as
a separate execution environment with its own node daemon, node ID, and environment ID. Do not let
a Windows-native node claim commands for WSL paths, or vice versa.

## Values used below

Replace these placeholders before running commands:

| Placeholder | Example |
| --- | --- |
| `<hub-tailnet-url>` | `https://my-hub.example.ts.net` |
| `<node-credential>` | One-time credential returned by the Hub |
| `<codex-exe>` | Full path returned by `Get-Command codex.exe` |

The examples use these stable Bridge identities:

```text
node_id: windows-nuc
environment_id: windows-native
display_name: RobotStudio NUC
```

Changing the display name later is harmless. Do not casually change the node or environment IDs;
they are routing identities.

## 1. Prepare the existing Linux Hub

Do this on the Linux Hub computer.

### 1.1 Verify the existing services

For the current development setup, confirm that the API and NATS are healthy before involving the
NUC:

```bash
curl --fail --silent --show-error http://127.0.0.1:58080/api/v1/health
/home/varunkamat/dev/ai-infra/agent-bridge/.venv/bin/agent-bridge nats
```

Keep the API, NATS, and any desired local web development server running for the duration of the
test. The local UI remains at `http://127.0.0.1:5173/`.

The checked-in Compose deployment is the later durable option. It serves the built UI and API
together on port `58080`, but switching to it can create a new catalog database unless the current
state is migrated deliberately. Do not combine that migration with this first cross-machine test.

### 1.2 Publish the API to the tailnet

Install Tailscale on both computers, sign into the same private tailnet, and then run on the Hub:

```bash
tailscale status
tailscale serve --bg 58081
tailscale serve status
```

Tailscale prints an HTTPS URL such as `https://my-hub.example.ts.net`. Record it as
`<hub-tailnet-url>`. `tailscale serve --bg` persists its Serve configuration across Tailscale and
machine restarts. It exposes the API only to the tailnet; it does not make the service public.
See Tailscale's current [Serve command documentation](https://tailscale.com/docs/reference/tailscale-cli/serve)
for configuration and reset behavior.

If the Hub later moves to the Compose deployment, change the Serve target from `58081` to `58080`.
That deployment exposes the built UI and API through one origin.

### 1.3 Provision a credential for the NUC

Provision from the Hub's loopback API rather than from a remote machine. The returned credential is
shown only in this response, so copy it immediately without committing it anywhere:

```bash
curl --fail --silent --show-error \
  -X POST http://127.0.0.1:58080/api/v1/nodes \
  -H 'Content-Type: application/json' \
  -d '{
    "node_id": "windows-nuc",
    "display_name": "RobotStudio NUC",
    "platform": "windows"
  }'
```

If `windows-nuc` already exists, do not provision a second identity. Either reuse its saved
credential or rotate it with the authenticated credential-rotation endpoint.

## 2. Install prerequisites on the Windows NUC

Open a normal PowerShell session as the Windows user who will use Codex.

1. Install Tailscale for Windows and sign into the same tailnet as the Hub.
2. Install Git, Python 3.12 or newer, and Codex CLI.
3. Run `codex` once and complete its normal authentication flow.
4. Install Claude Code only if Claude discovery and delivery are also wanted on this NUC.

The current official Codex installation and Windows guidance are:

- <https://learn.chatgpt.com/docs/codex/cli>
- <https://learn.chatgpt.com/docs/windows/windows-app>

Verify the environment:

```powershell
tailscale status
git --version
py -3.12 --version
codex --version
```

Agent Bridge must run under this same Windows user—not `SYSTEM`—so its `codex app-server` process
sees the same `%USERPROFILE%\.codex` authentication and conversation data as the Codex app/CLI.

### 2.1 Configure the Codex defaults

For the requested default of Sol at medium effort with full access, ensure
`$HOME\.codex\config.toml` contains:

```toml
model = "gpt-5.6-sol"
model_reasoning_effort = "medium"
approval_policy = "never"
sandbox_mode = "danger-full-access"
```

These settings let remotely started and resumed Codex turns operate without an approval prompt
stranding the turn on the NUC. They also grant the agent unrestricted local access, so use this only
on a trusted NUC and trusted repositories.

After Agent Bridge is installed in the next section, add its local MCP server to the same file. TOML
literal strings avoid escaping the Windows path:

```toml
[mcp_servers.agent_bridge]
command = 'C:\dev\agent-bridge\.venv\Scripts\agent-bridge-mcp.exe'
env = { AGENT_BRIDGE_API_URL = '<hub-tailnet-url>/api/v1' }
```

Also append the following guidance to `$HOME\.codex\AGENTS.md`; preserve any instructions already
in that file:

```markdown
# Agent Bridge

Agent Bridge is the optional address book and durable message fabric for Codex and Claude
conversations across machines. Use it when the user asks, when an authenticated Agent Bridge
message arrives, or when another conversation needs a concrete update about shared work.

- Prefer the Agent Bridge MCP tools. Otherwise use
  `C:\dev\agent-bridge\.venv\Scripts\agent-bridge.exe`.
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

Restart Codex after changing `config.toml` so new chats receive the MCP server. This MCP setup is
what makes Bridge directly usable from chats the user starts in Codex, not only from chats launched
by the node daemon. It does not expose the node credential to Codex.

## 3. Install Agent Bridge on the NUC

In PowerShell:

```powershell
New-Item -ItemType Directory -Force C:\dev | Out-Null
git clone https://github.com/varunthepianoman/agent-bridge.git C:\dev\agent-bridge
Set-Location C:\dev\agent-bridge
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
```

Confirm the entry points:

```powershell
.\.venv\Scripts\agent-bridge.exe --help
.\.venv\Scripts\agent-bridge-node.exe --help
```

Find the native Codex executable:

```powershell
(Get-Command codex.exe -ErrorAction Stop).Source
```

Use the resulting `.exe` path as `<codex-exe>`. If the command finds only a PowerShell or `.cmd`
wrapper, locate the underlying `codex.exe` installed by the official package and use that direct
path. The node launches providers without an interactive shell.

## 4. Verify Tailscale connectivity from Windows

In PowerShell:

```powershell
$HubUrl = '<hub-tailnet-url>'
Invoke-RestMethod "$HubUrl/api/v1/health"
```

Do not continue until this returns healthy API and broker state. If it fails:

- confirm `tailscale status` shows both machines;
- confirm `tailscale serve status` on the Hub targets port `58081`;
- confirm the Hub API still answers on `127.0.0.1:58080`;
- check the tailnet ACL permits the NUC to reach the Hub on TCP 443.

No Windows inbound firewall exception should be necessary.

## 5. Configure the Windows node

First create and protect the configuration directory. Run PowerShell as Administrator for this ACL
operation:

```powershell
$ConfigDir = 'C:\ProgramData\AgentBridge'
New-Item -ItemType Directory -Force $ConfigDir | Out-Null
icacls $ConfigDir /inheritance:r /grant:r "$($env:USERNAME):(OI)(CI)F" "SYSTEM:(OI)(CI)F"
```

Return to the normal Codex user. Create `C:\ProgramData\AgentBridge\node-env.ps1` with the following
contents, substituting the Hub URL, node credential, and native provider executable paths:

```powershell
$env:AGENT_BRIDGE_HUB_URL = '<hub-tailnet-url>'
$env:AGENT_BRIDGE_API_URL = '<hub-tailnet-url>/api/v1'
$env:AGENT_BRIDGE_NODE_TOKEN = '<node-credential>'

$env:AGENT_BRIDGE_NODE_ID = 'windows-nuc'
$env:AGENT_BRIDGE_NODE_NAME = 'RobotStudio NUC'
$env:AGENT_BRIDGE_ENVIRONMENT_ID = 'windows-native'
$env:AGENT_BRIDGE_ENVIRONMENT_KIND = 'windows'

$env:AGENT_BRIDGE_NODE_INTERVAL = '10'
$env:AGENT_BRIDGE_HTTP_TIMEOUT = '30'
$env:AGENT_BRIDGE_CODEX_BIN = '<codex-exe>'
$env:AGENT_BRIDGE_CLAUDE_BIN = 'claude.exe'

$env:AGENT_BRIDGE_EXCLUDE_PROVIDERS = ''
$env:AGENT_BRIDGE_EXCLUDE_REPOSITORIES = ''
$env:AGENT_BRIDGE_EXCLUDE_FOLDERS = ''
$env:AGENT_BRIDGE_EXCLUDE_CONVERSATIONS = ''
$env:AGENT_BRIDGE_SYNC_TRANSCRIPTS = '0'
```

Important behavior:

- The Hub URL is the origin only; the node appends `/api/v1/...` itself.
- The general CLI URL includes `/api/v1`. Child Codex/Claude processes inherit it and can reply
  through Bridge.
- A ten-second interval controls discovery, command polling, and heartbeats.
- **Open in Terminal/provider** actions are always permitted when Windows supports the requested
  action. The node still validates the native URL, executable, path, and fixed argument vector.
- Transcript sync starts disabled for the first test. Titles, status, paths, and other catalog
  metadata still synchronize. Enable it later only after confirming the expected privacy boundary.
- Use the exclusion variables before the first sync if any local providers, repositories, folders,
  or conversation IDs must never leave the NUC.

The credential remains in a local ACL-protected file because the node needs it after restart. Never
put it in `config.toml`, `AGENTS.md`, the repository, or a Bridge message.

## 6. Run one reconciliation cycle manually

From a normal PowerShell session as the Codex user:

```powershell
. C:\ProgramData\AgentBridge\node-env.ps1
Set-Location C:\dev\agent-bridge
.\.venv\Scripts\agent-bridge-node.exe --once
```

The command should return JSON with discovery, synchronization, exclusion, and command counts.
On the Linux Hub, verify the new location:

```bash
/home/varunkamat/dev/ai-infra/agent-bridge/.venv/bin/agent-bridge nodes
/home/varunkamat/dev/ai-infra/agent-bridge/.venv/bin/agent-bridge \
  candidates --node windows-nuc
```

The node should be reachable, and Windows chats should appear as candidates. Because transcript
sync is disabled, conversation-history text from the NUC is intentionally blank at this stage.

Add one expendable Windows chat for the smoke test. Use the UI's **Add chats** dialog or resolve its
exact candidate ID and run:

```bash
/home/varunkamat/dev/ai-infra/agent-bridge/.venv/bin/agent-bridge add <conversation-id>
```

Do not enable **Auto-add new chats** until this one-chat privacy and routing test passes.

## 7. Keep the node running with Task Scheduler

Task Scheduler is preferable to a `LocalSystem` Windows service for the baseline because it starts
the node in the same user profile as Codex.

Create `C:\ProgramData\AgentBridge\run-node.ps1`:

```powershell
$ErrorActionPreference = 'Stop'
. C:\ProgramData\AgentBridge\node-env.ps1
Set-Location C:\dev\agent-bridge

$NodeExe = 'C:\dev\agent-bridge\.venv\Scripts\agent-bridge-node.exe'
$LogFile = 'C:\ProgramData\AgentBridge\node.log'
& $NodeExe 2>&1 | Tee-Object -FilePath $LogFile -Append
exit $LASTEXITCODE
```

Register an at-login task from PowerShell running as Administrator:

```powershell
$TaskUser = "$env:USERDOMAIN\$env:USERNAME"
$Runner = 'C:\ProgramData\AgentBridge\run-node.ps1'
$Action = New-ScheduledTaskAction `
  -Execute 'powershell.exe' `
  -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Runner`""
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $TaskUser
$Principal = New-ScheduledTaskPrincipal `
  -UserId $TaskUser `
  -LogonType Interactive `
  -RunLevel Highest
$Settings = New-ScheduledTaskSettingsSet `
  -StartWhenAvailable `
  -RestartCount 999 `
  -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask `
  -TaskName 'Agent Bridge Node' `
  -Description 'Catalog and command node for the Windows NUC' `
  -Action $Action `
  -Trigger $Trigger `
  -Principal $Principal `
  -Settings $Settings `
  -Force

Start-ScheduledTask -TaskName 'Agent Bridge Node'
```

Check it:

```powershell
Get-ScheduledTaskInfo -TaskName 'Agent Bridge Node'
Get-Content C:\ProgramData\AgentBridge\node.log -Tail 100
```

This baseline starts after that user logs in. For unattended RobotStudio work, automatic Windows
login may already satisfy that constraint. A later hardening step can install a proper per-user
service that runs before login, but it must preserve the same `USERPROFILE`, `CODEX_HOME`, provider
authentication, PATH, and network access. Do not simply change the task to run as `SYSTEM`.

## 8. Cross-machine acceptance test

Run the sending commands on the Linux Hub.

### 8.1 Existing Windows chat, round trip

First resolve the selected Windows recipient; never guess an ID from its title or display order:

```bash
/home/varunkamat/dev/ai-infra/agent-bridge/.venv/bin/agent-bridge \
  chats --node windows-nuc
/home/varunkamat/dev/ai-infra/agent-bridge/.venv/bin/agent-bridge \
  show <windows-conversation-id>
```

Open the existing Windows chat yourself and ask it to enter foreground listener mode for its exact
Bridge conversation ID. This creates one intentional provider turn whose pending listener tool call
holds the writer. Then send authenticated mail with a unique correlation ID:

```bash
/home/varunkamat/dev/ai-infra/agent-bridge/.venv/bin/agent-bridge message \
  --chat <windows-conversation-id> \
  --from-chat <linux-source-conversation-id> \
  --operation request \
  --correlation-id windows-nuc-smoke-001 \
  'Reply to the From conversation through Agent Bridge. Use operation reply and preserve this correlation ID. Include the NUC hostname and current working directory.'
```

Pass criteria:

1. The message is durably accepted by the Hub.
2. The active listener receives it without Bridge starting or resuming a provider turn.
3. The message progresses from transport delivery through `received` to `succeeded` processing.
4. The Windows agent sends a reply to the `From` conversation with correlation
   `windows-nuc-smoke-001`.
5. The reply arrives in the Linux source chat and both directions appear in message history.

Mail remaining `pending` is not a provider failure; it means no foreground listener has received
it. Do not blindly requeue `received` mail. Inspect the processing outcome or attention item first,
then use explicit requeue only when replay is safe. Cancel the listener turn or run
`agent-bridge stop-listener <windows-conversation-id>` to release listener ownership.

### 8.2 Start a new Windows chat

Use a real directory that already exists on the NUC. The `--cwd` value is evaluated on the NUC, so
quote the Windows path in the Linux shell:

```bash
/home/varunkamat/dev/ai-infra/agent-bridge/.venv/bin/agent-bridge start \
  --provider codex \
  --node windows-nuc \
  --environment windows-native \
  --cwd 'C:\dev\some-project' \
  --alias 'NUC cross-machine smoke' \
  --model gpt-5.6-sol \
  --effort medium \
  'Report the hostname, current working directory, git branch, and whether Agent Bridge is reachable. Do not modify files.'
```

Pass criteria:

1. The command is routed only to `windows-nuc/windows-native`.
2. Codex runs in the specified Windows directory with Sol at medium effort.
3. The new provider thread ID is returned and reconciled into the catalog.
4. The chat appears in **Add chats**, or directly in Conversations if auto-add is enabled later.
5. After the new chat enters listener mode, follow-up mail works and retains both transport and
   processing history.

### 8.3 Inspect the system

On the Hub:

```bash
/home/varunkamat/dev/ai-infra/agent-bridge/.venv/bin/agent-bridge attention
/home/varunkamat/dev/ai-infra/agent-bridge/.venv/bin/agent-bridge nodes
/home/varunkamat/dev/ai-infra/agent-bridge/.venv/bin/agent-bridge nats
```

Also inspect the dashboard and NATS server log. Confirm that retries, if any, stop after successful
delivery and that each logical request has one completion.

Request a targeted transcript refresh while the Windows chat is open or running:

```bash
/home/varunkamat/dev/ai-infra/agent-bridge/.venv/bin/agent-bridge \
  refresh <windows-conversation-id> --wait-seconds 30
```

The Windows node uses Codex App Server `thread/read(includeTurns=true)` against the owning provider
thread. Pass only if the Hub projection receives sanitized status and committed user/assistant prose
without resuming the task, subscribing to it, acquiring its writer, or returning tool/reasoning
records. A timeout may return queued command metadata; periodic transcript synchronization remains
the fallback.

## 9. Enable normal catalog behavior

After the two smoke tests pass:

1. Decide whether to set `AGENT_BRIDGE_SYNC_TRANSCRIPTS = '1'` on the NUC. Metadata-only cataloging
   is valid if centralized transcript search is not worth the privacy and storage cost.
2. Enable **Auto-add new chats** in the web UI if every newly discovered NUC chat and native
   subagent should enter the selected catalog automatically.
3. Add exclusion patterns before enabling auto-add if any NUC projects should remain local.
4. Restart the scheduled task after configuration changes:

```powershell
Stop-ScheduledTask -TaskName 'Agent Bridge Node'
Start-ScheduledTask -TaskName 'Agent Bridge Node'
```

## 10. Expected limitations in this baseline

- The NUC depends on the Linux Hub being powered on and its API/NATS processes being healthy.
- The Task Scheduler setup starts after the selected Windows user logs in.
- **Open in Terminal** requires Windows Terminal (`wt.exe`) and remains a development feature.
- Windows-native and WSL chats must be represented by distinct nodes/environments.
- The single-user Hub is not a coworker-sharing boundary. Adding another person's computer requires
  the ownership and sharing model in `docs/plans/shared-workspaces.md`.

## Rollback

Stop and remove only the NUC task:

```powershell
Stop-ScheduledTask -TaskName 'Agent Bridge Node'
Unregister-ScheduledTask -TaskName 'Agent Bridge Node' -Confirm:$false
```

On the Hub, disable Tailscale Serve if it is no longer needed:

```bash
tailscale serve reset
```

The NUC credential can remain unused or be rotated. Existing catalog records and message history
are intentionally not deleted by this rollback.
