import type {
  ActionResponse,
  CatalogNode,
  BridgeExecution,
  CoordinatorActivation,
  CoordinatorIntake,
  CoordinatorIntakeInput,
  CoordinatorReport,
  CoordinatorRollup,
  BridgeSubmission,
  Conversation,
  ConversationFilters,
  ConversationListResponse,
  ConversationPatch,
  CoordinatorRole,
  RelationshipInput,
  ManualMessageInput,
  ManualRequestInput,
  RoleInput,
  WorkItem,
  WorkItemDetail,
  WorkItemInput,
  WorkRelationship,
  OperationsSnapshot,
  OperationsSummary,
  OperationsAdvisory,
  OperationsLease,
  OperationsRetry,
  OperationsDeadLetter,
  OperationsArtifact,
  BrokerDiagnostics,
  DiagnosticMessage,
  DiagnosticDelivery,
  BackgroundDiagnostics,
} from "./types";

const API_ROOT = import.meta.env.VITE_API_BASE ?? "/api/v1";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_ROOT}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const detail = (await response.json().catch(() => null)) as {
      detail?: string;
    } | null;
    throw new Error(detail?.detail ?? `Request failed (${response.status})`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export async function listConversations(
  filters: ConversationFilters,
): Promise<ConversationListResponse> {
  const params = new URLSearchParams();
  if (filters.query.trim()) params.set("q", filters.query.trim());
  if (filters.status !== "all") params.set("status", filters.status);
  if (filters.source !== "all") params.set("source", filters.source);
  if (filters.view === "pinned") params.set("pinned", "true");
  if (filters.view === "all" || filters.view === "pinned")
    params.set("archived", "false");
  if (filters.view === "archived") params.set("archived", "true");
  if (filters.view === "hidden") params.set("include_hidden", "true");
  const data = await request<
    | {
        items: RawConversation[];
        total: number;
        limit?: number;
        offset?: number;
      }
    | RawConversation[]
  >(`/conversations?${params}`);
  const response = Array.isArray(data)
    ? { items: data, total: data.length }
    : data;
  const normalized = response.items
    .map(normalizeConversation)
    .filter((conversation) => {
      if (filters.view === "hidden") return conversation.hidden;
      if (filters.view === "archived")
        return conversation.archived && !conversation.hidden;
      if (filters.view === "all")
        return !conversation.hidden && !conversation.archived;
      return !conversation.hidden;
    });
  return { ...response, items: normalized, total: normalized.length };
}

export async function getConversation(id: string): Promise<Conversation> {
  return normalizeConversation(
    await request<RawConversation>(`/conversations/${encodeURIComponent(id)}`),
  );
}

export async function updateConversation(
  id: string,
  patch: ConversationPatch,
): Promise<Conversation> {
  const value = await request<RawConversation>(
    `/conversations/${encodeURIComponent(id)}`,
    {
      method: "PATCH",
      body: JSON.stringify(patch),
    },
  );
  return normalizeConversation(value);
}

export async function syncCatalog(): Promise<ActionResponse> {
  const result = await request<{ discovered: number; imported: number }>(
    "/actions/sync",
    {
      method: "POST",
      body: JSON.stringify({ include_turns: true }),
    },
  );
  return {
    ok: true,
    message: `Synced ${result.imported} of ${result.discovered} conversations`,
  };
}

export async function resumeConversation(
  conversationId: string,
): Promise<ActionResponse> {
  const result = await request<ActionResponse & { detail?: string }>(
    "/actions/resume",
    {
      method: "POST",
      body: JSON.stringify({ conversation_id: conversationId, launch: true }),
    },
  );
  return { ...result, message: result.detail ?? result.message };
}

export async function listNodes(): Promise<CatalogNode[]> {
  return collection(
    await request<{ items: RawNode[] } | RawNode[]>("/nodes"),
  ).map(normalizeNode);
}

interface RawConversation {
  id?: string;
  conversation_id?: string;
  provider: string;
  provider_thread_id?: string;
  title?: string;
  catalog_title?: string;
  provider_title?: string;
  preview?: string;
  transcript_text?: string;
  status?: Conversation["status"];
  source?: string;
  source_kind?: string;
  node_id?: string;
  node_name?: string;
  environment_id?: string;
  cwd?: string;
  repository?: string;
  branch?: string;
  commit_hash?: string;
  created_at?: string;
  updated_at?: string;
  last_activity_at?: string;
  last_active_at?: string;
  tags?: string[];
  notes?: string;
  pinned?: boolean;
  hidden?: boolean;
  archived?: boolean;
  resume_command?: string;
  interactive_open?: Conversation["interactive_open"];
  available?: boolean;
  node_reachable?: boolean;
  environment_available?: boolean;
  location_available?: boolean;
  location?: {
    node_id?: string;
    node_name?: string;
    environment?: string;
    environment_id?: string;
    cwd?: string;
    available?: boolean;
    last_seen_at?: string;
  };
}

function normalizeConversation(raw: RawConversation): Conversation {
  const explicitAvailability =
    raw.location?.available ??
    raw.location_available ??
    raw.environment_available ??
    raw.node_reachable ??
    raw.available;
  return {
    ...raw,
    id: raw.conversation_id || raw.id || raw.provider_thread_id || "",
    catalog_title: raw.catalog_title,
    title: raw.title || raw.provider_title,
    source_kind: raw.source_kind || raw.source,
    last_active_at: raw.last_active_at || raw.last_activity_at,
    transcript_text: raw.transcript_text,
    interactive_open: raw.interactive_open,
    location: {
      node_id: raw.location?.node_id || raw.node_id,
      node_name: raw.location?.node_name || raw.node_name || raw.node_id,
      environment:
        raw.location?.environment ||
        raw.location?.environment_id ||
        raw.environment_id,
      cwd: raw.location?.cwd || raw.cwd,
      available: explicitAvailability ?? true,
      last_seen_at: raw.location?.last_seen_at,
    },
    git: {
      repository_url: raw.repository,
      branch: raw.branch,
      commit_hash: raw.commit_hash,
    },
  };
}

interface RawNode {
  node_id?: string;
  id?: string;
  name?: string;
  hostname?: string;
  display_name?: string;
  platform?: string;
  status?: string;
  reachability?: string;
  reachable?: boolean;
  last_seen_at?: string;
  environments?: Array<{
    environment_id?: string;
    id?: string;
    name?: string;
    display_name?: string;
    kind?: string;
    available?: boolean;
    last_seen_at?: string;
  }>;
  capabilities?: string[];
}

function normalizeNode(raw: RawNode): CatalogNode {
  const id = raw.node_id || raw.id || "";
  const rawState = raw.reachability || raw.status;
  const reachability =
    rawState === "online" || rawState === "stale" || rawState === "offline"
      ? rawState
      : raw.reachable === true
        ? "online"
        : raw.reachable === false
          ? "offline"
          : "unknown";
  const reachable = raw.reachable ?? reachability === "online";
  return {
    id,
    name: raw.display_name || raw.name || raw.hostname || id,
    platform: raw.platform,
    reachability,
    reachable,
    last_seen_at: raw.last_seen_at,
    environments: (raw.environments ?? []).map((environment) => ({
      id: environment.environment_id || environment.id || "",
      name: environment.display_name || environment.name,
      kind: environment.kind,
      available: environment.available ?? reachable,
      last_seen_at: environment.last_seen_at,
    })),
    capabilities: raw.capabilities,
  };
}

function collection<T>(value: { items: T[] } | T[]): T[] {
  return Array.isArray(value) ? value : value.items;
}

export async function listWorkItems(): Promise<WorkItem[]> {
  return collection(
    await request<{ items: RawWorkItem[] } | RawWorkItem[]>("/work-items"),
  ).map(normalizeWorkItem);
}

export async function getWorkItem(id: string): Promise<WorkItemDetail> {
  const value = normalizeWorkItem(
    await request<RawWorkItem>(`/work-items/${encodeURIComponent(id)}`),
  );
  const relationships = await listRelationships();
  const conversationIds = relationships
    .filter(
      (item) =>
        item.source_id === id &&
        item.target_type === "conversation" &&
        item.kind === "contains",
    )
    .map((item) => item.target_id);
  const conversations = (
    await Promise.allSettled(conversationIds.map(getConversation))
  ).flatMap((result) => (result.status === "fulfilled" ? [result.value] : []));
  return { ...value, conversation_ids: conversationIds, conversations };
}

export function createWorkItem(input: WorkItemInput): Promise<WorkItem> {
  return request<RawWorkItem>("/work-items", {
    method: "POST",
    body: JSON.stringify(workInput(input)),
  }).then(normalizeWorkItem);
}

export function updateWorkItem(
  id: string,
  input: Partial<WorkItemInput>,
): Promise<WorkItem> {
  return request<RawWorkItem>(`/work-items/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(workInput(input)),
  }).then(normalizeWorkItem);
}

export function approveConvergencePublish(
  workItemId: string,
): Promise<{ work_id: string; status: string }> {
  return request(
    `/work-items/${encodeURIComponent(workItemId)}/convergence/approve-publish`,
    { method: "POST" },
  );
}

export function approveConvergenceImplementation(
  workItemId: string,
): Promise<{ work_id: string; status: string }> {
  return request(
    `/work-items/${encodeURIComponent(workItemId)}/convergence/approve-implementation`,
    { method: "POST" },
  );
}

export function attachConversation(
  workItemId: string,
  conversationId: string,
): Promise<void> {
  return request(
    `/work-items/${encodeURIComponent(workItemId)}/conversations`,
    {
      method: "POST",
      body: JSON.stringify({ conversation_id: conversationId }),
    },
  );
}

export function detachConversation(
  workItemId: string,
  conversationId: string,
): Promise<void> {
  return request(
    `/work-items/${encodeURIComponent(workItemId)}/conversations/${encodeURIComponent(conversationId)}`,
    { method: "DELETE" },
  );
}

export async function listRoles(
  workItemId?: string,
): Promise<CoordinatorRole[]> {
  const suffix = workItemId
    ? `?work_item_id=${encodeURIComponent(workItemId)}`
    : "";
  return collection(
    await request<{ items: RawRole[] } | RawRole[]>(`/roles${suffix}`),
  ).map(normalizeRole);
}

export function createRole(input: RoleInput): Promise<CoordinatorRole> {
  const { work_item_id, ...rest } = input;
  return request<RawRole>("/roles", {
    method: "POST",
    body: JSON.stringify({
      ...rest,
      scope: work_item_id ? `work:${work_item_id}` : "portfolio",
      status: input.status === "planned" ? "draft" : input.status,
    }),
  }).then(normalizeRole);
}

export function updateRole(
  id: string,
  input: Partial<RoleInput>,
): Promise<CoordinatorRole> {
  const { work_item_id, ...rest } = input;
  return request<RawRole>(`/roles/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify({
      ...rest,
      ...(work_item_id ? { scope: `work:${work_item_id}` } : {}),
    }),
  }).then(normalizeRole);
}

