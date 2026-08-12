import { Archive, Bot, FolderGit2, MapPin, Pin, Search, WifiOff } from 'lucide-react'
import type { Conversation } from '../types'

interface Props {
  conversations: Conversation[]
  selectedId?: string
  loading: boolean
  onSelect: (id: string) => void
}

function relativeDate(value?: string) {
  if (!value) return 'Unknown'
  const elapsed = Date.now() - new Date(value).getTime()
  if (elapsed < 60_000) return 'Now'
  if (elapsed < 3_600_000) return `${Math.floor(elapsed / 60_000)}m`
  if (elapsed < 86_400_000) return `${Math.floor(elapsed / 3_600_000)}h`
  if (elapsed < 604_800_000) return `${Math.floor(elapsed / 86_400_000)}d`
  return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' }).format(new Date(value))
}

export function ConversationList({ conversations, selectedId, loading, onSelect }: Props) {
  if (loading) {
    return <div className="list-state"><span className="spinner" />Scanning your conversations…</div>
  }
  if (!conversations.length) {
    return (
      <div className="empty-state">
        <span className="empty-icon"><Search size={22} /></span>
        <strong>No conversations found</strong>
        <span>Try another phrase or clear a filter.</span>
      </div>
    )
  }
  return (
    <div className="conversation-list" role="list">
      {conversations.map((conversation) => {
        const title = conversation.catalog_title || conversation.title || 'Untitled conversation'
        const cwd = conversation.location?.cwd || conversation.cwd
        return (
          <button
            type="button"
            role="listitem"
            key={conversation.id}
            className={`conversation-row ${selectedId === conversation.id ? 'selected' : ''}`}
            onClick={() => onSelect(conversation.id)}
          >
            <span className={`provider-mark ${conversation.provider.toLowerCase()}`}><Bot size={16} /></span>
            <span className="row-content">
              <span className="row-title-line">
                <strong>{title}</strong>
                <span className="row-icons">
                  {conversation.pinned && <Pin size={13} aria-label="Pinned" />}
                  {conversation.archived && <Archive size={13} aria-label="Archived" />}
                </span>
              </span>
              <span className="row-preview">{conversation.preview || 'No searchable preview'}</span>
              <span className="row-meta">
                <span className={`status-dot ${conversation.status ?? 'unknown'}`} />
                <span>{conversation.status ?? 'unknown'}</span>
                <span className="meta-divider" />
                {conversation.location?.available === false ? <WifiOff size={12} /> : <MapPin size={12} />}
                <span className="truncate">{conversation.location?.node_name || conversation.location?.environment || 'Local'}</span>
                {conversation.git?.branch && <><span className="meta-divider" /><FolderGit2 size={12} /><span className="truncate">{conversation.git.branch}</span></>}
              </span>
            </span>
            <time>{relativeDate(conversation.last_active_at || conversation.updated_at)}</time>
          </button>
        )
      })}
    </div>
  )
}
