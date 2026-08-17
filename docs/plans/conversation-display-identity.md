# Conversation Display Identity

Status: implemented by migration `0008`

Each provider thread is identified by the tuple `(provider, provider_thread_id, node_id,
environment_id)`, from which Bridge derives a deterministic `conversation_id`. Selection allocates
an immutable integer `conversation_number`. The human label is:

```text
Chat <conversation_number> · <alias>
```

`provider_title` records the current provider-owned title. `alias` is Bridge-owned presentation
metadata. A human alias edit wins until an actual provider title change occurs; that provider event
then becomes the newest writer. The provenance and timestamp are stored. Merely reconciling an
unchanged provider title does not overwrite a human edit.

Internal hashes and provider thread IDs remain available in detail/API views but are never used as
the short primary label. Native subagents receive their own number if selected and retain explicit
parent and delivery-mode facts.