export async function listRelationships(
  workItemId?: string,
): Promise<WorkRelationship[]> {
  // Fetch the relationship set once and scope it using the work's known entities in
  // the view. Server-side work_item_id only finds edges directly touching the work.
  void workItemId;
  return collection(
    await request<{ items: RawRelationship[] } | RawRelationship[]>(
      "/relationships",
    ),
  ).map(normalizeRelationship);
}

export function createRelationship(
  input: RelationshipInput,
): Promise<WorkRelationship> {
  return request<RawRelationship>("/relationships", {
    method: "POST",
    body: JSON.stringify({
      source: { kind: endpointKind(input.source_type), id: input.source_id },
      target: { kind: endpointKind(input.target_type), id: input.target_id },
      type: input.kind,
      metadata: input.work_item_id ? { work_item_id: input.work_item_id } : {},
    }),
  }).then(normalizeRelationship);
}

interface RawWorkItem extends Omit<WorkItem, "id"> {
  work_id?: string;
  id?: string;
}
interface RawRole extends Omit<CoordinatorRole, "id"> {
  role_id?: string;
  id?: string;
}
interface RawRelationship {
  relationship_id?: string;
  id?: string;
  source: { kind: string; id: string };
  target: { kind: string; id: string };
  type: string;
  metadata?: Record<string, unknown>;
  created_at?: string;
}

