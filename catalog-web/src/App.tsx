import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bell,
  Bot,
  Check,
  CircleAlert,
  CircleStop,
  ExternalLink,
  Inbox,
  MessageSquare,
  Monitor,
  Plus,
  RadioTower,
  RefreshCw,
  RotateCcw,
  Search,
  Send,
  SquareTerminal,
  Users,
} from "lucide-react";
import {
  acknowledgeAttention,
  attentionItems,
  catalogSettings,
  conversationCandidates,
  coreConversation,
  coreConversations,
  coreMessages,
  createRoom,
  importConversations,
  listNodes,
  mailbox,
  natsActivity,
  natsSummary,
  openCoreConversation,
  reconcileCore,
  refreshConversation,
  requeueMailboxMessage,
  rooms,
  sendCoreMessage,
  sendProviderTurn,
  stopMailboxListener,
  updateCatalogSettings,
} from "./api";
import type { MailboxSnapshot } from "./types";

type Section = "conversations" | "attention" | "messages" | "rooms" | "nodes" | "nats";

export function App() {
  const cache = useQueryClient();
  const [section, setSection] = useState<Section>("conversations");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<string>();
  const [composer, setComposer] = useState("");
  const [turnPrompt, setTurnPrompt] = useState("");
  const [turnEffort, setTurnEffort] = useState<"" | "low" | "medium" | "high" | "xhigh" | "max" | "ultra">("");
  const [openFeedback, setOpenFeedback] = useState<string>();
  const [adding, setAdding] = useState(false);
  const [candidateSelection, setCandidateSelection] = useState<Set<string>>(new Set());
  const conversations = useQuery({
    queryKey: ["core-conversations", search],
    queryFn: () => coreConversations(search),
    refetchInterval: 10_000,
  });
  const preferences = useQuery({ queryKey: ["catalog-settings"], queryFn: catalogSettings });
  const attention = useQuery({ queryKey: ["attention"], queryFn: attentionItems, refetchInterval: 5_000 });
  const unread = attention.data?.items.filter((item) => !item.acknowledged).length ?? 0;
  const detail = useQuery({
    queryKey: ["core-conversation", selected],
    queryFn: () => coreConversation(selected!),
    enabled: Boolean(selected),
  });
  const inbox = useQuery({
    queryKey: ["mailbox", selected],
    queryFn: () => mailbox(selected!),
    enabled: Boolean(selected),
    refetchInterval: 5_000,
  });
  const candidates = useQuery({
    queryKey: ["candidates"],
    queryFn: conversationCandidates,
    enabled: adding,
  });
  const refresh = () => Promise.all([
    cache.invalidateQueries({ queryKey: ["core-conversations"] }),
    cache.invalidateQueries({ queryKey: ["candidates"] }),
  ]);
  const reconcile = useMutation({ mutationFn: reconcileCore, onSuccess: refresh });
  const importMutation = useMutation({
    mutationFn: () => importConversations([...candidateSelection]),
    onSuccess: async () => { await refresh(); setAdding(false); setCandidateSelection(new Set()); },
  });
  const send = useMutation({
    mutationFn: () => sendCoreMessage({ body: composer, target_conversation_id: selected }),
    onSuccess: async () => { setComposer(""); await cache.invalidateQueries({ queryKey: ["messages"] }); },
  });
  const sendTurn = useMutation({
    mutationFn: () => sendProviderTurn(selected!, {
      prompt: turnPrompt,
      ...(turnEffort ? { effort: turnEffort } : {}),
    }),
    onSuccess: () => { setTurnPrompt(""); setOpenFeedback("Provider turn queued explicitly."); },
    onError: (error) => setOpenFeedback(error instanceof Error ? error.message : "Provider turn failed."),
  });
  const refreshTranscript = useMutation({
    mutationFn: () => refreshConversation(selected!),
    onSuccess: async (result) => {
      setOpenFeedback(result.detail ?? (result.command_id ? "Remote transcript refresh queued." : "Transcript refreshed."));
      await cache.invalidateQueries({ queryKey: ["core-conversation", selected] });
    },
    onError: (error) => setOpenFeedback(error instanceof Error ? error.message : "Transcript refresh failed."),
  });
  const stopListener = useMutation({
    mutationFn: () => stopMailboxListener(selected!),
    onSuccess: async (result) => {
      setOpenFeedback(result.detail ?? "Listener stop requested.");
      await cache.invalidateQueries({ queryKey: ["mailbox", selected] });
    },
    onError: (error) => setOpenFeedback(error instanceof Error ? error.message : "Could not stop listener."),
  });
  const requeue = useMutation({
    mutationFn: requeueMailboxMessage,
    onSuccess: async () => {
      await Promise.all([
        cache.invalidateQueries({ queryKey: ["mailbox", selected] }),
        cache.invalidateQueries({ queryKey: ["messages"] }),
      ]);
    },
  });
  const openNative = useMutation({
    mutationFn: ({ id, target }: { id: string; target: "desktop" | "terminal" }) => openCoreConversation(id, target),
    onSuccess: (result) => setOpenFeedback(result.detail ?? "Native conversation opened."),
    onError: (error) => setOpenFeedback(error instanceof Error ? error.message : "Native open failed."),
  });
  const updatePreferences = useMutation({
    mutationFn: updateCatalogSettings,
    onSuccess: (value) => cache.setQueryData(["catalog-settings"], value),
  });
  const nav: Array<[Section, string, React.ReactNode]> = [
    ["conversations", "Conversations", <Inbox size={18} />],
    ["attention", `Attention${unread ? ` · ${unread}` : ""}`, <Bell size={18} />],
    ["messages", "Messages", <MessageSquare size={18} />],
    ["rooms", "Rooms (in dev)", <Users size={18} />],
    ["nodes", "Machines (WIP)", <Monitor size={18} />],
    ["nats", "NATS", <RadioTower size={18} />],
  ];
  return <div className="hub-shell">
    <aside className="hub-nav">
      <div className="hub-brand"><span className="brand-mark"><Bot size={19} /></span><div><strong>Agent Bridge</strong><small>Private conversation network</small></div></div>
      <nav>{nav.map(([key, label, icon]) => <button className={section === key ? "active" : ""} onClick={() => setSection(key)} key={key}>{icon}<span>{label}</span></button>)}</nav>
      <div className="hub-status"><span className="online-dot" /> Single-user hub</div>
    </aside>
    <main className="hub-main">
      {section === "conversations" && <>
        <header className="page-head"><div><h1>Conversations</h1><p>Your selected Codex and Claude chats across every machine.</p></div><div className="head-actions"><label className="auto-add"><input type="checkbox" checked={preferences.data?.auto_add_new_chats ?? false} disabled={!preferences.data || updatePreferences.isPending} onChange={(event) => updatePreferences.mutate({ auto_add_new_chats: event.target.checked })} /> Auto-add new chats</label><button onClick={() => reconcile.mutate()}><RefreshCw size={16} /> Reconcile</button><button className="primary" onClick={() => setAdding(true)}><Plus size={16} /> Add chats</button></div></header>
        <div className="conversation-layout">
          <section className="directory-pane"><label className="search-box"><Search size={17} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search alias, title, project, machine…" /></label>
            <div className="chat-list">{conversations.data?.items.map((chat) => <button key={chat.conversation_id} className={selected === chat.conversation_id ? "chat-card selected" : "chat-card"} onClick={() => { setSelected(chat.conversation_id); setOpenFeedback(undefined); }}><span className={`provider ${chat.provider}`}>{providerBadge(chat.provider)}</span><span><strong>{chat.display_name}</strong><small>{chat.provider} · {chat.node_id}/{chat.environment_id}</small><em>{chat.preview || "No preview available"}</em></span><i className={`state ${chat.status}`} /></button>)}</div>
          </section>
          <section className="detail-pane">{detail.data ? <><div className="detail-title"><div><span className="eyebrow">{detail.data.provider} · {detail.data.conversation_kind.replace("_", " ")}</span><h2>{detail.data.display_name}</h2><p>{detail.data.provider_title && detail.data.provider_title !== detail.data.alias ? `Provider title: ${detail.data.provider_title}` : detail.data.cwd}</p></div><div className="open-actions"><button className="primary" disabled={!detail.data.native_url || openNative.isPending} onClick={() => openNative.mutate({ id: detail.data.conversation_id, target: "desktop" })}>Open in {detail.data.provider === "claude" ? "Claude (in dev)" : "Codex"}</button><button disabled={!detail.data.capabilities.can_open || openNative.isPending} onClick={() => openNative.mutate({ id: detail.data.conversation_id, target: "terminal" })}><SquareTerminal size={15} /> Open in Terminal{detail.data.provider === "codex" ? " (in dev)" : ""}</button><button disabled={refreshTranscript.isPending} onClick={() => refreshTranscript.mutate()}><RefreshCw size={15} /> {refreshTranscript.isPending ? "Refreshing…" : "Refresh transcript"}</button></div></div>
            {openFeedback && <p className="action-feedback" role="status">{openFeedback}</p>}
            <div className="facts"><span><small>Machine</small>{detail.data.node_id}</span><span><small>Environment</small>{detail.data.environment_id}</span><span><small>Delivery</small>{detail.data.delivery_mode}</span><span><small>Status</small>{detail.data.status}</span></div>
            <pre className="transcript">{detail.data.transcript_text || detail.data.preview || "Transcript will appear after the next reconciliation."}</pre>
            <MailboxPanel snapshot={inbox.data} loading={inbox.isLoading} stopping={stopListener.isPending} requeueing={requeue.isPending} onStop={() => stopListener.mutate()} onRequeue={(id) => requeue.mutate(id)} />
            <section className="send-panel mailbox-send"><div><strong>Send to mailbox</strong><small>Durable mail only. This does not wake, resume, or write to the provider conversation.</small></div><form className="composer" onSubmit={(event) => { event.preventDefault(); if (composer.trim()) send.mutate(); }}><textarea value={composer} onChange={(event) => setComposer(event.target.value)} placeholder="Write a durable mailbox message…" /><button className="primary" disabled={!composer.trim() || !detail.data.capabilities.can_message || send.isPending}><Send size={16} /> Send mail</button></form></section>
            <details className="provider-controls"><summary><ExternalLink size={15} /> Explicit provider controls</summary><p>Starting a provider turn acquires the conversation writer. Use only when you intend to resume the agent.</p><form className="composer provider-composer" onSubmit={(event) => { event.preventDefault(); if (turnPrompt.trim()) sendTurn.mutate(); }}><textarea value={turnPrompt} onChange={(event) => setTurnPrompt(event.target.value)} placeholder="Prompt for a new provider turn…" /><select aria-label="Reasoning effort" value={turnEffort} onChange={(event) => setTurnEffort(event.target.value as typeof turnEffort)}><option value="">Default effort</option>{["low", "medium", "high", "xhigh", "max", "ultra"].map((effort) => <option value={effort} key={effort}>{effort}</option>)}</select><button className="danger-action" disabled={!turnPrompt.trim() || !detail.data.capabilities.can_receive_turn || sendTurn.isPending}><ExternalLink size={16} /> Start provider turn</button></form></details>
          </> : <div className="empty"><MessageSquare size={34} /><h2>Select a conversation</h2><p>Inspect its location, status, transcript, and message history.</p></div>}</section>
        </div>
      </>}
      {section === "attention" && <AttentionView />}
      {section === "messages" && <MessagesView />}
      {section === "rooms" && <RoomsView />}
      {section === "nodes" && <NodesView />}
      {section === "nats" && <NatsView />}
    </main>
    {adding && <div className="modal-backdrop"><div className="modal"><header><div><h2>Add discovered chats</h2><p>Select existing chats here. Auto-add controls chats first discovered later.</p></div><button onClick={() => setAdding(false)}>×</button></header><div className="modal-actions"><button onClick={() => setCandidateSelection(new Set(candidates.data?.items.map((item) => item.conversation_id)))}>Select all current</button><span>{candidateSelection.size} selected</span></div><div className="candidate-list">{candidates.data?.items.map((chat) => <label key={chat.conversation_id}><input type="checkbox" checked={candidateSelection.has(chat.conversation_id)} onChange={() => setCandidateSelection((current) => { const next = new Set(current); next.has(chat.conversation_id) ? next.delete(chat.conversation_id) : next.add(chat.conversation_id); return next; })} /><span><strong>{chat.alias}</strong><small>{chat.provider} · {chat.node_id}/{chat.environment_id}</small></span></label>)}</div><footer><button onClick={() => setAdding(false)}>Cancel</button><button className="primary" disabled={!candidateSelection.size} onClick={() => importMutation.mutate()}>Add {candidateSelection.size || ""} chats</button></footer></div></div>}
  </div>;
}

