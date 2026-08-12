import { BriefcaseBusiness, Plus } from 'lucide-react'
import { useState } from 'react'
import type { WorkItem, WorkItemInput } from '../types'

interface Props {
  items: WorkItem[]
  selectedId?: string
  loading: boolean
  creating: boolean
  onSelect: (id: string) => void
  onCreate: (input: WorkItemInput) => void
}

export function WorkPortfolio({ items, selectedId, loading, creating, onSelect, onCreate }: Props) {
  const [showForm, setShowForm] = useState(false)
  const [title, setTitle] = useState('')
  const [objective, setObjective] = useState('')
  const submit = () => {
    if (!title.trim()) return
    onCreate({ title: title.trim(), objective: objective.trim(), status: 'planned' })
    setTitle(''); setObjective(''); setShowForm(false)
  }
  return (
    <section className="work-portfolio list-panel">
      <div className="list-heading"><div><h1>Work portfolio</h1><span>{items.length} focused work items</span></div><button className="icon-button" title="Create work item" onClick={() => setShowForm(!showForm)}><Plus size={17} /></button></div>
      {showForm && <div className="inline-create-form"><input aria-label="Work item title" value={title} onChange={(event) => setTitle(event.target.value)} placeholder="PR 17 reconnect validation" /><textarea aria-label="Work item objective" value={objective} onChange={(event) => setObjective(event.target.value)} placeholder="What does done look like?" rows={3} /><button className="primary-button compact" disabled={!title.trim() || creating} onClick={submit}>{creating ? 'Creating…' : 'Create work item'}</button></div>}
      <div className="work-list">
        {loading ? <div className="list-state"><span className="spinner" />Loading work…</div> : items.length ? items.map((item) => (
          <button key={item.id} className={`work-row ${selectedId === item.id ? 'selected' : ''}`} onClick={() => onSelect(item.id)}>
            <span className="work-icon"><BriefcaseBusiness size={15} /></span><span className="work-row-copy"><strong>{item.title}</strong><span>{item.objective || 'No objective recorded'}</span><small><i className={`status-dot ${item.status}`} />{item.status}{item.conversation_ids ? ` · ${item.conversation_ids.length} conversations` : ''}</small></span>
          </button>
        )) : <div className="empty-state"><span className="empty-icon"><BriefcaseBusiness size={20} /></span><strong>No work items yet</strong><span>Create one to group conversations and coordinator roles.</span></div>}
      </div>
    </section>
  )
}
