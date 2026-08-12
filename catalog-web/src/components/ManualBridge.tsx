import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, ChevronRight, CircleCheck, Clock3, Code2, Plus, RadioTower, Send, Trash2 } from 'lucide-react'
import { useMemo, useState } from 'react'
import { listBridgeExecutions, submitBridgeMessage, submitBridgeRequest } from '../api'
import type { BridgeArtifact, BridgeDeliveryPolicy, BridgeEndpointKind, BridgeMessageKind, BridgeOperation, BridgeSubmission, ManualMessageInput, ManualRequestInput } from '../types'

interface Props { workId?: string }

const endpointKinds: BridgeEndpointKind[] = ['conversation', 'role', 'node', 'capability', 'room', 'endpoint']
const messageKinds: BridgeMessageKind[] = ['message', 'request', 'event', 'response', 'control']
const operations: BridgeOperation[] = ['new_execution', 'resume_conversation', 'wake_endpoint', 'invoke_adapter']

export function ManualBridge({ workId }: Props) {
  const queryClient = useQueryClient()
  const [kind, setKind] = useState<BridgeMessageKind>('request')
  const [operation, setOperation] = useState<BridgeOperation>('new_execution')
  const [destinationKind, setDestinationKind] = useState<BridgeEndpointKind>('capability')
  const [destinationId, setDestinationId] = useState('')
  const [instruction, setInstruction] = useState('')
  const [conversationId, setConversationId] = useState('')
  const [cwd, setCwd] = useState('')
  const [bodyText, setBodyText] = useState('{}')
  const [replyKind, setReplyKind] = useState<BridgeEndpointKind>('endpoint')
  const [replyId, setReplyId] = useState('')
  const [expiryMinutes, setExpiryMinutes] = useState(60)
  const [maxAttempts, setMaxAttempts] = useState(3)
  const [backoff, setBackoff] = useState(5)
  const [ackTimeout, setAckTimeout] = useState(60)
  const [artifacts, setArtifacts] = useState<BridgeArtifact[]>([])
  const [extensionsText, setExtensionsText] = useState('{}')
  const [subject, setSubject] = useState('')
  const [advanced, setAdvanced] = useState(false)
  const [advancedText, setAdvancedText] = useState('')
  const [validationError, setValidationError] = useState<string>()
  const [submission, setSubmission] = useState<BridgeSubmission>()

  const executions = useQuery({
    queryKey: ['bridge-executions', workId],
    queryFn: () => listBridgeExecutions(workId),
    refetchInterval: 5_000,
  })

  const delivery = useMemo<BridgeDeliveryPolicy>(() => ({
    expires_at: new Date(Date.now() + expiryMinutes * 60_000).toISOString(),
    max_attempts: maxAttempts,
    retry_backoff_seconds: backoff,
    acknowledgement_timeout_seconds: ackTimeout,
  }), [ackTimeout, backoff, expiryMinutes, maxAttempts])

  const generated = useMemo(() => ({
    schema_version: 'agent-bridge/v1',
    message_id: '<assigned by server>',
    correlation_id: '<assigned by server>',
    sender: { kind: 'endpoint', id: '<authenticated manual UI>' },
    kind,
    destination: { kind: destinationKind, id: destinationId || '<required>' },
    body: kind === 'request' ? {
      operation,
      instruction: instruction || '<required>',
      ...(operation === 'resume_conversation' && conversationId.trim() ? { conversation_id: conversationId.trim() } : {}),
      ...((operation === 'new_execution' || operation === 'resume_conversation') && cwd.trim() ? { cwd: cwd.trim() } : {}),
      parameters: safeJson(bodyText) ?? '<invalid JSON>',
    } : { ...(safeJson(bodyText) ?? {}), ...(instruction ? { instruction } : {}) },
    ...(replyId ? { reply_to: { kind: replyKind, id: replyId } } : {}),
    ...(workId ? { work_id: workId } : {}),
    delivery,
    artifacts,
    extensions: safeJson(extensionsText) ?? '<invalid JSON>',
    ...(subject ? { custom_subject: subject } : {}),
  }), [artifacts, bodyText, conversationId, cwd, delivery, destinationId, destinationKind, extensionsText, instruction, kind, operation, replyId, replyKind, subject, workId])

  const mutation = useMutation({
    mutationFn: async () => {
      const values = validateAndReadAdvanced(advanced && advancedText ? advancedText : JSON.stringify(generated), generated)
      const target = { kind: destinationKind, id: destinationId }
      const common = {
        work_id: workId,
        delivery: values.delivery,
        artifacts: values.artifacts,
          extensions: kind === 'request' ? {} : values.extensions,
        custom_subject: values.custom_subject,
      }
      if (kind === 'request') {
        const input: ManualRequestInput = {
          ...common,
          operation,
          instruction,
          target,
          conversation_id: operation === 'resume_conversation' ? conversationId.trim() : undefined,
          cwd: (operation === 'new_execution' || operation === 'resume_conversation') && cwd.trim() ? cwd.trim() : undefined,
          adapter: operation === 'invoke_adapter' ? destinationId : undefined,
          parameters: values.body,
          reply_to: replyId ? { kind: replyKind, id: replyId } : undefined,
          envelope_extensions: values.extensions,
        }
        return submitBridgeRequest(input)
      }
      const input: ManualMessageInput = {
        ...common,
        kind,
        destination: target,
          body: { ...values.body, ...(instruction ? { instruction } : {}) },
        reply_to: replyId ? { kind: replyKind, id: replyId } : undefined,
      }
      return submitBridgeMessage(input)
    },
    onMutate: () => setValidationError(undefined),
    onSuccess: async (result) => {
      setSubmission(result)
      await queryClient.invalidateQueries({ queryKey: ['bridge-executions'] })
    },
    onError: (error: Error) => setValidationError(error.message),
  })

  const openAdvanced = () => {
    setAdvanced(!advanced)
    if (!advanced) setAdvancedText(JSON.stringify(generated, null, 2))
    setValidationError(undefined)
  }
  const submit = () => {
    if (!destinationId.trim()) return setValidationError('A stable destination identity is required.')
    if (kind === 'request' && !instruction.trim()) return setValidationError('An instruction is required for execution requests.')
    if (kind === 'request' && operation === 'resume_conversation' && !conversationId.trim()) return setValidationError('A Codex conversation ID is required to resume an agent.')
    mutation.mutate()
  }

  return <section className={`manual-page ${workId ? 'manual-embedded' : ''}`}>
    <header className="manual-header">
      <div><span className="eyebrow">Coordinator bypass</span><h1>Manual Bridge</h1><p>Address the Bridge directly. Manual traffic is delivered exactly as entered and is never approved, reinterpreted, or rerouted by a coordinator.</p></div>
      <span className="manual-mode"><RadioTower size={14} />Manual</span>
    </header>

    <div className="manual-grid">
      <form className="manual-form" onSubmit={(event) => { event.preventDefault(); submit() }}>
        <section className="manual-card">
          <div className="manual-card-title"><span>1</span><div><h2>Address and operation</h2><p>Choose one durable identity and the exact operation to perform.</p></div></div>
          <div className="manual-fields three">
            <label>Message kind<select aria-label="Message kind" value={kind} onChange={(event) => setKind(event.target.value as BridgeMessageKind)}>{messageKinds.map((value) => <option key={value} value={value}>{label(value)}</option>)}</select></label>
            <label>Destination type<select aria-label="Destination type" value={destinationKind} onChange={(event) => setDestinationKind(event.target.value as BridgeEndpointKind)}>{endpointKinds.map((value) => <option key={value} value={value}>{label(value)}</option>)}</select></label>
            <label>Stable identity<input aria-label="Destination identity" value={destinationId} onChange={(event) => setDestinationId(event.target.value)} placeholder="robot-test-runner" /></label>
          </div>
          {kind === 'request' && <label className="manual-field">Execution operation<select aria-label="Execution operation" value={operation} onChange={(event) => setOperation(event.target.value as BridgeOperation)}>{operations.map((value) => <option key={value} value={value}>{label(value)}</option>)}</select></label>}
        </section>

        <section className="manual-card">
          <div className="manual-card-title"><span>2</span><div><h2>Instruction and context</h2><p>Keep the human-readable intent clear; add structured parameters when needed.</p></div></div>
          <label className="manual-field">{kind === 'request' ? 'Instruction' : 'Instruction / message'}<textarea aria-label="Instruction" rows={4} value={instruction} onChange={(event) => setInstruction(event.target.value)} placeholder="Run the ABB server/client reconnect test and return evidence." /></label>
          {kind === 'request' && operation === 'resume_conversation' && <label className="manual-field">Codex conversation ID<input aria-label="Codex conversation ID" value={conversationId} onChange={(event) => setConversationId(event.target.value)} placeholder="019ff7bd-…" /><small>The provider thread to continue; destination identity remains the runner node.</small></label>}
          {kind === 'request' && (operation === 'new_execution' || operation === 'resume_conversation') && <label className="manual-field">Working directory (cwd) <span className="optional">optional</span><input aria-label="Working directory" value={cwd} onChange={(event) => setCwd(event.target.value)} placeholder="/absolute/path/to/workspace" /><small>Recorded with the execution and passed to new or resumed Codex turns.</small></label>}
          <label className="manual-field">Structured body (JSON)<textarea className="code-input" aria-label="Structured body" rows={4} value={bodyText} onChange={(event) => setBodyText(event.target.value)} /></label>
          <div className="manual-fields two"><label>Reply destination type<select aria-label="Reply destination type" value={replyKind} onChange={(event) => setReplyKind(event.target.value as BridgeEndpointKind)}>{endpointKinds.map((value) => <option key={value} value={value}>{label(value)}</option>)}</select></label><label>Reply identity <span className="optional">optional</span><input aria-label="Reply identity" value={replyId} onChange={(event) => setReplyId(event.target.value)} placeholder="ui-session" /></label></div>
        </section>

        <section className="manual-card">
          <div className="manual-card-title"><span>3</span><div><h2>Delivery policy and artifacts</h2><p>Expiry and retries are explicit; attached artifacts remain references.</p></div></div>
          <div className="manual-fields four"><label>Expires in<input aria-label="Expiry minutes" type="number" min={1} value={expiryMinutes} onChange={(event) => setExpiryMinutes(Number(event.target.value))} /><small>minutes</small></label><label>Attempts<input aria-label="Maximum attempts" type="number" min={1} max={100} value={maxAttempts} onChange={(event) => setMaxAttempts(Number(event.target.value))} /></label><label>Retry backoff<input aria-label="Retry backoff" type="number" min={0} value={backoff} onChange={(event) => setBackoff(Number(event.target.value))} /><small>seconds</small></label><label>Ack deadline<input aria-label="Acknowledgement deadline" type="number" min={1} value={ackTimeout} onChange={(event) => setAckTimeout(Number(event.target.value))} /><small>seconds</small></label></div>
          <ArtifactEditor artifacts={artifacts} onChange={setArtifacts} />
        </section>

        <section className="manual-card advanced-card">
          <button type="button" className="advanced-toggle" onClick={openAdvanced}>{advanced ? <ChevronDown size={15} /> : <ChevronRight size={15} />}<Code2 size={14} /><span><strong>Advanced envelope</strong><small>Generated identities stay server-controlled</small></span></button>
          {advanced && <div className="advanced-content"><label>Custom subject <span className="optional">optional</span><input aria-label="Custom subject" value={subject} onChange={(event) => { setSubject(event.target.value); setAdvancedText('') }} placeholder="bridge.v1.inbox.endpoint.custom" /></label><label>Namespaced extensions (JSON)<textarea aria-label="Namespaced extensions" className="code-input" rows={3} value={extensionsText} onChange={(event) => { setExtensionsText(event.target.value); setAdvancedText('') }} /></label><label>Generated envelope<textarea aria-label="Generated envelope" className="code-input envelope-input" rows={18} value={advancedText || JSON.stringify(generated, null, 2)} onChange={(event) => setAdvancedText(event.target.value)} /></label><p>Message, correlation, sender, and execution identities are assigned and authorized by the server; edits to those preview fields are ignored.</p></div>}
        </section>

        {validationError && <div className="manual-error" role="alert">{validationError}</div>}
        <div className="manual-submit"><div><strong>Direct manual dispatch</strong><span>{workId ? `Attached to ${workId} for visibility` : 'Not attached to coordinator work'}</span></div><button className="primary-button" disabled={mutation.isPending} type="submit"><Send size={14} />{mutation.isPending ? 'Submitting…' : 'Submit to Bridge'}</button></div>
      </form>

      <aside className="manual-status">
        <div className="status-heading"><h2>Delivery and execution</h2><span>{executions.data?.length ?? 0}</span></div>
        {submission && <article className="submission-state"><CircleCheck size={17} /><div><strong>Accepted by Bridge</strong><code>{submission.execution_id as string || submission.message_id as string || 'Server identity assigned'}</code></div></article>}
        {executions.isError ? <p className="manual-error">{executions.error.message}</p> : executions.isLoading ? <div className="list-state"><span className="spinner" />Loading state…</div> : executions.data?.length ? <div className="execution-list">{executions.data.map((execution) => { const providerThreadId = execution.result?.output?.provider_thread_id; return <article key={execution.execution_id}><i className={`execution-dot ${execution.status}`} /><div><strong>{execution.instruction || label(execution.operation || 'execution')}</strong><code>{execution.execution_id}</code>{typeof providerThreadId === 'string' && <code>Agent: {providerThreadId}</code>}<span>{execution.target ? `${execution.target.kind}:${execution.target.id}` : 'Target pending'} · {execution.attempt_count ?? 0} attempts</span></div><em>{label(execution.status)}</em></article> })}</div> : <div className="manual-empty"><Clock3 size={22} /><strong>No executions yet</strong><span>Submitted requests and their durable state will appear here.</span></div>}
      </aside>
    </div>
  </section>
}

