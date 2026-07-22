"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { repositories } from "@/lib/api";
import type { TicketQuery } from "@/lib/api/repositories";
import type { AdminSettings, ExecutiveRecord } from "@/lib/api/types";
import { config } from "@/lib/config";

export function useDashboardStats() {
  return useQuery({
    queryKey: ["dashboard-stats"],
    queryFn: () => repositories.dashboard.getStats(),
    refetchInterval: config.polling.dashboard,
  });
}

export function useTickets(query?: TicketQuery) {
  return useQuery({
    queryKey: ["tickets", query],
    queryFn: () => repositories.tickets.list(query),
    refetchInterval: config.polling.tickets,
    refetchOnWindowFocus: true,
  });
}

export function useTicket(id: string) {
  return useQuery({
    queryKey: ["ticket", id],
    queryFn: () => repositories.tickets.get(id),
    enabled: !!id,
    refetchInterval: config.polling.tickets,
    refetchOnWindowFocus: true,
  });
}

export function useCustomers(q?: string) {
  return useQuery({
    queryKey: ["customers", q],
    queryFn: () => repositories.customers.list(q),
    refetchInterval: config.polling.customers,
  });
}

export function useCustomerProfile(accountNo: string) {
  return useQuery({
    queryKey: ["customer", accountNo],
    queryFn: () => repositories.customers.getProfile(accountNo),
    enabled: !!accountNo,
  });
}

export function useLiveCalls() {
  return useQuery({
    queryKey: ["live-calls"],
    queryFn: () => repositories.liveCalls.list(),
    refetchInterval: config.polling.liveCalls,
  });
}

export function useSystemHealth() {
  return useQuery({
    queryKey: ["system-health"],
    queryFn: () => repositories.system.health(),
    refetchInterval: config.polling.systemHealth,
  });
}

// ── calls ────────────────────────────────────────────────────────────────────
export function useCalls(q?: string) {
  return useQuery({
    queryKey: ["calls", q],
    queryFn: () => repositories.calls.list(q),
    refetchInterval: config.polling.conversations,
  });
}

export function useCall(id: string) {
  return useQuery({ queryKey: ["call", id], queryFn: () => repositories.calls.get(id), enabled: !!id });
}

// ── ticket actions (mutations) ───────────────────────────────────────────────
export function useTicketActions(id: string) {
  const qc = useQueryClient();
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["ticket", id] });
    qc.invalidateQueries({ queryKey: ["tickets"] });
    qc.invalidateQueries({ queryKey: ["dashboard-stats"] });
  };
  return {
    setStatus: useMutation({ mutationFn: (status: string) => repositories.tickets.setStatus(id, status), onSuccess: invalidate }),
    assign: useMutation({ mutationFn: (executive: string) => repositories.tickets.assign(id, executive), onSuccess: invalidate }),
    addNotes: useMutation({ mutationFn: (notes: string) => repositories.tickets.addNotes(id, notes), onSuccess: invalidate }),
  };
}

// ── executives CRUD ──────────────────────────────────────────────────────────
export function useExecutives() {
  return useQuery({ queryKey: ["executives"], queryFn: () => repositories.executivesAdmin.list() });
}

export function useExecutiveMutations() {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: ["executives"] });
  type Body = Omit<ExecutiveRecord, "id" | "created_at">;
  return {
    create: useMutation({ mutationFn: (e: Body) => repositories.executivesAdmin.create(e), onSuccess: invalidate }),
    update: useMutation({ mutationFn: ({ id, e }: { id: number; e: Body }) => repositories.executivesAdmin.update(id, e), onSuccess: invalidate }),
    remove: useMutation({ mutationFn: (id: number) => repositories.executivesAdmin.remove(id), onSuccess: invalidate }),
  };
}

// ── settings ─────────────────────────────────────────────────────────────────
export function useSettings() {
  return useQuery({ queryKey: ["settings"], queryFn: () => repositories.settings.get() });
}

export function useSaveSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: Partial<AdminSettings>) => repositories.settings.save(patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }),
  });
}

// ── global search ────────────────────────────────────────────────────────────
export function useSearch(q: string) {
  return useQuery({
    queryKey: ["search", q],
    queryFn: () => repositories.search.query(q),
    enabled: q.trim().length >= 2,
  });
}

// ── knowledge base ───────────────────────────────────────────────────────────
export function useKbSearch(q: string) {
  return useQuery({
    queryKey: ["kb", q],
    queryFn: () => repositories.kb.search(q),
    enabled: q.trim().length >= 2,
  });
}

export function useKbReload() {
  return useMutation({ mutationFn: () => repositories.kb.reload() });
}

export function useEscalations() {
  return useQuery({
    queryKey: ["escalations"],
    queryFn: () => repositories.escalations.list(),
    refetchInterval: config.polling.escalations,
  });
}

export function useConversations(q?: string) {
  return useQuery({
    queryKey: ["conversations", q],
    queryFn: () => repositories.conversations.list(q),
    refetchInterval: config.polling.conversations,
  });
}

export function useNotifications() {
  return useQuery({
    queryKey: ["notifications"],
    queryFn: () => repositories.notifications.list(),
    refetchInterval: config.polling.notifications,
  });
}