function AttentionView() {
  const cache = useQueryClient();
  const query = useQuery({ queryKey: ["attention"], queryFn: attentionItems, refetchInterval: 5_000 });
  const acknowledge = useMutation({ mutationFn: acknowledgeAttention, onSuccess: () => cache.invalidateQueries({ queryKey: ["attention"] }) });
  return <><PageTitle title="Attention" subtitle="Things that need you, separated from ordinary completion updates." /><div className="attention-grid">{["needs_attention", "update"].map((category) => <section className="panel" key={category}><h2>{category === "needs_attention" ? <><CircleAlert size={18} /> Needs attention</> : <><Check size={18} /> Updates</>}</h2>{query.data?.items.filter((item) => item.category === category).map((item) => <article className={item.acknowledged ? "attention-card read" : "attention-card"} key={item.attention_id}><strong>{item.title}</strong><p>{item.detail}</p><small>{new Date(item.created_at).toLocaleString()}</small>{!item.acknowledged && <button onClick={() => acknowledge.mutate(item.attention_id)}>Acknowledge</button>}</article>)}</section>)}</div></>;
}

function MessagesView() {
  const query = useQuery({ queryKey: ["messages"], queryFn: coreMessages, refetchInterval: 5_000 });
  return <><PageTitle title="Message history" subtitle="Durable mailbox traffic with separate transport and processing outcomes." /><section className="panel table-panel"><table><thead><tr><th>Time</th><th>Route</th><th>Message</th><th>Correlation</th><th>Transport</th><th>Processing</th></tr></thead><tbody>{query.data?.items.map((item) => <tr key={item.message_id}><td>{new Date(item.created_at).toLocaleString()}</td><td>{item.target_conversation_id || item.room_id}</td><td title={item.outcome_detail || item.processing_detail}>{item.body}</td><td className="mono">{item.correlation_id}</td><td><span className={`pill ${item.transport_state ?? item.state}`}>{item.transport_state ?? item.state}</span></td><td><span className={`pill ${item.processing_state ?? "pending"}`}>{item.processing_state ?? "pending"}</span>{item.reply_message_id && <small className="reply-link">reply {item.reply_message_id}</small>}</td></tr>)}</tbody></table></section></>;
}

