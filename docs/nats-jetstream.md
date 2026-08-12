# Private NATS JetStream broker

Agent Bridge uses one durable NATS JetStream broker as its delivery authority. The broker is
separate from optional coordinator agents: losing a coordinator does not lose queued work,
and using Manual mode does not require a coordinator.

The private deployment stores JetStream data in the `nats-state` volume. Its client and
monitoring ports are available only on the internal container network. Publish the web hub
through Tailscale; do not publish the NATS monitoring port, because NATS monitoring has no
built-in authentication.

See the official NATS documentation for
[JetStream server storage](https://docs.nats.io/running-a-nats-service/configuration),
[subject-level authorization](https://docs.nats.io/running-a-nats-service/configuration/securing_nats/authorization),
and the warning that the
[monitoring endpoint has no authentication](https://docs.nats.io/running-a-nats-service/nats_admin/monitoring).

Copy `deploy/.env.example` to the ignored `deploy/.env`, replace every example password with
an independently generated random value, then start the compose stack. The checked-in
configuration contains a trusted Catalog service identity and two illustrative node
identities. Add a separately credentialed user for each real node and grant only its inbox,
control endpoint, registered capabilities, and rooms. The admin credential is for operator
diagnostics and must not be used by a runner. The Catalog service provisions the Bridge-owned
streams and its result consumer; node users may manage consumers only within subjects they
are permitted to receive.

Subject families are:

- `bridge.v1.inbox.<kind>.<id>` and `bridge.v1.control.<kind>.<id>` for addressed traffic
- `bridge.v1.capability.<id>` for competing capable runners
- `bridge.v1.room.<id>` for explicit fan-out collaboration
- `bridge.v1.result.<correlation-id>` for durable results
- `bridge.v1.event.<topic>` for fan-out operational events
- `bridge.v1.dead.<family>` for terminally failed delivery

Every token is restricted to letters, digits, `_`, and `-`; wildcard or dotted user-provided
identities are rejected before publish. Tailscale protects network reachability, while NATS
credentials and subject permissions provide a distinct application-level boundary.
