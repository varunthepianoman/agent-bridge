# NATS JetStream

The single-user Hub owns one logical NATS service and three streams:

- `BRIDGE_WORK_V1`: `bridge.v1.inbox.>` and `bridge.v1.room.>`;
- `BRIDGE_EVENTS_V1`: `bridge.v1.event.>`;
- `BRIDGE_DLQ_V1`: `bridge.v1.dead.>`.

The Hub publishes and consumes conversation/room mail. Broker delivery appends durable mailbox
records; it never invokes a provider runtime. Remote nodes communicate with the Hub over
authenticated HTTPS and do not need broker credentials. This keeps explicit provider-turn
authority and catalog visibility in one place.

Messages use `Nats-Msg-Id` for broker de-duplication, explicit acknowledgement, bounded attempts,
backoff, expiry, and durable dead-letter publication before source ACK. The Hub stores an
operational projection and a user-facing NATS activity log without storing credentials. Broker
acknowledgement is a transport event and is independent from mailbox claim, agent acknowledgment,
or processing outcome. A Broker ACK is never presented as proof that an agent read a message.

Configure one server with `AGENT_BRIDGE_NATS_SERVERS`. For a three-node JetStream cluster, provide
all client URLs and set `AGENT_BRIDGE_NATS_REPLICAS=3`. Cluster deployment automation is not yet
included; see the availability section of the shared-workspaces follow-on plan.