function normalizeWorkItem(raw: RawWorkItem): WorkItem {
  return {
    ...raw,
    id: raw.work_id || raw.id || "",
    repository: raw.repository_id,
  };
}

function workInput(input: Partial<WorkItemInput>) {
  const { repository, project: _project, ...rest } = input;
  return { ...rest, ...(repository ? { repository_id: repository } : {}) };
}

function normalizeRole(raw: RawRole): CoordinatorRole {
  return {
    ...raw,
    id: raw.role_id || raw.id || "",
    work_item_id: raw.scope?.startsWith("work:")
      ? raw.scope.slice(5)
      : undefined,
  };
}

function normalizeRelationship(raw: RawRelationship): WorkRelationship {
  return {
    id: raw.relationship_id || raw.id || "",
    work_item_id:
      typeof raw.metadata?.work_item_id === "string"
        ? raw.metadata.work_item_id
        : undefined,
    source_type: entityType(raw.source.kind),
    source_id: raw.source.id,
    target_type: entityType(raw.target.kind),
    target_id: raw.target.id,
    kind: raw.type,
    created_at: raw.created_at,
  };
}

function entityType(kind: string): WorkRelationship["source_type"] {
  if (
    kind === "conversation" ||
    kind === "role" ||
    kind === "artifact" ||
    kind === "node" ||
    kind === "capability" ||
    kind === "room" ||
    kind === "endpoint"
  )
    return kind;
  return "work";
}