function RoomsView() {
  const cache = useQueryClient(); const [name, setName] = useState("");
  const query = useQuery({ queryKey: ["rooms"], queryFn: rooms });
  const create = useMutation({ mutationFn: () => createRoom(name), onSuccess: async () => { setName(""); await cache.invalidateQueries({ queryKey: ["rooms"] }); } });
  return <><PageTitle title="Rooms (in dev)" subtitle="Lightweight broadcast channels with mailbox, notify, or digest delivery. Room mail never starts a provider turn." /><form className="inline-create" onSubmit={(event) => { event.preventDefault(); if (name.trim()) create.mutate(); }}><input value={name} onChange={(event) => setName(event.target.value)} placeholder="New room name" /><button className="primary"><Plus size={16} /> Create</button></form><div className="card-grid">{query.data?.items.map((room) => <article className="panel room-card" key={room.room_id}><Users /><h2>{room.name}</h2><p>{room.description || "No description"}</p><small>{room.members.length} members</small><div className="room-modes">{(["mailbox", "notify", "digest"] as const).map((mode) => <span key={mode}>{mode}: {room.members.filter((member) => member.delivery_mode === mode).length}</span>)}</div></article>)}</div></>;
}

function MailboxPanel({
  snapshot,
  loading,
  stopping,
  requeueing,
  onStop,
  onRequeue,
}: {
  snapshot?: MailboxSnapshot;
  loading: boolean;
  stopping: boolean;
  requeueing: boolean;
  onStop: () => void;
  onRequeue: (messageId: string) => void;
}) {
  const listener = snapshot?.listener;
  const listenerState = listener
    ? listener.stop_requested ? "stopping" : listener.state ?? "waiting"
    : "offline";
  return <section className="mailbox-panel">
    <header><div><strong><Inbox size={16} /> Mailbox</strong><small>{snapshot?.total ?? 0} messages · processing is agent-controlled</small></div><div className={`listener-state ${listenerState}`}><span className={listener ? "online-dot" : "offline-dot"} /><span>Listener {listenerState}</span>{listener && <button disabled={stopping} onClick={onStop}><CircleStop size={14} /> {stopping ? "Stopping…" : "Stop"}</button>}</div></header>
    {loading ? <p className="muted">Loading mailbox…</p> : snapshot?.items.length ? <div className="mailbox-items">{snapshot.items.slice(0, 8).map((item) => <article key={item.message_id}><div><strong>{item.source_conversation_id || item.actor_kind}</strong><span className={`pill ${item.processing_state ?? "pending"}`}>{item.processing_state ?? "pending"}</span></div><p>{item.body}</p><small>{new Date(item.created_at).toLocaleString()} · {item.operation} · {item.correlation_id}</small>{(item.outcome_detail || item.processing_detail) && <em>{item.outcome_detail || item.processing_detail}</em>}{item.processing_state && item.processing_state !== "pending" && <button disabled={requeueing} onClick={() => onRequeue(item.message_id)}><RotateCcw size={13} /> Requeue</button>}</article>)}</div> : <p className="muted">No mailbox messages yet.</p>}
  </section>;
}

