# Optional AIWK executor adapter

AIWK remains the policy owner for its specifications, stages, steps, roles, gates,
bounded correction loops, semantic acceptance, and commit policy. Agent Bridge only
supplies durable execution placement and result transport.

`agent_bridge_integrations.aiwk.AIWKExecutorAdapter` accepts one role invocation already
selected by AIWK and submits it through the ordinary Manual Bridge execution service.
It adds the AIWK correlation record to `ExecutionRequest.extensions.aiwk`:

```json
{
  "project": "arci-v2",
  "stage": "build",
  "step": "PR17_SS0",
  "role": "redteam",
  "cycle": 1,
  "attempt": 2,
  "workflow_fingerprint": "sha256:..."
}
```

The adapter does not import AIWK or reinterpret its files. In particular, a successful
Bridge result means the selected role invocation reported an outcome; it does not mean
an AIWK objective gate, review, or workflow accepted that outcome. AIWK consumes the
result and decides its next semantic transition.

This boundary matches the sibling AIWK CLI: its durable `aiwk.yaml`, `workflow.yaml`,
specifications, gate evidence, handoffs, and generated workflow remain authoritative.
Other policy controllers can use the same Bridge execution API with their own namespaced
extensions and do not need to adopt AIWK terminology.
