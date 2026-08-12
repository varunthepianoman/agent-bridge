# Flexible collaboration

Milestone 7 adds a durable collaboration registry above the topology-neutral Bridge transport.
It does not require a coordinator hierarchy and it does not replace provider-native subagent
communication.

## Routing semantics

- `direct` and `request` target one or more explicit stable endpoints.
- `reply` preserves the request correlation and identifies the request as its causation.
- `capability` publishes exactly one message to a capability subject. Eligible workers compete
  for that work; the Catalog registry is discovery metadata, not client-side load balancing.
- A durable room publishes exactly one message to the room subject. Each participant owns its
  durable consumer cursor, so offline readers can replay independently.
- `fanout` is the explicit operation that expands a capability or room membership into direct
  deliveries to every currently registered target.

Registered endpoints and rooms retain unknown namespaced extensions. Generic relationship types
also remain open strings, so cross-work dependencies and alternate policy systems can add topology
edges without a core schema change.

## Planner and auditor convention

`proposal -> critique -> revision -> acceptance` is an optional convention. Critiques must cause a
proposal or revision, revisions must cause a critique, and acceptance must cause a proposal or
revision. Every turn preserves one correlation ID. This validation supplies a readable audit trail
without making planner/auditor roles mandatory.

## API

The `/api/v1/collaboration` group provides endpoint and room registration, message submission and
correlation-history queries, native-subagent discovery, and a combined topology projection. The
HTTP caller cannot choose the Bridge sender identity: the trusted hub assigns `catalog-user`.

Inbound inbox, capability, room, and event envelopes are materialized by separate durable
JetStream consumers. Each envelope is committed to SQL before it is acknowledged. Results retain
their specialized execution projection and control/DLQ traffic is deliberately excluded.

Provider-native subagents appear as family nodes and `provider_native_child` edges. They are not
assumed independently addressable unless provider metadata explicitly marks the conversation as
stable and actionable, and their internal parent/child traffic never creates collaboration rows.
