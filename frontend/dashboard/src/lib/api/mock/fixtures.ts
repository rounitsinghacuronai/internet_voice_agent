import type {
  AppNotification,
  Conversation,
  Customer,
  DashboardStats,
  Escalation,
  Executive,
  LiveCall,
  SystemHealth,
  Ticket,
} from "../types";

const now = Date.now();
const ago = (min: number) => new Date(now - min * 60_000).toISOString();
const ahead = (min: number) => new Date(now + min * 60_000).toISOString();

export const mockCustomers: Customer[] = [
  { account_no: "300012345678", name: "Ramesh Patil", mobile: "9820012345", address: "Kothrud, Pune", service_type: "fiber", plan_name: "Fiber 100 Mbps Unlimited + Landline", plan_price: 799, ont_status: "OK", payment_status: "PAID" },
  { account_no: "300023456789", name: "Sunita Deshmukh", mobile: "9822233445", address: "Hadapsar, Pune", service_type: "postpaid", plan_name: "Postpaid 599 — 75GB + Unlimited Calls", plan_price: 599, ont_status: "", payment_status: "DUE" },
  { account_no: "210034567890", name: "Abdul Sheikh", mobile: "9867554433", address: "Bhiwandi, Thane", service_type: "fiber", plan_name: "Fiber 300 Mbps Unlimited", plan_price: 1499, ont_status: "LOS", payment_status: "PAID" },
  { account_no: "330045678901", name: "Kavita Jadhav", mobile: "9700112233", address: "Nanded City", service_type: "prepaid", plan_name: "Prepaid 299 — 2GB/day, 28 days", plan_price: 299, ont_status: "", payment_status: "ACTIVE" },
  { account_no: "410056789012", name: "Suresh Wagh", mobile: "9922334455", address: "CIDCO, Chh. Sambhajinagar", service_type: "enterprise", plan_name: "Enterprise Leased Line 200 Mbps 1:1", plan_price: 8999, ont_status: "OK", payment_status: "PAID" },
  { account_no: "880012340001", name: "Kiran Darkunde", mobile: "8624900039", address: "Wakad, Pune", service_type: "fiber", plan_name: "Fiber 100 Mbps Unlimited + Landline", plan_price: 799, ont_status: "OK", payment_status: "PAID" },
  { account_no: "880012340002", name: "Rounit Singh", mobile: "7267850755", address: "Baner, Pune", service_type: "fiber", plan_name: "Fiber 300 Mbps Unlimited", plan_price: 1499, ont_status: "OK", payment_status: "PAID" },
];

const executives = ["Priya Nair", "Amit Kulkarni", "Rahul Verma", "Sneha Rao", "Vikram Joshi"];

