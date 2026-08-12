import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Background, Controls, MarkerType, ReactFlow, type Edge, type Node } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import {
  AlertTriangle, Archive, Boxes, CheckCircle2, CircleDot, Clock3, FileOutput,
  GitBranch, Network, RadioTower, RefreshCw, RotateCcw, Server, ShieldAlert,
} from 'lucide-react'
import { getOperationsSnapshot } from '../api'
import type { OperationsSnapshot } from '../types'

type View = 'overview' | 'topology' | 'diagnostics'

export function OperationsCenter() {
  const [view, setView] = useState<View>('overview')
  const [topologyMode, setTopologyMode] = useState<'graph' | 'list'>('graph')
  const query = useQuery({
    queryKey: ['operations'],
    queryFn: getOperationsSnapshot,
    refetchInterval: 15_000,
  })

  return <section className="operations-page">
    <header className="operations-header">
      <div>
        <span className="eyebrow">One level above execution</span>
        <h1>Operations overview</h1>
        <p>Follow durable work, coordination, and delivery health without supervising terminal panes.</p>
      </div>
      <button className="secondary-button" onClick={() => void query.refetch()} disabled={query.isFetching}>
        <RefreshCw size={14} className={query.isFetching ? 'spinning' : ''} /> Refresh state
      </button>
    </header>
    <nav className="operations-tabs" aria-label="Operations views">
      {(['overview', 'topology', 'diagnostics'] as const).map((item) =>
        <button key={item} className={view === item ? 'active' : ''} onClick={() => setView(item)}>{item}</button>)}
    </nav>
    {query.isLoading && <div className="operations-state"><span className="spinner" />Assembling durable state…</div>}
    {query.isError && <div className="error-banner"><CircleDot size={16} />{query.error.message}<button onClick={() => void query.refetch()}>Retry</button></div>}
    {query.data && view === 'overview' && <Overview data={query.data} />}
    {query.data && view === 'topology' && <Topology data={query.data} mode={topologyMode} onMode={setTopologyMode} />}
    {query.data && view === 'diagnostics' && <Diagnostics data={query.data} />}
  </section>
}

