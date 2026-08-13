import { useEffect, useState } from 'react'
import {
  AlertTriangle, Archive, ArchiveRestore, Bot, Check, ChevronRight, Clipboard, Code2, EyeOff,
  FolderGit2, MapPin, MessageSquareText, Pencil, Pin, PinOff, Play, Save, Tag, X,
} from 'lucide-react'
import type { Conversation, ConversationPatch } from '../types'

interface Props {
  conversation?: Conversation
  loading: boolean
  saving: boolean
  resuming: boolean
  onUpdate: (patch: ConversationPatch) => void
  onResume: () => void
}

export function ConversationDetail({ conversation, loading, saving, resuming, onUpdate, onResume }: Props) {
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState('')
  const [notes, setNotes] = useState('')
  const [tagText, setTagText] = useState('')
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    setTitle(conversation?.catalog_title || conversation?.title || '')
    setNotes(conversation?.notes || '')
    setTagText(conversation?.tags?.join(', ') || '')
    setEditing(false)
  }, [conversation?.id])

  if (loading) return <section className="detail-panel"><div className="list-state"><span className="spinner" />Loading details…</div></section>
  if (!conversation) {
    return (
      <section className="detail-panel detail-welcome">
        <span className="welcome-orbit"><MessageSquareText size={28} /></span>
        <h2>Your work, in context.</h2>
        <p>Select a conversation to see where it lives, what changed, and how to continue.</p>
      </section>
    )
  }

  const displayTitle = conversation.catalog_title || conversation.title || 'Untitled conversation'
  const cwd = conversation.location?.cwd || conversation.cwd
  const providerThread = conversation.provider_thread_id || conversation.id
  const fallbackResume = conversation.provider.toLowerCase() === 'claude'
    ? `${cwd ? `cd ${shellQuote(cwd)} && ` : ''}claude --resume ${shellQuote(providerThread.split(':agent:')[0])}`
    : `codex resume ${providerThread}${cwd ? ` -C ${shellQuote(cwd)}` : ''}`
  const command = conversation.resume_command || fallbackResume
  const desktopOpen = conversation.interactive_open?.desktop
  const canOpenDesktop = conversation.provider.toLowerCase() === 'codex'
    && desktopOpen?.available === true
    && Boolean(desktopOpen.url)
  const save = () => {
    onUpdate({
      title: title.trim() || displayTitle,
      notes: notes.trim(),
      tags: tagText.split(',').map((tag) => tag.trim()).filter(Boolean),
    })
    setEditing(false)
  }
  const copyCommand = async () => {
    await navigator.clipboard.writeText(command)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1600)
  }

  return (
    <section className="detail-panel">
      <header className="detail-header">
        <div className="breadcrumb"><span>Conversations</span><ChevronRight size={13} /><span>{conversation.provider}</span></div>
        <div className="detail-heading">
          <span className={`provider-mark large ${conversation.provider.toLowerCase()}`}><Bot size={20} /></span>
          <div>
            {editing ? <input className="title-input" value={title} onChange={(event) => setTitle(event.target.value)} autoFocus /> : <h2>{displayTitle}</h2>}
            <div className="detail-subtitle">
              <span className={`status-pill ${conversation.status ?? 'unknown'}`}><span className="status-dot" />{conversation.status ?? 'unknown'}</span>
              <span>{conversation.provider}</span><span>·</span><span>{conversation.source_kind || 'native'}</span>
            </div>
          </div>
        </div>
        <div className="header-actions">
          {canOpenDesktop ? (
            <a className="primary-button" href={desktopOpen?.url} aria-label="Open in Codex desktop">
              <Play size={15} fill="currentColor" />Open in Codex
            </a>
          ) : (
            <button className="primary-button" onClick={onResume} disabled={resuming || conversation.location?.available === false}>
              <Play size={15} fill="currentColor" />{resuming ? 'Opening…' : `Open in ${conversation.provider === 'claude' ? 'Claude' : 'Codex'}`}
            </button>
          )}
          <button className="icon-button" title="Edit metadata" onClick={() => setEditing(!editing)}>{editing ? <X size={17} /> : <Pencil size={17} />}</button>
          <button className={`icon-button ${conversation.pinned ? 'active' : ''}`} title={conversation.pinned ? 'Unpin' : 'Pin'} onClick={() => onUpdate({ pinned: !conversation.pinned })}>{conversation.pinned ? <PinOff size={17} /> : <Pin size={17} />}</button>
          <button className="icon-button" title={conversation.archived ? 'Restore' : 'Archive'} onClick={() => onUpdate({ archived: !conversation.archived })}>{conversation.archived ? <ArchiveRestore size={17} /> : <Archive size={17} />}</button>
          <button className="icon-button danger-hover" title="Hide" onClick={() => onUpdate({ hidden: !conversation.hidden })}><EyeOff size={17} /></button>
        </div>
      </header>

      <div className="detail-scroll">
        {conversation.location?.available === false && <div className="location-unavailable" role="alert">
          <AlertTriangle size={17} />
          <div><strong>Original environment unavailable</strong><span>This conversation stays searchable, but Agent Bridge will not open it somewhere else. Bring {conversation.location.node_name || 'its owning node'} online or copy the native resume command for that environment.</span></div>
        </div>}
        {conversation.provider.toLowerCase() === 'codex' && desktopOpen?.available === false && <div className="location-unavailable" role="status">
          <AlertTriangle size={17} />
          <div><strong>Desktop chat unavailable on this machine</strong><span>{desktopOpen.reason || 'The local Codex thread could not be found. Use the terminal fallback on its owning host.'}</span></div>
        </div>}
        <div className="context-grid">
          <ContextCard icon={<MapPin size={15} />} label="Location" value={conversation.location?.node_name || conversation.location?.environment || 'This machine'} secondary={cwd} />
          <ContextCard icon={<FolderGit2 size={15} />} label="Git context" value={conversation.git?.branch || 'No branch recorded'} secondary={conversation.git?.repository_url} />
          <ContextCard icon={<Code2 size={15} />} label="Native thread" value={conversation.provider_thread_id || conversation.id} secondary={conversation.location?.available === false ? 'Node offline' : 'Ready to resume'} />
        </div>

        <div className="section-block">
          <div className="section-title"><Tag size={15} /><h3>Organization</h3></div>
          {editing ? (
            <>
              <label className="field-label">Tags<input value={tagText} onChange={(event) => setTagText(event.target.value)} placeholder="arci-v2, pr-17" /></label>
              <label className="field-label">Notes<textarea value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="Add context that should travel with this conversation…" rows={4} /></label>
              <button className="primary-button compact" onClick={save} disabled={saving}><Save size={14} />{saving ? 'Saving…' : 'Save metadata'}</button>
            </>
          ) : (
            <>
              <div className="tag-list">{conversation.tags?.length ? conversation.tags.map((tag) => <span className="tag" key={tag}>{tag}</span>) : <span className="muted">No tags yet</span>}</div>
              <p className={`notes ${conversation.notes ? '' : 'muted'}`}>{conversation.notes || 'No catalog notes. Add a durable note to remember why this thread matters.'}</p>
            </>
          )}
        </div>

        <div className="section-block">
          <div className="section-title"><MessageSquareText size={15} /><h3>Recent context</h3></div>
          {conversation.messages?.length ? (
            <div className="transcript">
              {conversation.messages.slice(-6).map((message, index) => <div className="message" key={message.id || index}><span>{message.role === 'assistant' ? (conversation.provider === 'claude' ? 'Claude' : 'Codex') : 'You'}</span><p>{message.text}</p></div>)}
            </div>
          ) : <p className="notes transcript-text">{conversation.transcript_text || conversation.preview || 'Transcript content has not been synchronized for this conversation.'}</p>}
        </div>

        <div className="resume-card">
          <div><strong>Resume from a terminal</strong><span>Use this exact command if native launch is unavailable.</span></div>
          <code>{command}</code>
          <button className="secondary-button compact" onClick={copyCommand}>{copied ? <Check size={14} /> : <Clipboard size={14} />}{copied ? 'Copied' : 'Copy command'}</button>
        </div>
      </div>
    </section>
  )
}

function ContextCard({ icon, label, value, secondary }: { icon: React.ReactNode, label: string, value: string, secondary?: string }) {
  return <div className="context-card"><span className="context-icon">{icon}</span><div><span>{label}</span><strong title={value}>{value}</strong>{secondary && <small title={secondary}>{secondary}</small>}</div></div>
}

function shellQuote(value: string) {
  return `'${value.replaceAll("'", "'\\''")}'`
}
