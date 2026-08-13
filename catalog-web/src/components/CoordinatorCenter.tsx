import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Bot, Check, ChevronRight, CircleGauge, Clock3, Network, Play, Send, ShieldCheck, Sparkles, X } from 'lucide-react'
import { useMemo, useState } from 'react'
import { activateCoordinatorRole, approveConvergenceImplementation, decideCoordinatorIntake, listCoordinatorActivations, listCoordinatorIntakes, listCoordinatorRollups, listRoleReports, listRoles, submitCoordinatorIntake } from '../api'
import type { AuthorityLimits, AutonomyMode, CoordinatorIntakeInput, CoordinatorRole, WorkItem } from '../types'

interface Props { workItems: WorkItem[], onOpenManual: () => void, initialWorkId?: string, initialRoleId?: string }

const defaultAuthority: AuthorityLimits = {
  max_parallel_executions: 1,
  max_attempts: 3,
  allowed_capabilities: [],
  may_expand_scope: false,
}

export function CoordinatorCenter({ workItems, onOpenManual, initialWorkId, initialRoleId }: Props) {
  const queryClient = useQueryClient()
  const [mode, setMode] = useState<AutonomyMode>('delegate')
  const [objective, setObjective] = useState('')
  const [workId, setWorkId] = useState(initialWorkId || '')
  const [targetRoleId, setTargetRoleId] = useState(initialRoleId || '')
  const [contextText, setContextText] = useState('{}')
  const [capabilities, setCapabilities] = useState('')
  const [authority, setAuthority] = useState(defaultAuthority)
  const [error, setError] = useState<string>()
  const [selectedRequestId, setSelectedRequestId] = useState<string>()

  const roles = useQuery({ queryKey: ['roles', 'coordinator-center'], queryFn: () => listRoles() })
  const intakes = useQuery({ queryKey: ['coordinator-intakes'], queryFn: listCoordinatorIntakes, refetchInterval: 5_000 })
  const selectedRole = roles.data?.find((role) => role.id === targetRoleId)
  const reports = useQuery({ queryKey: ['role-reports', targetRoleId], queryFn: () => listRoleReports(targetRoleId), enabled: Boolean(targetRoleId) })
  const rollups = useQuery({ queryKey: ['role-rollups', targetRoleId], queryFn: () => listCoordinatorRollups(targetRoleId), enabled: Boolean(targetRoleId) })
  const activations = useQuery({ queryKey: ['role-activations', targetRoleId], queryFn: () => listCoordinatorActivations(targetRoleId), enabled: Boolean(targetRoleId), refetchInterval: 5_000 })
  const selectedIntake = intakes.data?.find((item) => item.request_id === selectedRequestId) ?? intakes.data?.[0]
  const attention = intakes.data?.filter((item) => item.approval_required || item.status === 'awaiting_approval' || item.attention_required) ?? []
  const implementationGates = workItems.filter((work) => convergenceState(work) === 'awaiting_user_implementation_approval')

  const effectiveAuthority = useMemo(() => ({
    ...authority,
    allowed_capabilities: capabilities.split(',').map((value) => value.trim()).filter(Boolean),
  }), [authority, capabilities])

  const intakeMutation = useMutation({
    mutationFn: (input: CoordinatorIntakeInput) => submitCoordinatorIntake(input),
    onSuccess: async (result) => {
      setSelectedRequestId(result.request_id)
      setObjective('')
      await queryClient.invalidateQueries({ queryKey: ['coordinator-intakes'] })
    },
    onError: (value: Error) => setError(value.message),
  })
  const decisionMutation = useMutation({
    mutationFn: ({ requestId, decision }: { requestId: string, decision: 'approve' | 'reject' }) => decideCoordinatorIntake(requestId, decision, undefined, decision === 'approve' ? effectiveAuthority : undefined),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ['coordinator-intakes'] }),
    onError: (value: Error) => setError(value.message),
  })
  const implementationDecisionMutation = useMutation({
    mutationFn: (workItemId: string) => approveConvergenceImplementation(workItemId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['work-items'] })
      await queryClient.invalidateQueries({ queryKey: ['work-item'] })
    },
    onError: (value: Error) => setError(value.message),
  })
  const activationMutation = useMutation({
    mutationFn: () => {
      if (!selectedIntake) throw new Error('Select an intake before activating this role.')
      return activateCoordinatorRole(targetRoleId, selectedIntake.request_id)
    },
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ['role-activations', targetRoleId] }),
    onError: (value: Error) => setError(value.message),
  })

  const submit = () => {
    setError(undefined)
    if (mode === 'manual') return onOpenManual()
    if (!objective.trim()) return setError('Describe the objective before submitting it.')
    if (mode === 'autonomous' && (!effectiveAuthority.deadline || (!effectiveAuthority.token_budget && !effectiveAuthority.cost_budget_usd))) return setError('Autonomous mode requires an explicit deadline and token or cost budget.')
    let context: Record<string, unknown>
    try { const value: unknown = JSON.parse(contextText); if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(); context = value as Record<string, unknown> } catch { return setError('Context must be a JSON object.') }
    intakeMutation.mutate({
      objective: objective.trim(), mode, work_id: workId || undefined,
      target_role_id: targetRoleId || undefined, context, authority: effectiveAuthority,
      artifacts: [], extensions: {},
    })
  }

  return <section className="coordinator-page">
    <header className="coordinator-header">
      <div><span className="eyebrow">Optional intelligence layer</span><h1>Coordinator</h1><p>Route a goal through the portfolio coordinator or speak directly to a durable work role. The hierarchy can propose and execute within the authority you grant.</p></div>
      <button className="secondary-button" onClick={onOpenManual}><Send size={14} />Open Manual Bridge</button>
    </header>

    <div className="coordinator-layout">
      <div className="coordinator-main">
        <section className="coord-card intake-card">
          <div className="coord-title"><Sparkles size={16} /><div><h2>Submit an objective</h2><p>Portfolio intake is the default; selecting a role speaks to that work coordinator directly.</p></div></div>
          <div className="mode-selector" aria-label="Autonomy mode">{(['manual', 'advise', 'delegate', 'autonomous'] as AutonomyMode[]).map((value) => <button key={value} className={mode === value ? 'active' : ''} onClick={() => setMode(value)}><strong>{value}</strong><span>{modeDescription(value)}</span></button>)}</div>
          {mode === 'manual' ? <div className="manual-bypass-callout"><ShieldCheck size={18} /><div><strong>Manual bypass is always available</strong><span>No coordinator will infer, approve, retry, or reroute your request.</span></div><button className="primary-button" onClick={onOpenManual}>Continue manually</button></div> : <>
            <label className="coord-field">Objective<textarea aria-label="Coordinator objective" rows={4} value={objective} onChange={(event) => setObjective(event.target.value)} placeholder="Implement the reconnect validation and summarize evidence." /></label>
            <div className="coord-fields two"><label>Work scope<select aria-label="Coordinator work scope" value={workId} onChange={(event) => setWorkId(event.target.value)}><option value="">Portfolio decides</option>{workItems.map((work) => <option key={work.id} value={work.id}>{work.title}</option>)}</select></label><label>Target coordinator<select aria-label="Target coordinator" value={targetRoleId} onChange={(event) => setTargetRoleId(event.target.value)}><option value="">Portfolio coordinator</option>{roles.data?.filter((role) => role.role_type.includes('coordinator')).map((role) => <option key={role.id} value={role.id}>{role.charter}</option>)}</select></label></div>
            <label className="coord-field">Structured context (JSON)<textarea className="code-input" aria-label="Coordinator context" rows={3} value={contextText} onChange={(event) => setContextText(event.target.value)} /></label>
            <AuthorityEditor value={authority} capabilities={capabilities} onChange={setAuthority} onCapabilities={setCapabilities} />
            {error && <div className="manual-error" role="alert">{error}</div>}
            <div className="coord-submit"><span>{mode === 'advise' ? 'Propose actions and wait for approval' : mode === 'delegate' ? 'Execute bounded work and escalate expansion' : 'Adapt and retry within explicit budgets'}</span><button className="primary-button" disabled={intakeMutation.isPending} onClick={submit}><Send size={14} />{intakeMutation.isPending ? 'Submitting…' : targetRoleId ? 'Send to role' : 'Submit to portfolio'}</button></div>
          </>}
        </section>

        <section className="coord-card topology-card">
          <div className="coord-title"><Network size={16} /><div><h2>Proposed topology and durable roles</h2><p>State comes from role records and checkpoints, not transient model sessions.</p></div></div>
          {selectedIntake && <article className="topology-proposal"><div><span>Latest intake</span><strong>{selectedIntake.request?.objective || selectedIntake.objective || selectedIntake.request_id}</strong></div><em className={`coord-status ${selectedIntake.status}`}>{selectedIntake.status.replaceAll('_', ' ')}</em><TopologyValue value={selectedIntake.proposed_topology} actions={selectedIntake.proposed_actions} /></article>}
          <div className="role-topology">{roles.data?.length ? roles.data.map((role) => <button key={role.id} className={role.id === targetRoleId ? 'selected' : ''} style={{ marginLeft: role.parent_role_id ? 22 : 0 }} onClick={() => setTargetRoleId(role.id)}><i className={`role-state ${role.status}`} /><div><strong>{role.charter}</strong><span>{role.role_type.replaceAll('_', ' ')} · checkpoint {role.checkpoint_version}</span></div><em>{role.status}</em><ChevronRight size={13} /></button>) : <p className="muted">No durable coordinator roles are registered.</p>}</div>
        </section>

        {selectedRole && <section className="coord-card role-detail-card">
          <div className="coord-title"><Bot size={16} /><div><h2>{selectedRole.charter}</h2><p>{selectedRole.role_type.replaceAll('_', ' ')} · {selectedRole.authority_profile || 'authority unset'} · checkpoint {selectedRole.checkpoint_version}</p></div><button className="secondary-button compact" disabled={activationMutation.isPending || !selectedIntake} onClick={() => activationMutation.mutate()}><Play size={12} />Activate</button></div>
          <div className="role-metrics"><span><strong>{selectedRole.status}</strong>role status</span><span><strong>{selectedRole.autonomy_mode || 'delegate'}</strong>autonomy</span><span><strong>{activations.data?.[0]?.status || 'idle'}</strong>activation</span><span className={rollups.data?.some((item) => item.stale) ? 'warning' : ''}><strong>{rollups.data?.filter((item) => item.stale).length ?? 0}</strong>stale rollups</span></div>
          <div className="reports-heading"><h3>Structured child reports</h3><span>{reports.data?.length ?? 0}</span></div>
          <div className="report-list">{reports.data?.length ? reports.data.map((report) => { const rollup = rollups.data?.find((item) => item.child_role_id === report.reporting_role_id); return <article key={report.report_id}><div className="report-top"><strong>{report.reporting_role_id}</strong><span>checkpoint {report.checkpoint_version}</span>{rollup?.stale && <em><AlertTriangle size={11} />stale rollup</em>}</div><p>{report.summary}</p>{report.decisions.length > 0 && <small>{report.decisions.length} decisions · {report.recommended_action || 'no next action recorded'}</small>}{report.attention_required && <div className="report-attention">Attention: {report.attention_required}</div>}</article> }) : <p className="muted">No child reports have reached this role.</p>}</div>
        </section>}
      </div>

      <aside className="attention-panel">
        <div className="attention-title"><CircleGauge size={15} /><div><h2>Attention queue</h2><p>Only decisions that need you.</p></div><span>{attention.length + implementationGates.length}</span></div>
        {implementationGates.map((work) => <article key={`implementation-${work.id}`}><div><Clock3 size={12} /><span>awaiting implementation approval</span></div><strong>{work.title}</strong><p>Review intake and the implementation proposal are ready. Approval starts local development; remote writes remain gated.</p><div className="approval-actions"><button disabled={implementationDecisionMutation.isPending} onClick={() => implementationDecisionMutation.mutate(work.id)}><Check size={12} />Approve implementation</button></div></article>)}
        {attention.length ? attention.map((item) => <article key={item.request_id}><div><Clock3 size={12} /><span>{item.status.replaceAll('_', ' ')}</span></div><strong>{item.request?.objective || item.objective || item.request_id}</strong><p>{item.attention_required || 'Approval is required before bounded execution begins.'}</p><div className="approval-actions"><button disabled={decisionMutation.isPending} onClick={() => decisionMutation.mutate({ requestId: item.request_id, decision: 'reject' })}><X size={12} />Reject</button><button disabled={decisionMutation.isPending} onClick={() => decisionMutation.mutate({ requestId: item.request_id, decision: 'approve' })}><Check size={12} />Approve</button></div></article>) : implementationGates.length === 0 && <div className="attention-empty"><Check size={20} /><strong>Nothing needs approval</strong><span>Coordinators will surface meaningful scope, authority, and blocker decisions here.</span></div>}
      </aside>
    </div>
  </section>
}

