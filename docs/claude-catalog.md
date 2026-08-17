# Claude Code Catalog adapter

The Catalog discovers Claude Code sessions from the owning node's local
`~/.claude/projects` records. This is a read-only adapter: it does not start Claude, read provider
credentials, or expose a provider service over the network. Root conversations and native
subagents are assigned stable provider locators and retain their root/child relationship.

Only user prose and assistant `text` blocks enter the searchable transcript. Tool calls, tool
results, thinking blocks, attachments, queue operations, and malformed partial records are
excluded. The same provider, repository, folder, conversation, and transcript exclusion settings
used by the node agent apply before synchronized data leaves the machine.

The exact recovery command is `claude --resume SESSION_ID` from the original working directory.
For a native Claude subagent, the Catalog resumes the owning root session; it does not pretend that
the child is an independently resumable endpoint. Native launch remains user-triggered and refuses
to fall back to another environment when the owning node is unavailable.

Codex and Claude are equal catalog and conversation-turn providers. The Claude adapter is a
Catalog discovery and native-recovery adapter, not authorization for autonomous Claude execution.
