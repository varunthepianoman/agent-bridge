# ABB simulator E2E over the durable Bridge

The ABB RobotStudio workflow is addressed by the stable capability
`robot-simulator-e2e`. A request is stored in JetStream even when the Windows machine is offline.
After reconnection, one eligible runner claims it, executes `run_abb_sim_e2e.ps1`, publishes
progress and a terminal result, and only then acknowledges the input. The sender does not poll and
does not need to remain active.

The PowerShell payload remains deliberately fail-closed: it requires exactly one matching virtual
controller under the configured RobotStudio project, performs the bounded restart and task probes,
and emits one structured JSON result. It is not a messaging client and does not contain broker
credentials.

## Configure the Windows runner

Install the package and provide a runner configuration readable only by the runner account. The
capability command is an argv array, never a shell command. Adapt the repository path to the local
checkout. A complete template is checked in at
[`deploy/runner/robotstudio.example.json`](../deploy/runner/robotstudio.example.json):

```json
{
  "node_id": "robotstudio-windows",
  "state_path": "C:\\ProgramData\\AgentBridge\\runner.db",
  "capabilities": {
    "robot-simulator-e2e": {
      "argv": [
        "pwsh",
        "-NoProfile",
        "-NonInteractive",
        "-File",
        "C:\\dev\\agent-bridge\\run_abb_sim_e2e.ps1"
      ],
      "allowed_workspaces": ["C:\\dev\\agent-bridge"],
      "timeout_seconds": 900
    }
  }
}
```

Start it as a supervised native process:

```powershell
agent-bridge-runner serve --config C:\ProgramData\AgentBridge\runner.json
```

`AGENT_BRIDGE_RUNNER_CONFIG` may provide the config path instead. Supply that node's NATS
credentials through the runner's protected environment or credentials-file setting. The broker
permissions restrict this runner to the ABB/server-client capability subjects and its own inbox,
control, progress, result, and dead-letter subjects. Do not place credentials in the request body,
runner JSON checked into source control, or test output.

The runner reads `AGENT_BRIDGE_NATS_SERVERS` and either
`AGENT_BRIDGE_NATS_CREDENTIALS_FILE` or the paired `AGENT_BRIDGE_NATS_USERNAME` /
`AGENT_BRIDGE_NATS_PASSWORD`. Its SQLite state path must be durable across service restarts. Work
uses one shared durable consumer per capability, so only one eligible runner performs the side
effect. Capability control uses a separate durable consumer per node, so a cancellation reaches the
actual owner even when another eligible runner is online.

## Submit and observe a request

The checked-in request can be submitted unchanged through the headless CLI:

```shell
agent-bridge --api-url https://agent-bridge.example/api/v1 \
  request-submit --json examples/abb-robot-simulator-e2e.request.json
```

The Manual Bridge form provides the same fields: choose **Capability**, enter
`robot-simulator-e2e`, select **Invoke adapter**, and use the same adapter identity. The generated
subject is `bridge.v1.capability.robot-simulator-e2e`; custom subjects are unnecessary.

Record the returned `execution_id`. Inspect high-level state in the focused work or Operations
view, or headlessly:

```shell
agent-bridge execution-status EXECUTION_ID
```

Queued means the durable request is waiting for an eligible runner. Claimed/running state includes
the attempt and lease. A successful result contains the parsed PowerShell JSON under runner output;
failures retain bounded diagnostics and follow the request's retry/dead-letter policy. Cancellation
uses `agent-bridge execution-cancel EXECUTION_ID` and is delivered on the capability control
subject.

## Migration boundary

Milestone 9 intentionally has no compatibility adapter. The removed mailbox endpoints,
hard-coded two-party identities, JSONL storage, bootstrap client, shared bearer token, and
foreground polling loop are not supported. Existing automation must submit the durable request
above or use the equivalent Manual UI operation.

The Catalog HTTP API remains the authoritative command/query interface at `/api/v1`; it is not the
retired mailbox protocol. NATS JetStream remains delivery authority.

## Acceptance check

1. Stop the Windows runner and submit the checked-in request.
2. Confirm the execution remains queued and consumer lag is visible in Operations.
3. Restart NATS and the Catalog; confirm the same execution remains queued.
4. Start the Windows runner and confirm exactly one runner claims the request.
5. Confirm progress is visible, then a structured terminal result is persisted.
6. Redeliver the same JetStream message and confirm the completed side effect is not repeated.
7. Confirm no foreground Codex/Claude turn or polling shell is required.

Steps that require RobotStudio, node credentials, or service deployment are an operator acceptance
run; the repository tests cover contract routing, durability/idempotency primitives, and removal of
the obsolete protocol surface.
