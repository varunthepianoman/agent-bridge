# Shared Workspaces and Multi-User Add-On

Status: follow-on plan; not part of the single-user core  
Updated: 2026-08-14

## Framing

Multi-user support is an add-on to the single-user architecture, not a rewrite and not a return to
AI-organization modeling. The core remains a conversation directory, message fabric, and attention
surface. This add-on introduces explicit ownership, visibility, and administrative boundaries when
another person joins the same private Tailscale network.

No multi-user behavior should be inferred merely because a second node connects. Until a shared
workspace is deliberately created and a node is enrolled, each installation remains a private
single-user catalog.

## Recommended first topology

For a coworker in Norway on the same tailnet:

1. Keep one logical Hub and one NATS service reachable through Tailscale HTTPS/TCP.
2. Install the existing node daemon on the coworker's selected machines.
3. Create a principal and a workspace membership at the Hub.
4. Enroll each node with a principal-scoped credential and explicit workspace.
5. Let the coworker choose which discovered chats to publish into that workspace.

Their entire local provider history must **not** be indexed centrally by default. Discovery remains
local; central selection is explicit and can be revoked. Transcript sharing should be a separate
choice from metadata sharing.

A separate broker per person is not advisable initially. It adds cross-account routing,
deduplication, subject translation, replay ownership, distributed authorization, and operational
debugging without improving the common case. One private broker with workspace-scoped subjects and
credentials is substantially simpler.

## Domain additions

- `principal`: a human or service identity.
- `workspace`: an explicit sharing/visibility boundary.
- `workspace_membership`: principal role (`owner`, `member`, `viewer`) and status.
- `node_enrollment`: binds node, principal, workspace, credential, and allowed environments.
- `conversation_share`: workspace, conversation, metadata/transcript visibility, sender permissions,
  and revocation time.
- `room_membership`: principal- and conversation-level room permissions.
- `audit_event`: append-only admin, sharing, message, credential, and visibility changes.

Core rows gain `workspace_id` and `owner_principal_id` only where ownership is meaningful. A
single-user migration creates one implicit personal workspace/principal, preserving existing IDs
and behavior.

## Authorization

Every API request and message resolves an authenticated principal. Authorization is evaluated at
the Hub before catalog reads, message publication, native commands, room membership changes, or
transcript access.

Suggested defaults:

- Metadata is private until explicitly shared.
- Transcript text is private even when metadata is shared, unless separately enabled.
- A member may message only shared conversations that allow incoming messages.
- Only the owning principal may open a native UI or start a native turn unless delegated.
- Node credentials are bound to one node, principal, and workspace; rotation invalidates the old
  credential.
- Cross-workspace rooms and implicit global search are forbidden.

## Subject layout

Introduce workspace tokens without exposing human email/name in subjects:

```text
bridge.v2.workspace.<workspace_id>.inbox.conversation.<conversation_id>
bridge.v2.workspace.<workspace_id>.room.<room_id>
bridge.v2.workspace.<workspace_id>.event.<topic>
bridge.v2.workspace.<workspace_id>.dead.<family>
```

NATS accounts or scoped users restrict publish/subscribe to one workspace prefix. The Hub still
validates application-level membership because broker permissions alone cannot express transcript
visibility, native-action authority, or revoked conversation shares.

## Broker availability versus federation

### High availability cluster

If the one logical broker needs machine-failure tolerance, deploy a normal three-node NATS
JetStream cluster:

- stable Tailscale DNS names and routes for all three servers;
- `cluster {}` routes and unique `server_name` values;
- stream replica count 3;
- odd quorum and persistent disks;
- clients configured with all server URLs;
- one authority source for accounts/users/credentials;
- monitoring, backup, restore, and loss-of-quorum runbooks.

Agent Bridge's transport already accepts multiple URLs and a replica count, but deployment,
credential rotation, upgrade order, disaster recovery, and live cluster acceptance tests remain to
be implemented. This is moderate infrastructure work, not a change to product semantics.

### Federation / multiple independent brokers

Independent brokers are a later and materially harder feature. NATS leaf nodes or gateways can
connect brokers, but Bridge must additionally define:

- globally unique workspace/message identities;
- authoritative home broker per workspace;
- loop prevention and replay cursor ownership;
- subject and identity mapping;
- conflict handling for alias, attention, room, and revocation changes;
- durable delivery and dead-letter ownership across partitions;
- trust establishment and credential revocation between administrators;
- data residency and transcript replication rules.

Do not treat a NATS mesh configuration as sufficient product federation. Implement it only after
real use shows that one logical workspace service cannot meet organizational or residency needs.

## UI changes

- Workspace switcher in the top navigation; personal workspace remains the default.
- Conversation sharing panel showing owner, metadata/transcript visibility, message permission,
  and revocation.
- People and node-enrollment administration for owners.
- Attention items identify workspace and actor.
- Message/NATS logs show principal and workspace while redacting credentials/payloads.
- Search is workspace-scoped unless the user explicitly chooses an authorized cross-workspace
  view.

## Delivery phases

1. Add the implicit personal principal/workspace and authorization middleware with no behavior
   change.
2. Add principal login, memberships, and workspace-scoped catalog reads.
3. Add node enrollment and explicit metadata/transcript sharing.
4. Add authorized direct messages, rooms, audit events, and revocation.
5. Add three-node cluster deployment and failure tests if availability warrants it.
6. Evaluate leaf-node/gateway federation only from demonstrated multi-broker requirements.

## Acceptance

- Joining a tailnet or enrolling a node never publishes all local chats automatically.
- An unauthorized principal cannot discover metadata, read transcripts, send messages, or trigger
  native actions.
- Revocation blocks new access and delivery without deleting the owner's private history.
- Every cross-user message and administrative change has an attributable audit event.
- A personal single-user installation behaves exactly as before without multi-user setup.
