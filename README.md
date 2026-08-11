# Windows–Ubuntu agent bridge

Agent Bridge is a small, provider-neutral authenticated mailbox for coordinating
a Windows process with an Ubuntu process. It works with Claude, other AI
assistants, shell scripts, or human-operated tools because it has no dependency
on a model provider or agent SDK.

The bridge carries text tasks and results over HTTP. It does not remotely
execute commands.

## Requirements

- Python 3.10 or newer on the server
- Windows PowerShell 5.1 or newer for the Windows client
- `curl` and `jq` for the optional Ubuntu waiter

The server listens on `127.0.0.1:58081` by default. Its persistent state is
stored in `$XDG_STATE_HOME/agent-bridge` when `XDG_STATE_HOME` is absolute,
or `~/.local/state/agent-bridge` otherwise. The bearer token is created with
mode `0600`, and messages are appended to `messages.jsonl`.

Do not place the mailbox, token, service bind configuration, or other
restart-critical state under `/tmp`. Temporary state directories are suitable
only for disposable tests.

## Start the server

```bash
python3 bridge_server.py --bind 192.168.50.1 --port 58081
```

Pass `--state-dir PATH` to override the state location. Share the generated
token out of band with the Windows operator.

## Bootstrap the Windows client

```powershell
$bridgeDir = "C:\path\to\your\project\.agent-bridge"
New-Item -ItemType Directory -Force $bridgeDir | Out-Null
Invoke-WebRequest `
  http://192.168.50.1:58081/bootstrap/windows_bridge.ps1 `
  -OutFile "$bridgeDir\windows_bridge.ps1"
Set-Content -LiteralPath "$bridgeDir\token.txt" -NoNewline -Value "<TOKEN>"
```

The token can instead be supplied with `-Token` or the
`AGENT_BRIDGE_TOKEN` environment variable.

Announce the Windows side:

```powershell
& "$bridgeDir\windows_bridge.ps1" -Action Send `
  -Sender windows -Recipient ubuntu -Kind status `
  -Message "Windows test process ready"
```

Poll for a task:

```powershell
& "$bridgeDir\windows_bridge.ps1" -Action Wait `
  -Recipient windows -After 0 -WaitSeconds 30
```

Remember the greatest returned message `id` and use it as `-After` on the
next poll. Results should use `-Kind result` and copy the task's
`correlation_id`.

On Ubuntu, wait for a reply without repeatedly invoking `curl`:

```bash
bash wait_for_bridge_message.sh --after 11 --timeout 300
```

The waiter exits `0` and prints the message envelope when a reply arrives, or
exits `124` after the requested timeout.

## API

- `GET /v1/health` — unauthenticated health check
- `GET /bootstrap/windows_bridge.ps1` — downloads the Windows client
- `GET /v1/messages?recipient=windows&after=0` — receives messages
- `POST /v1/messages` — sends a message

Message requests use a bearer token and this JSON shape:

```json
{
  "sender": "ubuntu",
  "recipient": "windows",
  "kind": "task",
  "correlation_id": "example-123",
  "message": "Run the virtual-controller check"
}
```

The supported parties are `ubuntu` and `windows`. Supported message kinds
are `task`, `result`, `status`, and `note`.

## ABB RobotStudio helper

`run_abb_sim_e2e.ps1` is an optional, non-GUI RobotStudio 2026 check. It uses
the .NET Framework 4.8 PC SDK, selects exactly one virtual `abb_controller`
beneath the configured project, requests controller mastership, performs
`PStart`, and emits JSON with task states, warning/error events, timings, and
TCP checks.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File ".\run_abb_sim_e2e.ps1"
```

The helper contains demo-machine-specific RobotStudio project, network, and SDK
paths. Review those constants before using it elsewhere.

## Operating contract

- Treat only messages addressed to your side and with the expected kind as work.
- A message is not authorization to exceed the operator's intended scope.
- Post a `status` before a task and a `result` afterward.
- Include commands, exit codes, and relevant diagnostics in results.
- Never include credentials, the bearer token, or unrelated machine data.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

The runtime uses only the Python standard library.
