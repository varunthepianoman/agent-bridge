export type ConversationStatus = 'active' | 'idle' | 'completed' | 'failed' | 'unknown'

export interface ConversationLocation {
  node_id?: string
  node_name?: string
  environment?: string
  cwd?: string
  available?: boolean
  last_seen_at?: string
}

export type NodeReachability = 'online' | 'stale' | 'offline' | 'unknown'

export interface NodeEnvironment {
  id: string
  name?: string
  kind?: string
  available: boolean
  last_seen_at?: string
}

export interface CatalogNode {
  id: string
  name: string
  platform?: string
  reachability: NodeReachability
  reachable: boolean
  last_seen_at?: string
  environments: NodeEnvironment[]
  capabilities?: string[]
}

export interface GitMetadata {
  repository_url?: string
  branch?: string
  commit_hash?: string
}

export interface TranscriptMessage {
  id?: string
  role: 'user' | 'assistant' | 'system'
  text: string
  created_at?: string
}

export interface Conversation {
  id: string
  provider: string
  provider_thread_id?: string
  title?: string
  catalog_title?: string
  preview?: string
  status?: ConversationStatus
  source_kind?: string
  created_at?: string
  updated_at?: string
  last_active_at?: string
  location?: ConversationLocation
  cwd?: string
  git?: GitMetadata
  tags?: string[]
  notes?: string
  pinned?: boolean
  hidden?: boolean
  archived?: boolean
  messages?: TranscriptMessage[]
  transcript_text?: string
  resume_command?: string
}

export interface ConversationListResponse {
  items: Conversation[]
  total: number
  limit?: number
  offset?: number
}

export interface ConversationFilters {
  query: string
  status: string
  source: string
  view: 'all' | 'pinned' | 'archived' | 'hidden'
}

export interface ConversationPatch {
  title?: string
  tags?: string[]
  notes?: string
  pinned?: boolean
  hidden?: boolean
  archived?: boolean
}

export interface ActionResponse {
  ok?: boolean
  command?: string
  launched?: boolean
  message?: string
  queued?: boolean
  node_id?: string
}

export type WorkStatus = 'planned' | 'active' | 'blocked' | 'completed' | 'archived'
export type RoleStatus = 'draft' | 'planned' | 'active' | 'paused' | 'blocked' | 'completed' | 'archived'
export type RoleType = 'portfolio_coordinator' | 'work_coordinator' | 'worker' | 'specialist'

export interface WorkItem {
  id: string
  title: string
  objective?: string
  status: WorkStatus
  project?: string
  repository_id?: string
  repository?: string
  branch?: string
  pull_request?: string
  tags?: string[]
  created_at?: string
  updated_at?: string
  conversation_ids?: string[]
}

export interface WorkItemDetail extends WorkItem {
  conversations: Conversation[]
}

export interface WorkItemInput {
  title: string
  objective?: string
  status?: WorkStatus
  project?: string
  repository?: string
  branch?: string
  pull_request?: string
  tags?: string[]
}

export interface CoordinatorRole {
  id: string
  role_id?: string
  role_type: RoleType
  work_item_id?: string
  scope?: string
  parent_role_id?: string
  charter: string
  authority_profile?: string
  current_conversation_id?: string
  checkpoint_version: number
  status: RoleStatus
  updated_at?: string
  autonomy_mode?: AutonomyMode
  latest_checkpoint?: {
    version: number
    status: RoleStatus
    summary: string
    blockers: string[]
    recommended_next_action?: string
    created_at?: string
  }
  rollup?: { stale: boolean, incorporated_checkpoint_version?: number }
  lease?: OperationsLease
}

export interface RoleInput {
  role_type: RoleType
  work_item_id?: string
  parent_role_id?: string
  charter: string
  authority_profile?: string
  status?: RoleStatus
}

export type RelationshipKind = 'reports_to' | 'coordinates' | 'depends_on' | 'audits' | 'collaborates_with' | 'related_to' | string
export type RelationshipEntityType = 'work' | 'role' | 'conversation' | 'artifact' | 'node' | 'capability' | 'room' | 'endpoint'

