import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bell,
  Bot,
  Check,
  CircleAlert,
  Inbox,
  MessageSquare,
  Monitor,
  Plus,
  RadioTower,
  RefreshCw,
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
  natsActivity,
  natsSummary,
  openCoreConversation,
  reconcileCore,
  rooms,
  sendCoreMessage,
  updateCatalogSettings,
} from "./api";

type Section = "conversations" | "attention" | "messages" | "rooms" | "nodes" | "nats";

export function App() {
  const cache = useQueryClient();
  const [section, setSection] = useState<Section>("conversations");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<string>();
  const [composer, setComposer] = useState("");
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
          <section className="detail-pane">{detail.data ? <><div className="detail-title"><div><span className="eyebrow">{detail.data.provider} · {detail.data.conversation_kind.replace("_", " ")}</span><h2>{detail.data.display_name}</h2><p>{detail.data.provider_title && detail.data.provider_title !== detail.data.alias ? `Provider title: ${detail.data.provider_title}` : detail.data.cwd}</p></div><div className="open-actions"><button className="primary" disabled={!detail.data.native_url || openNative.isPending} onClick={() => openNative.mutate({ id: detail.data.conversation_id, target: "desktop" })}>Open in {detail.data.provider === "claude" ? "Claude (in dev)" : "Codex"}</button><button disabled={!detail.data.capabilities.can_open || openNative.isPending} onClick={() => openNative.mutate({ id: detail.data.conversation_id, target: "terminal" })}><SquareTerminal size={15} /> Open in Terminal{detail.data.provider === "codex" ? " (in dev)" : ""}</button></div></div>
            {openFeedback && <p className="action-feedback" role="status">{openFeedback}</p>}
            <div className="facts"><span><small>Machine</small>{detail.data.node_id}</span><span><small>Environment</small>{detail.data.environment_id}</span><span><small>Delivery</small>{detail.data.delivery_mode}</span><span><small>Status</small>{detail.data.status}</span></div>
            <pre className="transcript">{detail.data.transcript_text || detail.data.preview || "Transcript will appear after the next reconciliation."}</pre>
            <form className="composer" onSubmit={(event) => { event.preventDefault(); if (composer.trim()) send.mutate(); }}><textarea value={composer} onChange={(event) => setComposer(event.target.value)} placeholder="Send an authenticated Bridge message as a normal user turn…" /><button className="primary" disabled={!composer.trim() || !detail.data.capabilities.can_message}><Send size={16} /> Send</button></form>
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
  return <><PageTitle title="Message history" subtitle="Incoming and outbound Bridge traffic with correlation and delivery state." /><section className="panel table-panel"><table><thead><tr><th>Time</th><th>Route</th><th>Message</th><th>Correlation</th><th>State</th></tr></thead><tbody>{query.data?.items.map((item) => <tr key={item.message_id}><td>{new Date(item.created_at).toLocaleString()}</td><td>{item.target_conversation_id || item.room_id}</td><td>{item.body}</td><td className="mono">{item.correlation_id}</td><td><span className={`pill ${item.state}`}>{item.state}</span></td></tr>)}</tbody></table></section></>;
}

function RoomsView() {
  const cache = useQueryClient(); const [name, setName] = useState("");
  const query = useQuery({ queryKey: ["rooms"], queryFn: rooms });
  const create = useMutation({ mutationFn: () => createRoom(name), onSuccess: async () => { setName(""); await cache.invalidateQueries({ queryKey: ["rooms"] }); } });
  return <><PageTitle title="Rooms (in dev)" subtitle="Lightweight broadcast channels with wake, notify, or digest delivery." /><form className="inline-create" onSubmit={(event) => { event.preventDefault(); if (name.trim()) create.mutate(); }}><input value={name} onChange={(event) => setName(event.target.value)} placeholder="New room name" /><button className="primary"><Plus size={16} /> Create</button></form><div className="card-grid">{query.data?.items.map((room) => <article className="panel room-card" key={room.room_id}><Users /><h2>{room.name}</h2><p>{room.description || "No description"}</p><small>{room.members.length} members</small></article>)}</div></>;
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