function AuthorityEditor({ value, capabilities, onChange, onCapabilities }: { value: AuthorityLimits, capabilities: string, onChange: (value: AuthorityLimits) => void, onCapabilities: (value: string) => void }) {
  const numeric = (key: keyof AuthorityLimits, raw: string) => onChange({ ...value, [key]: raw ? Number(raw) : undefined })
  return <details className="authority-editor"><summary><ShieldCheck size={14} /><span><strong>Authority, budget, and scope</strong><small>Explicit limits travel with the request</small></span><ChevronRight size={13} /></summary><div className="authority-content"><div className="coord-fields four"><label>Parallel<input aria-label="Maximum parallel executions" type="number" min={1} value={value.max_parallel_executions} onChange={(event) => numeric('max_parallel_executions', event.target.value)} /></label><label>Attempts<input aria-label="Coordinator maximum attempts" type="number" min={1} value={value.max_attempts} onChange={(event) => numeric('max_attempts', event.target.value)} /></label><label>Token budget<input aria-label="Token budget" type="number" min={1} value={value.token_budget || ''} onChange={(event) => numeric('token_budget', event.target.value)} /></label><label>Cost budget<input aria-label="Cost budget" type="number" min="0.01" step="0.01" value={value.cost_budget_usd || ''} onChange={(event) => numeric('cost_budget_usd', event.target.value)} /></label></div><div className="coord-fields two"><label>Deadline<input aria-label="Coordinator deadline" type="datetime-local" value={value.deadline?.slice(0, 16) || ''} onChange={(event) => onChange({ ...value, deadline: event.target.value ? new Date(event.target.value).toISOString() : undefined })} /></label><label>Allowed capabilities<input aria-label="Allowed capabilities" value={capabilities} onChange={(event) => onCapabilities(event.target.value)} placeholder="code, robot-test, review" /></label></div><label className="scope-checkbox"><input type="checkbox" checked={value.may_expand_scope} onChange={(event) => onChange({ ...value, may_expand_scope: event.target.checked })} /><span><strong>May expand scope</strong><small>Allow topology and work scope to grow without another approval.</small></span></label></div></details>
}

function TopologyValue({ value, actions }: { value: Record<string, unknown>, actions: Array<Record<string, unknown> | string> }) {
  const entries = Object.entries(value || {})
  return <div className="proposal-details">{entries.length > 0 && <div><span>Topology</span><code>{entries.map(([key, item]) => `${key}: ${typeof item === 'string' ? item : JSON.stringify(item)}`).join(' · ')}</code></div>}{actions.length > 0 && <div><span>Proposed actions</span><ol>{actions.map((action, index) => <li key={index}>{typeof action === 'string' ? action : String(action.summary || action.action || JSON.stringify(action))}</li>)}</ol></div>}</div>
}

function modeDescription(mode: AutonomyMode) {
  return { manual: 'Direct Bridge', advise: 'Recommend only', delegate: 'Bounded execution', autonomous: 'Adapt within budget' }[mode]
}

function convergenceState(work: WorkItem): string | undefined {
  const raw = work.extensions?.['agent_bridge.convergence']
  if (!raw || typeof raw !== 'object') return undefined
  const state = (raw as Record<string, unknown>).state
  if (!state || typeof state !== 'object') return undefined
  const status = (state as Record<string, unknown>).status
  return typeof status === 'string' ? status : undefined
}