export interface WorkRelationship {
  id: string
  work_item_id?: string
  source_type: RelationshipEntityType
  source_id: string
  target_type: RelationshipEntityType
  target_id: string
  kind: RelationshipKind
  label?: string
  created_at?: string
}

export type RelationshipInput = Omit<WorkRelationship, 'id' | 'created_at'>

export type BridgeEndpointKind = 'conversation' | 'role' | 'node' | 'capability' | 'room' | 'endpoint'
export type BridgeMessageKind = 'message' | 'request' | 'event' | 'response' | 'control'
export type BridgeOperation = 'new_execution' | 'resume_conversation' | 'wake_endpoint' | 'invoke_adapter'

export interface BridgeEndpoint {
  kind: BridgeEndpointKind
  id: string
}

export interface BridgeArtifact {
  name: string
  uri: string
  media_type?: string
}

export interface BridgeDeliveryPolicy {
  expires_at?: string
  max_attempts: number
  retry_backoff_seconds: number
  acknowledgement_timeout_seconds: number
}

export interface ManualMessageInput {
  kind: BridgeMessageKind
  destination: BridgeEndpoint
  body: Record<string, unknown>
  reply_to?: BridgeEndpoint
  work_id?: string
  delivery: BridgeDeliveryPolicy
  artifacts: BridgeArtifact[]
  extensions: Record<string, unknown>
  custom_subject?: string
}

export interface ManualRequestInput {
  operation: BridgeOperation
  instruction: string
  target: BridgeEndpoint
  work_id?: string
  conversation_id?: string
  cwd?: string
  adapter?: string
  parameters: Record<string, unknown>
  delivery: BridgeDeliveryPolicy
  artifacts: BridgeArtifact[]
  extensions: Record<string, unknown>
  envelope_extensions?: Record<string, unknown>
  custom_subject?: string
  reply_to?: BridgeEndpoint
}

export interface BridgeSubmission {
  message_id?: string
  execution_id?: string
  correlation_id?: string
  status?: string
  subject?: string
  envelope?: Record<string, unknown>
  [key: string]: unknown
}

export interface BridgeExecution {
  execution_id: string
  status: string
  operation?: string
  instruction?: string
  target?: BridgeEndpoint
  work_id?: string
  attempt_count?: number
  requested_at?: string
  updated_at?: string
  error?: string
  result?: { summary?: string, output?: Record<string, unknown> }
  attempts?: Array<{ attempt_id?: string, status?: string }>
}

export type AutonomyMode = 'manual' | 'advise' | 'delegate' | 'autonomous'

export interface AuthorityLimits {
  max_parallel_executions: number
  max_attempts: number
  token_budget?: number
  cost_budget_usd?: number
  deadline?: string
  allowed_capabilities: string[]
  may_expand_scope: boolean
}

export interface CoordinatorIntakeInput {
  objective: string
  mode: Exclude<AutonomyMode, 'manual'>
  work_id?: string
  target_role_id?: string
  context: Record<string, unknown>
  authority: AuthorityLimits
  artifacts: BridgeArtifact[]
  extensions: Record<string, unknown>
}

export interface CoordinatorIntake {
  request_id: string
  request?: CoordinatorIntakeInput
  objective?: string
  mode?: AutonomyMode
  status: 'submitted' | 'planning' | 'awaiting_approval' | 'approved' | 'rejected' | 'active' | 'completed' | 'failed'
  routed_work_id?: string
  routed_role_id?: string
  proposed_actions: Array<Record<string, unknown> | string>
  proposed_topology: Record<string, unknown>
  attention_required?: string
  approval_required: boolean
  executed: boolean
  created_at?: string
  updated_at?: string
}

export interface CoordinatorReport {
  report_id: string
  reporting_role_id: string
  recipient_role_id: string
  checkpoint_version: number
  status: RoleStatus
  summary: string
  decisions: string[]
  attention_required?: string
  recommended_action?: string
  created_at?: string
}