export const mockTickets: Ticket[] = [
  { ticket_id: "TT-2026-8AF8", call_id: "20531c327e92", complaint_no: "TC260733D895", event_type: "complaint_registered", category: "Broadband - No Internet", priority: "HIGH", customer_name: "Rounit Singh", mobile: "7267850755", account_no: "880012340002", service_type: "fiber", location: "Baner, Pune", summary: "No internet for last few hours. Account verified. ONT/line status checked.", status: "SENT", attempts: 1, follow_up_count: 0, created_at: ago(38), delivered_at: ago(37), last_error: "", assigned_executive: "Priya Nair", region: "Pune", raised_by: "AI", sla_due_at: ahead(180), language: "Hindi", resolution_notes: "" },
  { ticket_id: "TT-2026-BC8A", call_id: "440162be5d2c", complaint_no: "", event_type: "human_escalation", category: "Escalation - Account verification", priority: "HIGH", customer_name: "Abdul Sheikh", mobile: "9867554433", account_no: "210034567890", service_type: "fiber", location: "Bhiwandi, Thane", summary: "Customer unable to provide account details for broadband troubleshooting.", status: "OPEN", attempts: 0, follow_up_count: 1, created_at: ago(72), delivered_at: null, last_error: "", assigned_executive: "Amit Kulkarni", region: "Thane", raised_by: "AI", sla_due_at: ahead(90), language: "Hindi", resolution_notes: "" },
  { ticket_id: "TT-2026-C41D", call_id: "7be21aa0f1", complaint_no: "TC2607FF01", event_type: "priority_incident", category: "Fire Emergency", priority: "CRITICAL", customer_name: "Suresh Wagh", mobile: "9922334455", account_no: "410056789012", service_type: "enterprise", location: "CIDCO, Chh. Sambhajinagar", summary: "Sparking from overhead fiber pole reported near premises. Field team dispatched.", status: "OPEN", attempts: 2, follow_up_count: 0, created_at: ago(14), delivered_at: ago(13), last_error: "", assigned_executive: "Vikram Joshi", region: "Sambhajinagar", raised_by: "AI", sla_due_at: ahead(20), language: "Marathi", resolution_notes: "" },
  { ticket_id: "TT-2026-D93E", call_id: "9ac1220bd0", complaint_no: "TC2607AA22", event_type: "complaint_registered", category: "Billing - Overcharge", priority: "MEDIUM", customer_name: "Sunita Deshmukh", mobile: "9822233445", account_no: "300023456789", service_type: "postpaid", location: "Hadapsar, Pune", summary: "Disputed data add-on charge on latest postpaid bill.", status: "PENDING", attempts: 1, follow_up_count: 0, created_at: ago(210), delivered_at: ago(208), last_error: "", assigned_executive: "Sneha Rao", region: "Pune", raised_by: "AI", sla_due_at: ahead(600), language: "English", resolution_notes: "" },
  { ticket_id: "TT-2026-E12F", call_id: "b1c0e93aa5", complaint_no: "", event_type: "sim_blocked", category: "SIM Replacement", priority: "HIGH", customer_name: "Kavita Jadhav", mobile: "9700112233", account_no: "330045678901", service_type: "prepaid", location: "Nanded City", summary: "Lost SIM blocked at customer request; replacement flow initiated.", status: "RESOLVED", attempts: 1, follow_up_count: 0, created_at: ago(320), delivered_at: ago(318), last_error: "", assigned_executive: "Rahul Verma", region: "Nanded", raised_by: "AI", sla_due_at: ago(20), language: "Marathi", resolution_notes: "SIM swap booked, courier scheduled." },
  { ticket_id: "TT-2026-F7B0", call_id: "c9de44a1b2", complaint_no: "TC2607BC90", event_type: "complaint_registered", category: "Network Issue - Slow Internet", priority: "LOW", customer_name: "Ramesh Patil", mobile: "9820012345", account_no: "300012345678", service_type: "fiber", location: "Kothrud, Pune", summary: "Wi-Fi slow on 2.4GHz in evenings; channel congestion suspected.", status: "PENDING", attempts: 1, follow_up_count: 0, created_at: ago(150), delivered_at: ago(149), last_error: "", assigned_executive: "Priya Nair", region: "Pune", raised_by: "AI", sla_due_at: ahead(720), language: "English", resolution_notes: "" },
  { ticket_id: "TT-2026-A0C3", call_id: "d0ff21bc7e", complaint_no: "", event_type: "new_connection", category: "New Connection", priority: "MEDIUM", customer_name: "Kiran Darkunde", mobile: "8624900039", account_no: "880012340001", service_type: "fiber", location: "Wakad, Pune", summary: "New fiber connection enquiry, feasibility check requested.", status: "OPEN", attempts: 0, follow_up_count: 0, created_at: ago(52), delivered_at: null, last_error: "", assigned_executive: "Amit Kulkarni", region: "Pune", raised_by: "HUMAN", sla_due_at: ahead(1440), language: "Hindi", resolution_notes: "" },
];

export const mockLiveCalls: LiveCall[] = [
  { call_id: "live-88a1", customer_name: "Rohit Sharma", phone: "98•••• 4412", language: "Hindi", intent: "Broadband - No Internet", ai_response: "मैं आपके कनेक्शन की जांच कर रहा हूँ, कृपया एक क्षण रुकें।", current_tool: "get_broadband_status", duration_s: 74, sentiment: "neutral", stage: "calling_tool" },
  { call_id: "live-4d20", customer_name: "Meera Kulkarni", phone: "98•••• 9931", language: "Marathi", intent: "Billing - Payment", ai_response: "तुमचं बिल ₹941 आहे, मी पेमेंट लिंक पाठवतो.", current_tool: null, duration_s: 128, sentiment: "positive", stage: "speaking" },
  { call_id: "live-1f77", customer_name: "Unknown Caller", phone: "70•••• 0755", language: "Hindi", intent: "Fiber Damage", ai_response: "…", current_tool: null, duration_s: 41, sentiment: "negative", stage: "escalating" },
];

