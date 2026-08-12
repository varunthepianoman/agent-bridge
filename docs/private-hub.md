# Private Catalog hub

The Catalog hub is a single-user deployment: one API and SQLite database behind one web
origin. Its container port is bound to loopback by default. Tailscale supplies the private
network boundary and HTTPS identity; the Codex App Server is never exposed by this stack.

## Start locally

From the repository root:

```bash
cp deploy/.env.example deploy/.env
docker compose --env-file deploy/.env -f deploy/compose.yaml up --build -d
curl --fail http://127.0.0.1:58080/api/v1/health
```

The named `catalog-state` volume contains the SQLite database. The API is reachable only
through the web reverse proxy and is not published as a host port.

## Publish privately with Tailscale

After confirming the loopback health check, publish that loopback listener to the current
tailnet using the host's Tailscale installation:

```bash
tailscale serve --bg http://127.0.0.1:58080
tailscale serve status
```

Do not change `AGENT_BRIDGE_HUB_BIND` to `0.0.0.0` on an untrusted network. Tailnet ACLs
should grant access only to the user and node identities that need the Catalog. Node-agent
credentials remain distinct from Tailscale identity and are issued by the hub; never put a
node credential in `deploy/.env` or the browser bundle.

## Availability behavior

The hub stores synchronized Catalog data even while a source node is offline. Open/resume
always targets the conversation's owning node and environment. If that environment is not
reachable, the API returns an explicit failure and the UI keeps the action disabled; neither
layer silently substitutes the hub or another machine.

Back up the named volume before upgrades. A tested backup/restore and credential-rotation
procedure is documented in [`hardening-and-recovery.md`](hardening-and-recovery.md); this compose file is intended for
private development use rather than unattended production operation.
