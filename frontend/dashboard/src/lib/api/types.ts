/** Domain types — shared by both live and mock repositories. */

export type TicketPriority = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";
export type TicketStatus = "OPEN" | "PENDING" | "RETRYING" | "SENT" | "RESOLVED" | "CLOSED" | "FAILED";
export type RaisedBy = "AI" | "HUMAN";

export interface Ticket {
  ticket_id: string;
  call_id: string;
  complaint_no: string;
  event_type: string;
  category: string;
  priority: string;
  customer_name: string;
  mobile: string;
  account_no: string;
  service_type: string;
  location: string;
  summary: string;
  status: string;
  attempts: number;
  follow_up_count: number;
  created_at: string;
  delivered_at: string | null;
  last_error: string;
  // enrichment from the customer join (ticket detail endpoint)
  receiving_number?: string;
  address?: string;
  plan_name?: string;
  plan_price?: number | null;
  payment_status?: string;
  ont_status?: string;
  // optional fields not stored per-ticket in this build
  assigned_executive?: string;
  region?: string;
  raised_by?: RaisedBy;
  sla_due_at?: string;
  resolution_notes?: string;
  language?: string;
}

export interface Customer {
  account_no: string;
  name: string;
  mobile: string;
  address: string;
  service_type: string;
  plan_name: string;
  plan_price: number;
  ont_status: string;
  payment_status: string;
}

export interface CustomerProfile {
  customer: Customer;
  plan: Record<string, unknown>;
  bill: Record<string, unknown>;
  usage: Record<string, unknown>;
  broadband: Record<string, unknown> | null;
  complaints: Complaint[];
  tickets: Ticket[];
  verification_status: string;
}

export interface Complaint {
  ticket_no: string;
  account_no: string;
  category: string;
  description: string;
  status: string;
  created_at: string;
}

export type CallStage = "listening" | "thinking" | "calling_tool" | "speaking" | "escalating";
export type Sentiment = "positive" | "neutral" | "negative";

export interface LiveCall {
  call_id: string;
  customer_name: string;
  phone: string;
  language: string;
  intent: string;
  ai_response: string;
  current_tool: string | null;
  duration_s: number;
  sentiment: Sentiment;
  stage: CallStage;
}

export interface TrendPoint {
  label: string;
  date?: string;
  calls: number;
}

export interface DashboardStats {
  generated_at: string;
  kpis: {
    todays_tickets: number;
    active_calls: number;
    resolved_tickets: number;
    open_tickets: number;
    critical_tickets: number;
    transferred_calls: number;
    avg_response_time_s: number | null;
    avg_resolution_time_min: number | null;
    customer_satisfaction: number | null;
    ai_resolution_rate: number | null;
    human_escalation_rate: number | null;
    avg_call_duration_s: number | null;
    total_customers: number;
    open_complaints: number;
    total_tickets: number;
  };
  trend_7d: TrendPoint[];
  peak_hours: TrendPoint[];
  common_issues: { issue: string; count: number }[];
  recent_complaints: Complaint[];
}

export interface SystemComponent {
  name: string;
  status: "operational" | "degraded" | "down" | "idle";
  latency_ms?: number;
  detail?: string;
}

export interface SystemHealth {
  generated_at: string;
  uptime_seconds: number;
  components: SystemComponent[];
  metrics: { cpu_percent: number | null; memory_percent: number | null; api_errors_24h: number; streaming: string };
}

export interface Executive {
  id: string;
  name: string;
  status: "available" | "busy" | "offline";
  calls_today: number;
  avg_resolution_min: number;
  current_call: string | null;
  transfers_handled: number;
  satisfaction: number;
}

export interface Escalation {
  id: string;
  ticket_id: string;
  reason: string;
  category: string;
  customer_name?: string;
  mobile?: string;
  executive?: string;
  transferred_at: string;
  handoff_seconds?: number;
  summary: string;
  resolution: string | null;
  status?: string;
}

export interface Conversation {
  call_id: string;
  ticket_id?: string;
  customer_name: string;
  phone: string;
  intent: string;
  summary: string;
  escalated: boolean;
  started_at: string;
  complaint_no?: string;
}

export interface CallRecord {
  session_id: string;
  call_sid: string;
  caller: string;
  receiving_number: string;
  language: string;
  customer_name: string;
  account_no: string;
  verified: number;
  intent: string;
  outcome: string;
  escalated: number;
  summary: string;
  turns: number;
  started_at: string;
  ended_at: string | null;
  duration_s: number;
}

export interface SearchResult {
  type: "customer" | "ticket" | "call";
  title: string;
  subtitle: string;
  href: string;
}

export interface AdminSettings {
  company_name: string;
  brand_color: string;
  business_hours: string;
  languages: string[];
  executive_transfer_number: string;
  voice_pace: number;
  prompt_version: string;
  kb_version: string;
  barge_in: boolean;
  dark_mode_default: boolean;
}

export interface ExecutiveRecord {
  id: number;
  name: string;
  phone: string;
  email: string;
  role: string;
  status: "available" | "busy" | "offline";
  created_at?: string;
}

export interface KbResult {
  text?: string;
  score?: number;
  category?: string;
  title?: string;
  [k: string]: unknown;
}

export interface AppNotification {
  id: string;
  type: "critical" | "warning" | "info";
  title: string;
  body: string;
  created_at: string;
  read: boolean;
}