function Overview({ data }: { data: OperationsSnapshot }) {
  const { summary } = data
  const activeLeases = data.leases.filter((lease) => lease.active)
  const expiredLeases = data.leases.filter((lease) => !lease.active)
  return <div className="operations-content">
    <div className={`system-state ${summary.status}`}>
      {summary.status === 'healthy' ? <CheckCircle2 size={16} /> : <AlertTriangle size={16} />}
      <div><strong>{summary.status === 'healthy' ? 'System is ready' : 'Attention is required'}</strong><span>Broker {summary.broker_status.replaceAll('_', ' ')} · background services {summary.background_status}</span></div>
      <time>{relative(summary.observed_at)}</time>
    </div>
    <div className="operations-metrics">
      <Metric icon={<Clock3 />} label="Pending requests" value={summary.counts.pending_requests} tone={summary.counts.pending_requests ? 'working' : ''} />
      <Metric icon={<ShieldAlert />} label="Needs attention" value={summary.counts.attention_required + summary.counts.unresolved_dead_letters} tone={summary.counts.unresolved_dead_letters ? 'danger' : ''} />
      <Metric icon={<Server />} label="Reachable nodes" value={`${summary.counts.nodes - summary.counts.unreachable_nodes}/${summary.counts.nodes}`} tone={summary.counts.unreachable_nodes ? 'warning' : ''} />
      <Metric icon={<RadioTower />} label="Consumer backlog" value={summary.counts.consumer_pending} tone={summary.counts.consumer_pending ? 'warning' : ''} />
      <Metric icon={<Boxes />} label="Durable roles" value={summary.counts.roles} />
      <Metric icon={<GitBranch />} label="Executions" value={summary.counts.executions} />
    </div>

    {summary.advisories.length > 0 && <section className="operations-panel attention-list">
      <PanelTitle icon={<AlertTriangle />} title="Attention queue" subtitle="Only durable conditions that may need a decision" count={summary.advisories.length} />
      {summary.advisories.map((item, index) => <article key={`${item.code}:${index}`} className={item.severity}>
        <span>{item.severity}</span><strong>{item.message}</strong><code>{item.code}</code>
      </article>)}
    </section>}

    <div className="operations-grid">
      <section className="operations-panel">
        <PanelTitle icon={<Clock3 />} title="Current procedure" subtitle="Queued and running requests, in order" count={data.pending.length} />
        <CompactList empty="No requests are waiting." items={data.pending.map((item) => ({
          id: item.execution_id, title: item.instruction || item.operation || 'Execution request', meta: `${item.status} · ${item.target?.kind || 'target'}:${item.target?.id || 'unknown'}`, state: item.status,
        }))} />
      </section>
      <section className="operations-panel">
        <PanelTitle icon={<Boxes />} title="Durable coordination" subtitle="Logical roles and their latest checkpoint" count={data.roles.length} />
        <CompactList empty="No durable roles." items={data.roles.map((role) => ({
          id: role.id,
          title: role.latest_checkpoint?.summary || role.charter,
          meta: `${role.role_type.replaceAll('_', ' ')} · checkpoint ${role.checkpoint_version}${role.latest_checkpoint?.recommended_next_action ? ` · next: ${role.latest_checkpoint.recommended_next_action}` : ''}${role.latest_checkpoint?.blockers.length ? ` · ${role.latest_checkpoint.blockers.length} blockers` : ''}${role.rollup?.stale ? ' · stale rollup' : ''}`,
          state: role.rollup?.stale ? 'warning' : role.status,
        }))} />
      </section>
      <section className="operations-panel">
        <PanelTitle icon={<RadioTower />} title="Leases" subtitle="Ownership that prevents duplicate coordination" count={data.leases.length} />
        <CompactList empty="No active leases." items={[...activeLeases, ...expiredLeases].map((lease) => ({
          id: lease.resource_id, title: lease.resource_id, meta: `${lease.holder_id} · expires ${relative(lease.expires_at)}`, state: lease.active ? 'active' : 'expired',
        }))} />
      </section>
      <section className="operations-panel">
        <PanelTitle icon={<RotateCcw />} title="Retries" subtitle="Attempts that did not follow the happy path" count={data.retries.length} />
        <CompactList empty="No retries recorded." items={data.retries.map((retry) => ({
          id: retry.attempt_id, title: retry.execution_id, meta: `attempt ${retry.attempt_number}${retry.error ? ` · ${retry.error}` : ''}`, state: retry.status,
        }))} />
      </section>
      <section className="operations-panel">
        <PanelTitle icon={<Archive />} title="Dead letters" subtitle="Terminal deliveries retained for diagnosis" count={data.deadLetters.length} />
        <CompactList empty="No unresolved dead letters." items={data.deadLetters.map((item) => ({
          id: item.dead_letter_id, title: item.reason, meta: `${item.consumer} · ${item.attempts} attempts`, state: 'failed',
        }))} />
      </section>
      <section className="operations-panel">
        <PanelTitle icon={<Server />} title="Nodes" subtitle="Execution locations, not terminal sessions" count={data.nodes.length} />
        <CompactList empty="No nodes registered." items={data.nodes.map((node) => ({
          id: node.id, title: node.name, meta: `${node.platform || 'platform unknown'} · ${node.capabilities?.length || 0} capabilities`, state: node.reachable ? 'online' : 'offline',
        }))} />
      </section>
      <section className="operations-panel">
        <PanelTitle icon={<FileOutput />} title="Artifacts" subtitle="Durable evidence referenced by executions" count={data.artifacts.length} />
        <CompactList empty="No artifacts referenced." items={data.artifacts.map((artifact) => ({
          id: artifact.artifact_id || artifact.uri, title: artifact.name, meta: `${artifact.uri}${artifact.sources?.length ? ` · ${artifact.sources.length} references` : ''}`, state: artifact.media_type || 'artifact',
        }))} />
      </section>
      <section className="operations-panel">
        <PanelTitle icon={<GitBranch />} title="Recent executions" subtitle="Outcomes and durable execution state" count={data.executions.length} />
        <CompactList empty="No executions recorded." items={data.executions.map((item) => ({
          id: item.execution_id, title: item.instruction || item.operation || item.execution_id, meta: `${item.attempt_count ?? 0} attempts · ${relative(item.updated_at)}`, state: item.status,
        }))} />
      </section>
    </div>
  </div>
}

