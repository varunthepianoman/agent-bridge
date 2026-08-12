import { Laptop, Radio, Server, Wifi, WifiOff } from 'lucide-react'
import type { CatalogNode } from '../types'

interface Props {
  nodes: CatalogNode[]
  loading: boolean
  error?: string
  onRetry: () => void
}

export function NodeOverview({ nodes, loading, error, onRetry }: Props) {
  if (loading) return <section className="nodes-page"><div className="list-state"><span className="spinner" />Checking node reachability…</div></section>
  return <section className="nodes-page">
    <header className="nodes-header">
      <div><span className="eyebrow">Execution locations</span><h1>Nodes and environments</h1><p>Reachability is operational state. Conversation history remains available when its original node is offline.</p></div>
      <span className="node-summary"><Radio size={15} />{nodes.filter((node) => node.reachable).length} of {nodes.length} online</span>
    </header>
    {error && <div className="error-banner"><WifiOff size={16} />{error}<button onClick={onRetry}>Retry</button></div>}
    {!error && !nodes.length ? <div className="empty-state"><span className="empty-icon"><Server size={22} /></span><strong>No nodes registered</strong><span>Start a node agent with a hub-issued credential to register its environments.</span></div> : null}
    <div className="node-grid">
      {nodes.map((node) => <article className="node-card" key={node.id}>
        <div className="node-card-heading">
          <span className={`node-icon ${node.reachable ? 'online' : 'offline'}`}>{node.reachable ? <Wifi size={18} /> : <WifiOff size={18} />}</span>
          <div><h2>{node.name}</h2><code>{node.id}</code></div>
          <span className={`reachability ${node.reachability}`}>{node.reachability}</span>
        </div>
        <div className="node-facts"><span>{node.platform || 'Platform not reported'}</span><span>{lastSeen(node.last_seen_at)}</span></div>
        <div className="environment-list">
          {node.environments.length ? node.environments.map((environment) => <div key={environment.id}>
            <Laptop size={14} /><span><strong>{environment.name || environment.id}</strong><small>{environment.kind || 'environment'} · {environment.available ? 'available' : 'unavailable'}</small></span><i className={environment.available ? 'available' : 'unavailable'} />
          </div>) : <p>No environments reported.</p>}
        </div>
      </article>)}
    </div>
  </section>
}

function lastSeen(value?: string) {
  if (!value) return 'Never seen'
  return `Last seen ${new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))}`
}
