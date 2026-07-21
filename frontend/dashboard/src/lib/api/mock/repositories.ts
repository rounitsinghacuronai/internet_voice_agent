/** Mock repositories — realistic in-memory data, same interface as live. */
import type {
  ConversationRepository,
  CustomerRepository,
  DashboardRepository,
  EscalationRepository,
  ExecutiveRepository,
  LiveCallRepository,
  NotificationRepository,
  Repositories,
  SystemRepository,
  TicketQuery,
  TicketRepository,
} from "../repositories";
import type { CustomerProfile } from "../types";
import {
  mockConversations,
  mockCustomers,
  mockEscalations,
  mockExecutives,
  mockHealth,
  mockLiveCalls,
  mockNotifications,
  mockStats,
  mockTickets,
} from "./fixtures";

const delay = <T>(v: T, ms = 120) => new Promise<T>((r) => setTimeout(() => r(v), ms));

function filterTickets(query?: TicketQuery) {
  let out = [...mockTickets];
  if (query?.q) {
    const q = query.q.toLowerCase();
    out = out.filter((t) => JSON.stringify(t).toLowerCase().includes(q));
  }
  if (query?.status && query.status !== "all") out = out.filter((t) => t.status === query.status);
  if (query?.priority && query.priority !== "all") out = out.filter((t) => t.priority === query.priority);
  if (query?.category && query.category !== "all") out = out.filter((t) => t.category.startsWith(query.category!));
  return out.slice(0, query?.limit ?? 200);
}

export const mockRepositories: Repositories = {
  dashboard: { getStats: () => delay(mockStats) } as DashboardRepository,
  tickets: {
    list: (query) => delay(filterTickets(query)),
    get: (id) => delay(mockTickets.find((t) => t.ticket_id === id) ?? null),
  } as TicketRepository,
  customers: {
    list: (q) =>
      delay(
        q
          ? mockCustomers.filter((c) => JSON.stringify(c).toLowerCase().includes(q.toLowerCase()))
          : mockCustomers,
      ),
    getProfile: (accountNo): Promise<CustomerProfile> => {
      const customer = mockCustomers.find((c) => c.account_no === accountNo) ?? mockCustomers[0];
      return delay({
        customer,
        plan: { plan_name: customer.plan_name, monthly_price_rs: customer.plan_price, service_type: customer.service_type },
        bill: { amount_rs: Math.round(customer.plan_price * 1.18), due_date: "30 Jul 2026", payment_status: customer.payment_status },
        usage: { cycle_data_used_gb: 412, fair_use_limit_tb: 3.3, throttled: false },
        broadband: customer.service_type === "fiber" || customer.service_type === "enterprise"
          ? { ont_status: customer.ont_status || "OK", line_state: customer.ont_status === "LOS" ? "DOWN" : "UP", last_sync_mbps: customer.ont_status === "LOS" ? 0 : 300 }
          : null,
        complaints: mockStats.recent_complaints.filter((x) => x.account_no === accountNo),
        tickets: mockTickets.filter((t) => t.account_no === accountNo),
        verification_status: customer.payment_status ? "VERIFIED" : "UNVERIFIED",
      });
    },
  } as CustomerRepository,
  liveCalls: { list: () => delay(mockLiveCalls, 80) } as LiveCallRepository,
  system: { health: () => delay(mockHealth) } as SystemRepository,
  executives: { list: () => delay(mockExecutives) } as ExecutiveRepository,
  escalations: { list: () => delay(mockEscalations) } as EscalationRepository,
  conversations: {
    list: (q) =>
      delay(q ? mockConversations.filter((c) => JSON.stringify(c).toLowerCase().includes(q.toLowerCase())) : mockConversations),
  } as ConversationRepository,
  notifications: { list: () => delay(mockNotifications) } as NotificationRepository,
};