function Topology({ data, mode, onMode }: { data: OperationsSnapshot, mode: 'graph' | 'list', onMode: (mode: 'graph' | 'list') => void }) {
  const topology = useMemo(() => buildTopology(data), [data])
  return <div className="operations-content">
    <div className="topology-heading"><div><h2>Observed topology</h2><p>Declared relationships and recent message routes share one model. A route is evidence, not a management hierarchy.</p></div><div className="segmented"><button className={mode === 'graph' ? 'active' : ''} onClick={() => onMode('graph')}>Graph</button><button className={mode === 'list' ? 'active' : ''} onClick={() => onMode('list')}>List</button></div></div>
    {mode === 'graph' ? <div className="operations-graph"><ReactFlow nodes={topology.nodes} edges={topology.edges} fitView nodesDraggable={false} nodesConnectable={false} minZoom={.25}><Background color="#243239" gap={18} size={1} /><Controls showInteractive={false} /></ReactFlow></div> : <div className="route-list">
      {topology.rows.map((row) => <article key={row.id}><span>{row.source}</span><em>{row.kind}</em><span>{row.target}</span><small>{row.observed ? 'observed route' : 'declared relationship'}</small></article>)}
      {!topology.rows.length && <div className="operations-empty">No relationships or routes observed yet.</div>}
    </div>}
  </div>
}

function Diagnostics({ data }: { data: OperationsSnapshot }) {
  return <div className="operations-content diagnostics-content">
    <div className="diagnostic-warning"><ShieldAlert size={15} /><div><strong>Raw diagnostics</strong><span>This view is for fault isolation. Normal work should be managed from the overview or focused work page.</span></div></div>
    <div className="operations-grid">
      <section className="operations-panel"><PanelTitle icon={<RadioTower />} title="Broker and streams" subtitle={`Connection: ${data.broker.status}`} count={data.broker.streams.length} />
        <CompactList empty="No streams reported." items={data.broker.streams.map((stream) => ({ id: stream.name, title: stream.name, meta: `${stream.messages ?? 0} messages · ${stream.consumer_count ?? 0} consumers`, state: 'stream' }))} />
      </section>
      <section className="operations-panel"><PanelTitle icon={<Network />} title="Consumers" subtitle="Lag, pending acknowledgements, and redelivery" count={data.broker.consumers.length} />
        <CompactList empty="No consumer diagnostics reported." items={data.broker.consumers.map((consumer) => ({ id: `${consumer.stream}:${consumer.consumer}`, title: consumer.consumer, meta: `${consumer.pending_count ?? consumer.pending ?? 0} pending · ${consumer.ack_pending_count ?? consumer.ack_pending ?? 0} ack pending · ${consumer.redelivered_count ?? consumer.redelivered ?? 0} redelivered`, state: consumer.state || 'unknown' }))} />
      </section>
      <section className="operations-panel"><PanelTitle icon={<Boxes />} title="Background services" subtitle="Supervised durable workers" count={data.background.tasks.length} />
        <CompactList empty="No background services registered." items={data.background.tasks.map((task) => ({ id: task.name, title: task.name, meta: task.error || `${task.critical ? 'critical' : 'optional'} · started ${relative(task.started_at)}`, state: task.state }))} />
      </section>
      <section className="operations-panel"><PanelTitle icon={<GitBranch />} title="Broker messages" subtitle="Metadata only; message bodies remain excluded" count={data.messages.length} />
        <CompactList empty="No broker messages projected." items={data.messages.map((message) => ({ id: message.message_id, title: message.subject, meta: `${message.message_type} · ${relative(message.last_observed_at)}`, state: message.state }))} />
      </section>
      <section className="operations-panel"><PanelTitle icon={<RotateCcw />} title="Deliveries" subtitle="Acknowledgement and redelivery records" count={data.deliveries.length} />
        <CompactList empty="No delivery records projected." items={data.deliveries.map((delivery) => ({ id: delivery.delivery_id, title: delivery.consumer, meta: `${delivery.message_id} · ${delivery.redelivery_count} redeliveries`, state: delivery.state }))} />
      </section>
    </div>
  </div>
}

