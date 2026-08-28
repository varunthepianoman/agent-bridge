export interface ActionResponse {
  queued?: boolean;
  launched?: boolean;
  command?: string;
  detail?: string;
}

export interface OperationResponse {
  status?: string;
  detail?: string;
  conversation_id?: string;
  message_id?: string;
  command_id?: string;
}

export interface CatalogSettings {
  auto_add_new_chats: boolean;
}

export interface CoreConversation {
  id?: string;
  conversation_id: string;
  conversation_number?: number;
  display_name: string;
  alias: string;
  provider: string;
  provider_title?: string;
  provider_thread_id: string;
  preview?: string;
  transcript_text?: string;
  status: string;
  node_id: string;
  environment_id: string;
  cwd?: string;
  conversation_kind: "full" | "native_subagent";
  delivery_mode: "direct" | "via_parent" | "catalog_only";
  selected: boolean;
  native_url?: string;
  native_launch_enabled: boolean;
  capabilities: {
    can_open: boolean;
    can_receive_turn: boolean;
    can_message: boolean;
  };
}

export interface AttentionItem {
  attention_id: string;
  conversation_id?: string;
  correlation_id?: string;
  category: "update" | "needs_attention";
  kind: string;
  title: string;
  detail: string;
  acknowledged: boolean;
  created_at: string;
}

export interface BridgeMessage {
  message_id: string;
  correlation_id: string;
  source_conversation_id?: string;
  target_conversation_id?: string;
  room_id?: string;
  actor_kind: "human" | "agent";
  operation: string;
  body: string;
  state: string;
  transport_state?: "queued" | "published" | "delivered" | "failed";
  processing_state?:
    | "pending"
    | "received"
    | "claimed"
    | "acknowledged"
    | "succeeded"
    | "blocked"
    | "failed";
  acknowledgement_requested?: boolean;
  processing_detail?: string;
  outcome_detail?: string;
  received_at?: string;
  claimed_at?: string;
  acknowledged_at?: string;
  acknowledgement_detail?: string;
  completed_at?: string;
  outcome_at?: string;
  attempt?: number;
  revision?: number;
  claimed_revision?: number;
  acknowledged_revision?: number;
  terminal_revision?: number;
  reply_message_id?: string;
  subject?: string;
  error?: string;
  created_at: string;
}

export type ReceiptMilestone = "claimed" | "acknowledged" | "terminal";

export interface ReceiptWaitResult {
  status: "reached" | "timeout";
  waited_for: ReceiptMilestone;
  message: BridgeMessage;
  receipt?: BridgeMessage | null;
  recipient_listener?: MailboxListener | null;
  recipient_node_reachable?: boolean;
}

export interface MailboxListener {
  listener_id: string;
  conversation_id: string;
  state?: "waiting" | "stopping" | "offline";
  started_at?: string;
  heartbeat_at?: string;
  expires_at?: string;
  stop_requested?: boolean;
}

export interface MailboxSnapshot {
  items: BridgeMessage[];
  total: number;
  listener?: MailboxListener | null;
}

export interface BridgeRoom {
  room_id: string;
  name: string;
  description: string;
  members: Array<{
    conversation_id: string;
    delivery_mode: "mailbox" | "notify" | "digest";
  }>;
}

export interface CatalogNode {
  node_id: string;
  display_name: string;
  platform?: string;
  reachable: boolean;
  environments: Array<{
    environment_id: string;
    display_name?: string;
    kind?: string;
    available: boolean;
  }>;
}

export interface NatsEvent {
  event_id: string;
  category: string;
  direction?: string;
  severity: string;
  subject?: string;
  message_id?: string;
  correlation_id?: string;
  detail: Record<string, unknown>;
  occurred_at: string;
}