export const mockStats: DashboardStats = {
  generated_at: new Date().toISOString(),
  kpis: {
    todays_calls: 342, active_calls: 3, resolved_tickets: 128, open_tickets: 24, critical_tickets: 4,
    transferred_calls: 41, avg_response_time_s: 2.1, avg_resolution_time_min: 7.4, customer_satisfaction: 4.4,
    ai_resolution_rate: 78.0, human_escalation_rate: 12.0, avg_call_duration_s: 168, total_customers: 7, open_complaints: 3,
  },
  language_distribution: [
    { language: "Hindi", value: 46 }, { language: "Marathi", value: 31 },
    { language: "English", value: 18 }, { language: "Other", value: 5 },
  ],
  common_issues: [
    { issue: "Broadband - No Internet", count: 96 }, { issue: "Billing", count: 61 },
    { issue: "Slow Internet", count: 44 }, { issue: "SIM Replacement", count: 22 },
    { issue: "New Connection", count: 18 }, { issue: "Fiber Damage", count: 9 },
  ],
  recent_complaints: [
    { ticket_no: "TC260733D895", account_no: "880012340002", category: "Broadband - No Internet", description: "इंटरनेट नहीं आ रहा है", status: "REGISTERED", created_at: ago(38) },
    { ticket_no: "TC26075FF88B", account_no: "210034567890", category: "Broadband - No Internet", description: "No power at home, no internet.", status: "REGISTERED", created_at: ago(120) },
    { ticket_no: "TC2607D10BF2", account_no: "210034567890", category: "Broadband - No Internet", description: "WiFi not working, fiber main line issue.", status: "REGISTERED", created_at: ago(240) },
  ],
};

export const mockHealth: SystemHealth = {
  generated_at: new Date().toISOString(),
  uptime_seconds: 92_340,
  components: [
    { name: "Backend API", status: "operational", latency_ms: 12 },
    { name: "WebSocket Voice", status: "operational", latency_ms: 20 },
    { name: "Gemini LLM", status: "operational", detail: "gemini-2.0-flash" },
    { name: "Sarvam STT", status: "operational", latency_ms: 180 },
    { name: "Sarvam TTS", status: "operational", latency_ms: 210 },
    { name: "Exotel Telephony", status: "operational" },
    { name: "Knowledge Base", status: "operational", detail: "412 chunks" },
    { name: "Database", status: "operational" },
  ],
  metrics: { cpu_percent: 18, memory_percent: 41, api_errors_24h: 0, streaming: "healthy" },
};

export const mockExecutives: Executive[] = executives.map((name, i) => ({
  id: `exec-${i + 1}`,
  name,
  status: (["available", "busy", "available", "offline", "busy"] as const)[i],
  calls_today: [22, 31, 18, 0, 27][i],
  avg_resolution_min: [6.2, 8.1, 5.4, 0, 7.7][i],
  current_call: [null, "TT-2026-8AF8", null, null, "TT-2026-C41D"][i],
  transfers_handled: [9, 14, 6, 0, 11][i],
  satisfaction: [4.6, 4.2, 4.7, 0, 4.3][i],
}));

export const mockEscalations: Escalation[] = [
  { id: "esc-1", ticket_id: "TT-2026-BC8A", reason: "Account verification failed", category: "Verification", executive: "Amit Kulkarni", transferred_at: ago(70), handoff_seconds: 22, summary: "Customer could not provide account details; needs manual KYC.", resolution: null },
  { id: "esc-2", ticket_id: "TT-2026-C41D", reason: "Safety / fire emergency", category: "Emergency", executive: "Vikram Joshi", transferred_at: ago(14), handoff_seconds: 9, summary: "Sparking pole reported; dispatched field safety team.", resolution: null },
  { id: "esc-3", ticket_id: "TT-2026-E12F", reason: "Fraud suspicion", category: "Fraud", executive: "Rahul Verma", transferred_at: ago(320), handoff_seconds: 18, summary: "Lost SIM, possible misuse; blocked and swapped.", resolution: "Resolved — SIM blocked, replacement dispatched." },
];

export const mockConversations: Conversation[] = mockTickets.map((t, i) => ({
  call_id: t.call_id,
  customer_name: t.customer_name || "Unknown",
  phone: t.mobile || "—",
  language: t.language || "Hindi",
  started_at: t.created_at,
  duration_s: [168, 240, 96, 312, 144, 210, 88][i] ?? 160,
  intent: t.category,
  sentiment: (["neutral", "negative", "negative", "neutral", "positive", "neutral", "positive"] as const)[i] ?? "neutral",
  escalated: t.event_type.includes("escalation") || t.event_type.includes("priority"),
  summary: t.summary,
  tokens: [1840, 2210, 990, 3120, 1420, 2010, 760][i] ?? 1500,
  turns: [8, 11, 5, 14, 7, 9, 4][i] ?? 8,
}));

export const mockNotifications: AppNotification[] = [
  { id: "n1", type: "critical", title: "Fire emergency reported", body: "Sparking fiber pole — CIDCO, Sambhajinagar. Field team dispatched.", created_at: ago(14), read: false },
  { id: "n2", type: "critical", title: "Broadband outage — Bhiwandi", body: "Area fiber cut affecting 40+ customers. ETA 2h.", created_at: ago(48), read: false },
  { id: "n3", type: "warning", title: "Ticket escalated to human", body: "TT-2026-BC8A handed to Amit Kulkarni.", created_at: ago(70), read: false },
  { id: "n4", type: "info", title: "VIP customer on call", body: "Enterprise leased-line customer connected.", created_at: ago(120), read: true },
];
