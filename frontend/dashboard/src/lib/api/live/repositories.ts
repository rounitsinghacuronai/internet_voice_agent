/**
 * Live repositories — the ONLY data source. Every method calls the real
 * FastAPI backend; there is no mock/sample data anywhere. Domains with no data
 * yet return empty arrays, and the UI shows honest empty states.
 */
import { apiGet } from "../http";
import type { Repositories, TicketQuery } from "../repositories";
import type {
  AppNotification,
  Conversation,
  Customer,
  CustomerProfile,
  DashboardStats,
  Escalation,
  Executive,
  LiveCall,
  SystemHealth,
  Ticket,
} from "../types";

function applyTicketFilters(list: Ticket[], query?: TicketQuery): Ticket[] {
  let out = list;
  if (query?.status && query.status !== "all") out = out.filter((t) => t.status === query.status);
  if (query?.priority && query.priority !== "all") out = out.filter((t) => t.priority === query.priority);
  if (query?.category && query.category !== "all") out = out.filter((t) => (t.category || "").startsWith(query.category!));
  return out;
}

export const liveRepositories: Repositories = {
  dashboard: {
    getStats: () => apiGet<DashboardStats>("/api/dashboard/stats"),
  },

  tickets: {
    async list(query?: TicketQuery) {
      const { tickets } = await apiGet<{ tickets: Ticket[] }>("/tickets", {
        q: query?.q,
        limit: query?.limit ?? 200,
      });
      return applyTicketFilters(tickets, query);
    },
    async get(id: string) {
      try {
        const { ticket } = await apiGet<{ ticket: Ticket }>(`/api/tickets/${id}`);
        return ticket;
      } catch {
        return null;
      }
    },
  },

  customers: {
    async list(q?: string) {
      const { customers } = await apiGet<{ customers: Customer[] }>("/api/customers", { q, limit: 200 });
      return customers;
    },
    getProfile: (accountNo: string) => apiGet<CustomerProfile>(`/api/customers/${accountNo}`),
  },

  liveCalls: {
    async list() {
      const { calls } = await apiGet<{ calls: LiveCall[] }>("/api/live-calls");
      return calls;
    },
  },

  system: {
    health: () => apiGet<SystemHealth>("/api/system/health"),
  },

  executives: {
    async list() {
      const { executives } = await apiGet<{ executives: Executive[] }>("/api/executives");
      return executives;
    },
  },

  escalations: {
    async list() {
      const { escalations } = await apiGet<{ escalations: Escalation[] }>("/api/escalations");
      return escalations;
    },
  },

  conversations: {
    async list(q?: string) {
      const { conversations } = await apiGet<{ conversations: Conversation[] }>("/api/conversations", { q });
      return conversations;
    },
  },

  notifications: {
    async list() {
      const { notifications } = await apiGet<{ notifications: AppNotification[] }>("/api/notifications");
      return notifications;
    },
  },
};
