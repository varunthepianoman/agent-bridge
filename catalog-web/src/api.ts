import type {
  ActionResponse,
  AttentionItem,
  BridgeMessage,
  BridgeRoom,
  CatalogNode,
  CatalogSettings,
  CoreConversation,
  MailboxSnapshot,
  NatsEvent,
  OperationResponse,
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
    const value = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(value?.detail ?? `Request failed (${response.status})`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function coreConversations(
  query = "",
): Promise<{ items: CoreConversation[]; total: number }> {
  const params = new URLSearchParams();
  if (query.trim()) params.set("q", query.trim());
  return request(`/conversations?${params}`);
}

export function conversationCandidates(): Promise<{
  items: CoreConversation[];
  total: number;
}> {
  return request("/conversations/candidates");
}

export function importConversations(conversationIds: string[]): Promise<unknown> {
  return request("/conversations/import", {
    method: "POST",
    body: JSON.stringify({ conversation_ids: conversationIds }),
  });
}

export function coreConversation(id: string): Promise<CoreConversation> {
  return request(`/conversations/${encodeURIComponent(id)}`);
}

export function openCoreConversation(
  id: string,
  target: "desktop" | "terminal",
): Promise<ActionResponse> {
  return request(`/conversations/${encodeURIComponent(id)}/open?target=${target}`, {
    method: "POST",
  });
}

export function catalogSettings(): Promise<CatalogSettings> {
  return request("/settings");
}

export function updateCatalogSettings(input: CatalogSettings): Promise<CatalogSettings> {
  return request("/settings", { method: "PATCH", body: JSON.stringify(input) });
}

export function sendCoreMessage(input: {
  body: string;
  target_conversation_id?: string;
  room_id?: string;
}): Promise<BridgeMessage> {
  return request("/messages", { method: "POST", body: JSON.stringify(input) });
}

export function sendProviderTurn(
  conversationId: string,
  input: { prompt: string; effort?: "low" | "medium" | "high" | "xhigh" | "max" | "ultra" },
): Promise<OperationResponse> {
  return request(`/conversations/${encodeURIComponent(conversationId)}/turns`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function refreshConversation(
  conversationId: string,
  waitSeconds = 30,
): Promise<OperationResponse> {
  return request(
    `/conversations/${encodeURIComponent(conversationId)}/refresh?wait_seconds=${waitSeconds}`,
    { method: "POST" },
  );
}

export function mailbox(
  conversationId: string,
  state?: BridgeMessage["processing_state"],
): Promise<MailboxSnapshot> {
  const params = new URLSearchParams();
  if (state) params.set("state", state);
  const query = params.size ? `?${params}` : "";
  return request(`/mailbox/${encodeURIComponent(conversationId)}${query}`);
}

export function stopMailboxListener(conversationId: string): Promise<OperationResponse> {
  return request(`/mailbox/${encodeURIComponent(conversationId)}/stop-listener`, {
    method: "POST",
  });
}

export function completeMailboxMessage(
  conversationId: string,
  messageId: string,
  input: {
    outcome: "succeeded" | "blocked" | "failed";
    detail?: string;
    reply_body?: string;
  },
): Promise<BridgeMessage> {
  return request(`/messages/${encodeURIComponent(messageId)}/complete`, {
    method: "POST",
    body: JSON.stringify({ conversation_id: conversationId, ...input }),
  });
}

export function requeueMailboxMessage(
  conversationId: string,
  messageId: string,
): Promise<BridgeMessage> {
  return request(`/messages/${encodeURIComponent(messageId)}/requeue`, {
    method: "POST",
    body: JSON.stringify({ conversation_id: conversationId }),
  });
}

export function coreMessages(): Promise<{ items: BridgeMessage[]; total: number }> {
  return request("/messages");
}

export function attentionItems(): Promise<{ items: AttentionItem[]; total: number }> {
  return request("/attention");
}

export function acknowledgeAttention(id: string): Promise<unknown> {
  return request(`/attention/${encodeURIComponent(id)}/acknowledge`, { method: "POST" });
}

export function rooms(): Promise<{ items: BridgeRoom[]; total: number }> {
  return request("/rooms");
}

export function createRoom(name: string): Promise<BridgeRoom> {
  return request("/rooms", { method: "POST", body: JSON.stringify({ name }) });
}

export async function listNodes(): Promise<CatalogNode[]> {
  const response = await request<{ items: CatalogNode[] }>("/nodes");
  return response.items;
}

export function natsSummary(): Promise<Record<string, unknown>> {
  return request("/nats/summary");
}

export function natsActivity(): Promise<{ items: NatsEvent[]; total: number }> {
  return request("/nats/activity");
}

export function reconcileCore(): Promise<{ discovered: number; imported: number }> {
  return request("/reconciliation", { method: "POST" });
}
