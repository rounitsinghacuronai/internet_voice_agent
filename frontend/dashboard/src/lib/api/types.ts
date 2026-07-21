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
  // enrichment (optional, may be filled by mock repo)
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

export interface DashboardStats {
  generated_at: string;
  kpis: {
    todays_calls: number;
    active_calls: number;
    resolved_tickets: number;
    open_tickets: number;
    critical_tickets: number;
    transferred_calls: number;
    avg_response_time_s: number;
    avg_resolution_time_min: number;
    customer_satisfaction: number;
    ai_resolution_rate: number;
    human_escalation_rate: number;
    avg_call_duration_s: number;
    total_customers: number;
    open_complaints: number;
  };
  language_distribution: { language: string; value: number }[];
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
  metrics: { cpu_percent: number; memory_percent: number; api_errors_24h: number; streaming: string };
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
  executive: string;
  transferred_at: string;
  handoff_seconds: number;
  summary: string;
  resolution: string | null;
}

export interface Conversation {
  call_id: string;
  customer_name: string;
  phone: string;
  language: string;
  started_at: string;
  duration_s: number;
  intent: string;
  sentiment: Sentiment;
  escalated: boolean;
  summary: string;
  tokens: number;
  turns: number;
}

export interface AppNotification {
  id: string;
  type: "critical" | "warning" | "info";
  title: string;
  body: string;
  created_at: string;
  read: boolean;
}
