# Private Hub Deployment

Run the API, web UI, and one logical NATS JetStream service on a trusted machine. Publish only the
web/HTTPS endpoint through Tailscale; keep SQLite, the NATS monitoring port, and broker credentials
private. Nodes authenticate separately with rotatable credentials whose verifiers are stored as
salted hashes.

The checked-in Compose deployment binds the web surface to loopback by default so it can be
published with Tailscale Serve. Back up the SQLite file and JetStream storage together. Health is
available at `/api/v1/health`; NATS diagnostics are available in the web UI and `/api/v1/nats/*`.

The web proxy allows API responses to remain open slightly longer than the one-hour maximum for
mailbox and receipt waits. Keep this timeout at or above both configured wait limits when replacing
the checked-in Nginx configuration; shorter proxy timeouts turn healthy foreground waits into
spurious network failures. Receipt waits are cancellable request-scoped long-polls, not background
jobs, and multiple senders may observe the same receipt without competing for mailbox ownership.

This deployment is single-user. Do not enroll a coworker's machine and assume their chats should
be globally visible. The explicit workspace/sharing model required for that is deferred to
`docs/plans/shared-workspaces.md`.
