/**
 * Live repositories — the ONLY data source. Every method calls the real
 * FastAPI backend; there is no mock/sample data anywhere. Domains with no data
 * yet return empty arrays, and the UI shows honest empty states.
 */
import { apiGet, apiSend } from "../http";
import type { Repositories, TicketQuery } from "../repositories";
import type {
  AdminSettings,
  AppNotification,
  CallRecord,
  Conversation,
  Customer,
  CustomerProfile,
  DashboardStats,
  Escalation,
  Executive,
  ExecutiveRecord,
  KbResult,
  LiveCall,
  LoginResponse,
  SearchResult,
  SystemHealth,
  Ticket,
  UserRecord,
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
      const { tickets } = await apiGet<{ tickets: Ticket[] }>("/api/tickets", {
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
    async setStatus(id: string, status: string) {
      const { ticket } = await apiSend<{ ticket: Ticket }>("POST", `/api/tickets/${id}/status`, { status });
      return ticket;
    },
    async assign(id: string, executive: string) {
      const { ticket } = await apiSend<{ ticket: Ticket }>("POST", `/api/tickets/${id}/assign`, { executive });
      return ticket;
    },
    async addNotes(id: string, notes: string) {
      const { ticket } = await apiSend<{ ticket: Ticket }>("POST", `/api/tickets/${id}/notes`, { notes });
      return ticket;
    },
  },

  calls: {
    async list(q?: string) {
      const { calls } = await apiGet<{ calls: CallRecord[] }>("/api/calls", { q });
      return calls;
    },
    async get(id: string) {
      try {
        const { call } = await apiGet<{ call: CallRecord }>(`/api/calls/${id}`);
        return call;
      } catch {
        return null;
      }
    },
  },

  settings: {
    async get() {
      const { settings } = await apiGet<{ settings: AdminSettings }>("/api/settings");
      return settings;
    },
    async save(patch: Partial<AdminSettings>) {
      const { settings } = await apiSend<{ settings: AdminSettings }>("POST", "/api/settings", patch);
      return settings;
    },
  },

  search: {
    async query(q: string) {
      const { results } = await apiGet<{ results: SearchResult[] }>("/api/search", { q });
      return results;
    },
  },

  kb: {
    async search(q: string) {
      const res = await apiGet<KbResult[] | { results?: KbResult[]; chunks?: KbResult[] }>("/kb/search", { q });
      if (Array.isArray(res)) return res;
      return res.results ?? res.chunks ?? [];
    },
    reload: () => apiSend<{ reloaded: boolean; chunks: number }>("POST", "/kb/reload"),
  },

  executivesAdmin: {
    async list() {
      const { executives } = await apiGet<{ executives: ExecutiveRecord[] }>("/api/executives");
      return executives;
    },
    async create(e) {
      const { executive } = await apiSend<{ executive: ExecutiveRecord }>("POST", "/api/executives", e);
      return executive;
    },
    async update(id, e) {
      const { executive } = await apiSend<{ executive: ExecutiveRecord }>("PUT", `/api/executives/${id}`, e);
      return executive;
    },
    async remove(id) {
      await apiSend("DELETE", `/api/executives/${id}`);
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

  auth: {
    login: (username: string, password: string) =>
      apiSend<LoginResponse>("POST", "/api/auth/login", { username, password }),
    async listUsers() {
      const { users } = await apiGet<{ users: UserRecord[] }>("/api/auth/users");
      return users;
    },
    async createUser(u) {
      const { user } = await apiSend<{ user: UserRecord }>("POST", "/api/auth/users", u);
      return user;
    },
    async deleteUser(id: number) {
      await apiSend("DELETE", `/api/auth/users/${id}`);
    },
  },
};