function ArtifactEditor({ artifacts, onChange }: { artifacts: BridgeArtifact[], onChange: (value: BridgeArtifact[]) => void }) {
  return <div className="artifact-editor"><div className="artifact-heading"><span>Artifact references</span><button type="button" onClick={() => onChange([...artifacts, { name: '', uri: '' }])}><Plus size={12} />Add</button></div>{artifacts.map((artifact, index) => <div className="artifact-row" key={index}><input aria-label={`Artifact ${index + 1} name`} placeholder="Evidence name" value={artifact.name} onChange={(event) => onChange(artifacts.map((item, itemIndex) => itemIndex === index ? { ...item, name: event.target.value } : item))} /><input aria-label={`Artifact ${index + 1} URI`} placeholder="file:///… or https://…" value={artifact.uri} onChange={(event) => onChange(artifacts.map((item, itemIndex) => itemIndex === index ? { ...item, uri: event.target.value } : item))} /><input aria-label={`Artifact ${index + 1} media type`} placeholder="application/json" value={artifact.media_type || ''} onChange={(event) => onChange(artifacts.map((item, itemIndex) => itemIndex === index ? { ...item, media_type: event.target.value } : item))} /><button type="button" title="Remove artifact" onClick={() => onChange(artifacts.filter((_, itemIndex) => itemIndex !== index))}><Trash2 size={12} /></button></div>)}</div>
}