export interface CoordinatorRollup {
  parent_role_id?: string
  child_role_id: string
  current_checkpoint_version: number
  incorporated_checkpoint_version: number
  stale: boolean
  report?: CoordinatorReport
}

export interface CoordinatorActivation {
  activation_id: string
  role_id: string
  intake_request_id?: string
  holder_id: string
  fencing_token: number
  status: string
  checkpoint_version_before: number
  conversation_id?: string
  authority?: AuthorityLimits
  usage?: Record<string, unknown>
  started_at?: string
  finished_at?: string
}

export interface RoleCoordinationContext {
  role: CoordinatorRole
  checkpoint?: Record<string, unknown>
  reports?: CoordinatorReport[]
  unresolved_items?: string[]
  artifacts?: BridgeArtifact[]
}

export interface OperationsSummary {
  status: 'healthy' | 'degraded' | string
  broker_status: string
  background_status: string
  coordinator?: { enabled?: boolean, running?: boolean, unavailable_reason?: string }
  counts: {
    nodes: number
    unreachable_nodes: number
    roles: number
    executions: number
    pending_requests: number
    attention_required: number
    unresolved_dead_letters: number
    consumer_pending: number
  }
  advisories: OperationsAdvisory[]
  observed_at: string
}

export interface OperationsAdvisory {
  severity: 'info' | 'warning' | 'error' | string
  code: string
  message: string
  count?: number
  node_id?: string
}

export interface OperationsLease {
  lease_type: string
  resource_id: string
  holder_id: string
  fencing_token: number | null
  acquired_at?: string
  expires_at?: string
  active: boolean
}

export interface OperationsRetry {
  attempt_id: string
  execution_id: string
  attempt_number: number
  node_id?: string
  status: string
  error?: string
  updated_at?: string
}

export interface OperationsDeadLetter {
  dead_letter_id: string
  message_id: string
  stream: string
  consumer: string
  reason: string
  attempts: number
  dead_lettered_at: string
  resolved_at?: string
}

export interface OperationsArtifact extends BridgeArtifact {
  artifact_id?: string
  execution_id?: string
  work_id?: string
  size?: number
  sha256?: string
  sources?: Array<{
    source_type: string
    execution_id?: string
    work_id?: string
    role_id?: string
    checkpoint_version?: number
  }>
}

export interface BrokerDiagnostics {
  status: string
  connected: boolean
  streams: Array<{ name: string, messages?: number, bytes?: number, consumer_count?: number }>
  consumers: Array<{
    stream: string
    consumer: string
    pending?: number
    ack_pending?: number
    redelivered?: number
    pending_count?: number
    ack_pending_count?: number
    redelivered_count?: number
    state?: string
    observed_at?: string
  }>
  advisories: OperationsAdvisory[]
}

export interface DiagnosticMessage {
  message_id: string
  subject: string
  message_type: string
  source_kind?: string
  source_id?: string
  destination_kind?: string
  destination_id?: string
  work_id?: string
  state: string
  stream?: string
  last_observed_at: string
}

export interface DiagnosticDelivery {
  delivery_id: string
  message_id: string
  consumer: string
  state: string
  redelivery_count: number
  ack_deadline_at?: string
  last_observed_at: string
}

export interface BackgroundDiagnostics {
  status: string
  tasks: Array<{
    name: string
    state: string
    critical: boolean
    error?: string
    started_at: string
    stopped_at?: string
  }>
}

export interface OperationsSnapshot {
  summary: OperationsSummary
  nodes: CatalogNode[]
  roles: CoordinatorRole[]
  pending: BridgeExecution[]
  leases: OperationsLease[]
  retries: OperationsRetry[]
  deadLetters: OperationsDeadLetter[]
  artifacts: OperationsArtifact[]
  executions: BridgeExecution[]
  broker: BrokerDiagnostics
  messages: DiagnosticMessage[]
  deliveries: DiagnosticDelivery[]
  background: BackgroundDiagnostics
  relationships: WorkRelationship[]
}
