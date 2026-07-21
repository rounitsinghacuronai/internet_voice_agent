"use client";

import { useQuery } from "@tanstack/react-query";
import { repositories } from "@/lib/api";
import type { TicketQuery } from "@/lib/api/repositories";
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

export function useExecutives() {
  return useQuery({ queryKey: ["executives"], queryFn: () => repositories.executives.list() });
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