function endpointKind(kind: WorkRelationship["source_type"]) {
  return kind === "work" ? "endpoint" : kind;
}

export function deleteRelationship(id: string): Promise<void> {
  return request(`/relationships/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export async function submitBridgeMessage(
  input: ManualMessageInput,
): Promise<BridgeSubmission> {
  const { custom_subject, ...envelope } = input;
  const result = await request<BridgeSubmission>("/bridge/messages", {
    method: "POST",
    body: JSON.stringify({
      envelope,
      ...(custom_subject ? { subject: custom_subject } : {}),
    }),
  });
  const message = result.message as Record<string, unknown> | undefined;
  const envelopeResult = result.envelope as Record<string, unknown> | undefined;
  return {
    ...result,
    message_id:
      result.message_id ||
      (message?.message_id as string | undefined) ||
      (envelopeResult?.message_id as string | undefined),
  };
}

export async function submitBridgeRequest(
  input: ManualRequestInput,
): Promise<BridgeSubmission> {
  const { custom_subject, reply_to, envelope_extensions, ...bridgeRequest } =
    input;
  const envelope = {
    ...(reply_to ? { reply_to } : {}),
    ...(envelope_extensions && Object.keys(envelope_extensions).length
      ? { extensions: envelope_extensions }
      : {}),
  };
  const result = await request<BridgeSubmission>("/bridge/requests", {
    method: "POST",
    body: JSON.stringify({
      request: bridgeRequest,
      ...(Object.keys(envelope).length ? { envelope } : {}),
      ...(custom_subject ? { subject: custom_subject } : {}),
    }),
  });
  const execution = result.execution as Record<string, unknown> | undefined;
  const message = result.message as Record<string, unknown> | undefined;
  return {
    ...result,
    execution_id:
      result.execution_id || (execution?.execution_id as string | undefined),
    message_id:
      result.message_id || (message?.message_id as string | undefined),
  };
}

export async function listBridgeExecutions(
  workId?: string,
): Promise<BridgeExecution[]> {
  const suffix = workId ? `?work_id=${encodeURIComponent(workId)}` : "";
  return collection(
    await request<{ items: BridgeExecution[] } | BridgeExecution[]>(
      `/bridge/executions${suffix}`,
    ),
  ).map((execution) => ({
    ...execution,
    attempt_count: execution.attempt_count ?? execution.attempts?.length ?? 0,
  }));
}

export async function listCoordinatorIntakes(): Promise<CoordinatorIntake[]> {
  return collection(
    await request<{ items: CoordinatorIntake[] } | CoordinatorIntake[]>(
      "/coordinator/intake",
    ),
  );
}

export function submitCoordinatorIntake(
  input: CoordinatorIntakeInput,
): Promise<CoordinatorIntake> {
  return request("/coordinator/intake", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function decideCoordinatorIntake(
  requestId: string,
  decision: "approve" | "reject",
  note?: string,
  authority?: CoordinatorIntakeInput["authority"],
): Promise<CoordinatorIntake> {
  return request(
    `/coordinator/intake/${encodeURIComponent(requestId)}/decision`,
    {
      method: "POST",
      body: JSON.stringify({
        decision,
        ...(note ? { note } : {}),
        ...(authority ? { authority } : {}),
      }),
    },
  );
}

export async function listRoleReports(
  roleId: string,
): Promise<CoordinatorReport[]> {
  return collection(
    await request<{ items: CoordinatorReport[] } | CoordinatorReport[]>(
      `/roles/${encodeURIComponent(roleId)}/reports`,
    ),
  );
}

export async function listCoordinatorRollups(
  roleId: string,
): Promise<CoordinatorRollup[]> {
  return collection(
    await request<{ items: CoordinatorRollup[] } | CoordinatorRollup[]>(
      `/coordinator/roles/${encodeURIComponent(roleId)}/rollups`,
    ),
  );
}

export async function listCoordinatorActivations(
  roleId: string,
): Promise<CoordinatorActivation[]> {
  return collection(
    await request<{ items: CoordinatorActivation[] } | CoordinatorActivation[]>(
      `/coordinator/roles/${encodeURIComponent(roleId)}/activations`,
    ),
  );
}

export function activateCoordinatorRole(
  roleId: string,
  intakeRequestId: string,
): Promise<{ accepted: boolean; role_id: string; intake_request_id: string }> {
  return request(
    `/coordinator/roles/${encodeURIComponent(roleId)}/activations`,
    {
      method: "POST",
      body: JSON.stringify({ intake_request_id: intakeRequestId }),
    },
  );
}

export async function getOperationsSnapshot(): Promise<OperationsSnapshot> {
  const [
    summary,
    nodes,
    roles,
    pending,
    leases,
    retries,
    deadLetters,
    artifacts,
    executions,
    broker,
    messages,
    deliveries,
    background,
    relationships,
  ] = await Promise.all([
    request<OperationsSummary>("/observability/summary"),
    request<{ items: RawNode[] }>("/observability/nodes"),
    request<{ items: RawRole[] }>("/observability/roles"),
    request<{ items: BridgeExecution[] }>("/observability/pending-requests"),
    request<{ items: OperationsLease[] }>("/observability/leases"),
    request<{ items: OperationsRetry[] }>("/observability/retries"),
    request<{ items: OperationsDeadLetter[] }>("/observability/dead-letters"),
    request<{ items: OperationsArtifact[] }>("/observability/artifacts"),
    request<{ items: BridgeExecution[] }>("/observability/executions"),
    request<BrokerDiagnostics>("/observability/broker"),
    request<{ items: DiagnosticMessage[] }>("/diagnostics/messages"),
    request<{ items: DiagnosticDelivery[] }>("/diagnostics/deliveries"),
    request<BackgroundDiagnostics>("/diagnostics/background"),
    request<{ items: RawRelationship[] }>("/relationships"),
  ]);
  return {
    summary,
    nodes: nodes.items.map(normalizeNode),
    roles: roles.items.map(normalizeRole),
    pending: pending.items,
    leases: leases.items,
    retries: retries.items,
    deadLetters: deadLetters.items,
    artifacts: artifacts.items,
    executions: executions.items,
    broker,
    messages: messages.items,
    deliveries: deliveries.items,
    background,
    relationships: relationships.items.map(normalizeRelationship),
  };
}

export async function listOperationsAdvisories(): Promise<
  OperationsAdvisory[]
> {
  return collection(
    await request<{ items: OperationsAdvisory[] }>("/observability/advisories"),
  );
}
