# Hardening, retention, and recovery

The private hub is single-user, but every node and broker integration still has a distinct
credential and least-privilege subject permissions. Do not expose the HTTP or NATS listeners to
the public internet. Tailscale and the private reverse proxy remain part of the security boundary.

## Retention and transcript deletion

Run retention explicitly (or schedule it outside the application):

```bash
agent-bridge-maintenance retention --broker-days 30 --collaboration-days 180
```

Only old terminal operational rows are removed. Active requests, unresolved dead letters, pending
collaboration, durable role checkpoints, reports, decisions, and work metadata are retained.
Artifact references can carry `retention_until` and `sensitivity`; deletion of the referenced URI
is the owning artifact store's responsibility.

Delete synchronized transcript content with either:

```bash
agent-bridge-maintenance delete-transcript CONVERSATION_ID
```

or `DELETE /api/v1/conversations/{conversation_id}/transcript`. This clears transcript text,
provider turn/message payloads, and the corresponding FTS content while preserving the stable
conversation identity and recovery locator. A later provider synchronization can re-import text;
configure the node/environment exclusion policy first when deletion must remain permanent.

## Backup and restore drill

SQLite backups use its online backup API and are integrity-checked before success is reported:

```bash
agent-bridge-maintenance backup /secure/backups/catalog-2026-08-11.db
agent-bridge-maintenance verify /secure/backups/catalog-2026-08-11.db
agent-bridge-maintenance restore /secure/backups/catalog-2026-08-11.db /staging/catalog.db
```

Restore never overwrites an existing database. Stop the hub, restore to a new path, verify the
reported Alembic revision, point `AGENT_BRIDGE_DATABASE_URL` at that path, and start the hub. Then
check health, node reachability, pending executions, coordinator leases, dead letters, and consumer
lag before declaring recovery complete. Reconcile the Catalog after recovery; do not purge
JetStream until central projections have caught up.

## Credential rotation

Node credentials rotate through `POST /api/v1/nodes/{node_id}/credentials/rotate`, authenticated
with the current node credential. The replacement is returned once and invalidates the old secret
atomically. Update the node's secret store immediately, restart it, and verify heartbeat/sync before
discarding the captured replacement.

NATS rotation is an operator procedure, not an HTTP action:

1. Generate a new per-node credential and add it with the same narrow permissions.
2. Update that node's secret file or environment and restart only that node.
3. Verify its heartbeat, consumers, delivery, ACKs, and result publication.
4. Revoke the old broker credential and verify it can no longer connect.
5. Repeat one node at a time; rotate the Catalog credential last.

Never log credentials or pass them on command lines. Structured payloads should pass through the
recursive redactor before logging; conventional authorization, password, token, and credential
fields become `[REDACTED]`.

## Failure recovery checklist

- Expire stale coordinator activations and reacquire their fenced role leases.
- Inspect pending executions, retry state, dead letters, and NATS consumer lag.
- Reconcile provider conversations and node environment registrations.
- Confirm transcript exclusions before synchronizing a recovered node.
- Re-run an idempotent dry-run request before allowing external side effects.
- Preserve broker data until every durable consumer has caught up or the loss is explicitly
  accepted.
