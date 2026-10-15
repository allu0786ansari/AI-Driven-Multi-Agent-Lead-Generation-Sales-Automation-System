// Shapes returned by the backend (backend/app/api/*.py).

export type LeadStatus = "new" | "hot" | "warm" | "cold" | "contacted" | "converted" | "lost";
export type Channel = "whatsapp" | "instagram" | "messenger";
export type View = "needs_you" | "open" | "closed" | "all";

export interface LeadSummary {
  lead_id: string;
  name: string | null;
  contact_phone: string | null;
  source: string | null;
  product_interest: string | null;
  status: LeadStatus;
  qualification_score: number | null;
  conversation_summary: string | null;
  preferred_channel: Channel | null;
  assigned_to: string | null;
  whatsapp_opt_in: boolean;
  opted_out: boolean;
  is_human_takeover: boolean;
  messaging_cost_estimate: string;
  created_at: string;
  last_activity_at: string;
  last_message_text: string | null;
  last_message_at: string | null;
  last_message_direction: "inbound" | "outbound" | null;
  last_message_sender: string | null;
  last_message_channel: Channel | null;
  unread_alerts: number;
}

export interface Message {
  message_id: string;
  channel: Channel;
  direction: "inbound" | "outbound";
  sender: "lead" | "agent" | "salesperson" | "system";
  text: string | null;
  media_type: string;
  template_name: string | null;
  pricing_category: string | null;
  delivery_status: string;
  estimated_cost: string;
  error: string | null;
  timestamp: string;
  kind: string | null;
}

export interface Activity {
  activity_id: string;
  actor: string;
  kind: "status" | "assign" | "takeover" | "release" | "message";
  detail: Record<string, string | null>;
  created_at: string;
}

export interface ReplyOption {
  channel: Channel;
  free_text: boolean;
  templates: boolean;
  open_until: string | null;
  reason: string;
}

export interface QualifySlots {
  product?: string | null;
  category?: string | null;
  quantity?: number | null;
  budget?: string | null;
  timeline?: "now" | "this_week" | "this_month" | "later" | null;
  city?: string | null;
  purchase_intent?: "high" | "medium" | "low" | null;
  sentiment?: string | null;
  wants_human?: boolean;
}

export interface LeadDetail extends LeadSummary {
  contact_email: string | null;
  source_detail: string | null;
  meta_lead_id: string | null;
  ad_id: string | null;
  campaign_id: string | null;
  ctwa_clid: string | null;
  language: string | null;
  opt_in_source: string | null;
  opt_in_at: string | null;
  opted_out_at: string | null;
  service_window_expires_at: string | null;
  free_entry_window_expires_at: string | null;
  instagram_scoped_id: string | null;
  messenger_scoped_id: string | null;
  facebook_user_id: string | null;
  qualify_slots: QualifySlots;
  qualify_state: string | null;
  score_rationale: string | null;
  messages: Message[];
  activity: Activity[];
  reply_options: ReplyOption[];
  default_channel: Channel | null;
}

export type Counts = Record<View | LeadStatus | "takeover", number>;

export interface Notification {
  notification_id: string;
  lead_id: string | null;
  kind: "hot_lead" | "handoff" | "customer_replied";
  title: string;
  body: string;
  status: string;
  channels_sent: string[];
  error: string | null;
  created_at: string;
  sent_at: string | null;
  read_at: string | null;
}

export interface Product {
  product_id: string;
  sku: string;
  name: string;
  category: string;
  price: string;
  currency: string;
  description: string;
  key_attributes: Record<string, unknown>;
  stock_status: "in_stock" | "low_stock" | "out_of_stock" | "preorder";
  source_url: string | null;
  last_updated: string;
}

export interface Template {
  name: string;
  language: string;
  category: string;
  status: string;
  body: string | null;
  param_count: number;
}

export interface SendResult {
  decision: "queued" | "needs_template" | "blocked";
  reason: string;
  message: Message | null;
}

export type Slot = "product" | "quantity" | "timeline" | "city" | "budget";

export interface QualificationConfig {
  questions: { slot: Slot; question: string }[];
  weights: Record<string, number>;
  bulk_threshold: number;
  hot_threshold: number;
  warm_threshold: number;
}

export interface InboundEvent {
  id: string;
  platform_event_id: string;
  endpoint: string;
  event_type: string;
  status: string;
  attempts: number;
  error: string | null;
  result_note: string | null;
  trace_id: string;
  received_at: string;
  processed_at: string | null;
}