function Metric({ icon, label, value, tone = '' }: { icon: React.ReactNode, label: string, value: string | number, tone?: string }) {
  return <article className={`operations-metric ${tone}`}><span>{icon}</span><div><strong>{value}</strong><small>{label}</small></div></article>
}

function PanelTitle({ icon, title, subtitle, count }: { icon: React.ReactNode, title: string, subtitle: string, count: number }) {
  return <header className="operations-panel-title"><span>{icon}</span><div><h2>{title}</h2><p>{subtitle}</p></div><em>{count}</em></header>
}

function CompactList({ items, empty }: { items: Array<{ id: string, title: string, meta: string, state: string }>, empty: string }) {
  if (!items.length) return <div className="operations-empty">{empty}</div>
  return <div className="compact-operations-list">{items.slice(0, 12).map((item) => <article key={item.id}><i className={item.state} /><div><strong title={item.title}>{item.title}</strong><span title={item.meta}>{item.meta}</span></div><em>{item.state.replaceAll('_', ' ')}</em></article>)}</div>
}

function buildTopology(data: OperationsSnapshot) {
  const entities = new Map<string, { kind: string, label: string, status: string }>()
  data.nodes.forEach((node) => entities.set(`node:${node.id}`, { kind: 'node', label: node.name, status: node.reachability }))
  data.roles.forEach((role) => entities.set(`role:${role.id}`, { kind: 'role', label: role.charter, status: role.status }))
  const rows = data.relationships.map((item) => ({ id: item.id, source: `${item.source_type}:${item.source_id}`, target: `${item.target_type}:${item.target_id}`, kind: item.kind, observed: false }))
  data.messages.filter((item) => item.source_kind && item.source_id && item.destination_kind && item.destination_id).forEach((item) => rows.push({ id: `route:${item.message_id}`, source: `${item.source_kind}:${item.source_id}`, target: `${item.destination_kind}:${item.destination_id}`, kind: item.message_type, observed: true }))
  rows.forEach((row) => { if (!entities.has(row.source)) entities.set(row.source, { kind: row.source.split(':')[0], label: row.source.split(':').slice(1).join(':'), status: 'observed' }); if (!entities.has(row.target)) entities.set(row.target, { kind: row.target.split(':')[0], label: row.target.split(':').slice(1).join(':'), status: 'observed' }) })
  const groups = new Map<string, number>()
  const nodes: Node[] = [...entities].map(([id, entity]) => { const index = groups.get(entity.kind) ?? 0; groups.set(entity.kind, index + 1); const column = ['work', 'role', 'conversation', 'node', 'capability', 'room'].indexOf(entity.kind); return { id, position: { x: Math.max(0, column) * 250, y: index * 92 }, className: `graph-node-shell ${entity.kind}`, data: { label: <div className="graph-node"><span>{entity.kind} · {entity.status}</span><strong title={entity.label}>{entity.label}</strong></div> } } })
  const edges: Edge[] = rows.map((row) => ({ id: row.id, source: row.source, target: row.target, label: row.kind.replaceAll('_', ' '), animated: row.observed, markerEnd: { type: MarkerType.ArrowClosed, color: row.observed ? '#d4a763' : '#76c7b7' }, style: { stroke: row.observed ? '#8b7049' : '#527b76', strokeDasharray: row.observed ? '5 4' : undefined }, labelStyle: { fill: '#91aaa6', fontSize: 9 } }))
  return { nodes, edges, rows }
}

function relative(value?: string) {
  if (!value) return 'time unknown'
  const seconds = Math.round((new Date(value).getTime() - Date.now()) / 1000)
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })
  if (Math.abs(seconds) < 60) return formatter.format(seconds, 'second')
  const minutes = Math.round(seconds / 60)
  if (Math.abs(minutes) < 60) return formatter.format(minutes, 'minute')
  const hours = Math.round(minutes / 60)
  if (Math.abs(hours) < 48) return formatter.format(hours, 'hour')
  return formatter.format(Math.round(hours / 24), 'day')
}
