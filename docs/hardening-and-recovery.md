# Hardening and Recovery

- Keep the Hub and broker on a private tailnet; use HTTPS for every non-loopback node.
- Give each node a distinct credential and rotate it through the node credential endpoint.
- Apply local discovery exclusions before synchronization; transcript sharing can be disabled per
  environment.
- Use `agent-bridge-maintenance backup`, `verify`, and `restore`. Restore never overwrites an
  existing database.
- Apply retention to acknowledged broker projections, delivered conversation messages, resolved
  dead letters, and NATS events.
- Delete a selected transcript through the conversation transcript endpoint; this also strips
  provider transcript fields from retained raw metadata and rebuilds FTS.
- Migration `0008` is not reversible in place. It stores retired tables as JSON in
  `legacy_exports`; use a verified pre-migration backup to run old code.
- Treat the NATS activity page as operational metadata. Payload bodies remain in the conversation
  message table, while credentials and conventional secret fields are redacted.
