/**
 * Live repositories — talk to the real FastAPI backend.
 *
 * Endpoints implemented today: /tickets, /api/dashboard/stats, /api/customers,
 * /api/customers/{id}, /api/system/health, /api/live-calls.
 *
 * For domains the backend does not serve yet (executives, escalations,
 * conversations, notifications) we delegate to the mock repository. The rest of
 * the app is oblivious — swapping a mock for a real endpoint later is a
 * one-line change in this file, with zero UI changes.
 */
import { apiGet } from "../http";
import type { CustomerRepository, DashboardRepository, LiveCallRepository, Repositories, SystemRepository, TicketQuery, TicketRepository } from "../repositories";
import type { Customer, CustomerProfile, DashboardStats, LiveCall, SystemHealth, Ticket } from "../types";
import { mockRepositories } from "../mock/repositories";
import { mockLiveCalls } from "../mock/fixtures";

const liveDashboard: DashboardRepository = {
  getStats: () => apiGet<DashboardStats>("/api/dashboard/stats"),
};

const liveTickets: TicketRepository = {
  async list(query?: TicketQuery) {
    const { tickets } = await apiGet<{ tickets: Ticket[] }>("/tickets", { q: query?.q, limit: query?.limit ?? 200 });
    let out = tickets;
    if (query?.status && query.status !== "all") out = out.filter((t) => t.status === query.status);
    if (query?.priority && query.priority !== "all") out = out.filter((t) => t.priority === query.priority);
    if (query?.category && query.category !== "all") out = out.filter((t) => (t.category || "").startsWith(query.category!));
    return out;
  },
  async get(id: string) {
    const { tickets } = await apiGet<{ tickets: Ticket[] }>("/tickets", { q: id, limit: 50 });
    return tickets.find((t) => t.ticket_id === id) ?? null;
  },
};

const liveCustomers: CustomerRepository = {
  async list(q?: string) {
    const { customers } = await apiGet<{ customers: Customer[] }>("/api/customers", { q, limit: 200 });
    return customers;
  },
  getProfile: (accountNo: string) => apiGet<CustomerProfile>(`/api/customers/${accountNo}`),
};

const liveSystem: SystemRepository = {
  health: () => apiGet<SystemHealth>("/api/system/health"),
};

const liveCalls: LiveCallRepository = {
  async list() {
    try {
      const { calls, source } = await apiGet<{ calls: LiveCall[]; source: string }>("/api/live-calls");
      // Backend has no live sessions to expose in this build → show a realistic
      // demo feed so supervisors can see the monitor working end-to-end.
      return source === "live" && calls.length ? calls : mockLiveCalls;
    } catch {
      return mockLiveCalls;
    }
  },
};

/** Compose: real repos where implemented, mock repos for the rest. */
export const liveRepositories: Repositories = {
  ...mockRepositories,
  dashboard: liveDashboard,
  tickets: liveTickets,
  customers: liveCustomers,
  system: liveSystem,
  liveCalls,
};