function NodesView() {
  const query = useQuery({ queryKey: ["nodes"], queryFn: listNodes, refetchInterval: 10_000 });
  return <><PageTitle title="Machines & environments (WIP)" subtitle="The execution locations that own your indexed conversations." /><div className="card-grid">{query.data?.map((node) => <article className="panel node-card" key={node.node_id}><div className="node-title"><Monitor /><div><h2>{node.display_name}</h2><p>{node.platform}</p></div><span className={node.reachable ? "online-dot" : "offline-dot"} /></div>{node.environments.map((environment) => <div className="environment" key={environment.environment_id}><strong>{environment.display_name || environment.environment_id}</strong><small>{environment.kind} · {environment.available ? "available" : "offline"}</small></div>)}</article>)}</div></>;
}

function NatsView() {
  const summary = useQuery({ queryKey: ["nats-summary"], queryFn: natsSummary, refetchInterval: 5_000 });
  const events = useQuery({ queryKey: ["nats-events"], queryFn: natsActivity, refetchInterval: 5_000 });
  const broker = summary.data?.broker as Record<string, unknown> | undefined;
  return <><PageTitle title="NATS server log" subtitle="Connections, publishes, deliveries, retries, dead letters, and server issues." /><div className="summary-strip"><span><small>Broker</small>{String(broker?.status ?? "unknown")}</span><span><small>Events</small>{String(summary.data?.events ?? 0)}</span><span><small>Issues</small>{String(summary.data?.issues ?? 0)}</span><span><small>Connected</small>{String(broker?.connected ?? false)}</span></div><section className="panel table-panel"><table><thead><tr><th>Time</th><th>Severity</th><th>Direction</th><th>Subject</th><th>Event</th></tr></thead><tbody>{events.data?.items.map((event) => <tr key={event.event_id}><td>{new Date(event.occurred_at).toLocaleString()}</td><td><span className={`pill ${event.severity}`}>{event.severity}</span></td><td>{event.direction}</td><td className="mono">{event.subject}</td><td>{String(event.detail.kind ?? event.category)}</td></tr>)}</tbody></table></section></>;
}

function PageTitle({ title, subtitle }: { title: string; subtitle: string }) { return <header className="page-head"><div><h1>{title}</h1><p>{subtitle}</p></div></header>; }

function providerBadge(provider: string): string {
  if (provider === "codex") return "CX";
  if (provider === "claude") return "CL";
  return provider.slice(0, 2).toUpperCase();
}