interface AdvancedValues {
  body: Record<string, unknown>
  delivery: BridgeDeliveryPolicy
  artifacts: BridgeArtifact[]
  extensions: Record<string, unknown>
  custom_subject?: string
}

function validateAndReadAdvanced(text: string, generated: Record<string, unknown>): AdvancedValues {
  const parsed = safeJson(text)
  if (!parsed) throw new Error('The generated envelope must be a JSON object.')
  const bodyCandidate = parsed.body
  const requestBody = typeof bodyCandidate === 'object' && bodyCandidate && 'parameters' in bodyCandidate ? (bodyCandidate as Record<string, unknown>).parameters : bodyCandidate
  if (!isObject(requestBody)) throw new Error('Structured body must be a JSON object.')
  const extensions = parsed.extensions
  if (!isObject(extensions)) throw new Error('Extensions must be a JSON object.')
  const invalidKey = Object.keys(extensions).find((key) => !key.includes('.') && !key.includes('/') && !key.includes(':'))
  if (invalidKey) throw new Error(`Extension key “${invalidKey}” must be namespaced.`)
  const delivery = parsed.delivery
  if (!isObject(delivery) || Number(delivery.max_attempts) < 1 || Number(delivery.acknowledgement_timeout_seconds) <= 0) throw new Error('Delivery policy values are invalid.')
  const subject = String(parsed.custom_subject || '')
  if (subject && (subject.includes(' ') || subject.includes('*') || subject.includes('>') || subject.startsWith('.') || subject.endsWith('.'))) throw new Error('Custom subject contains an invalid NATS token.')
  const artifactValues = Array.isArray(parsed.artifacts) ? parsed.artifacts : []
  if (artifactValues.some((value) => !isObject(value) || !value.name || !value.uri)) throw new Error('Every artifact requires a name and URI.')
  void generated
  return { body: requestBody, delivery: delivery as unknown as BridgeDeliveryPolicy, artifacts: artifactValues as BridgeArtifact[], extensions, custom_subject: subject || undefined }
}

function safeJson(text: string): Record<string, unknown> | null {
  try { const value: unknown = JSON.parse(text); return isObject(value) ? value : null } catch { return null }
}
function isObject(value: unknown): value is Record<string, unknown> { return typeof value === 'object' && value !== null && !Array.isArray(value) }
function label(value: string) { return value.replaceAll('_', ' ') }
