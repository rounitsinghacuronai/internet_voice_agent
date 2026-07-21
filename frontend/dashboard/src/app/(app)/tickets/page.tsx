"use client";
import { useMemo, useState, useEffect } from "react";
import Link from "next/link";
import { Download, Search, SlidersHorizontal } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/shared/page-header";
import { PriorityBadge, StatusBadge } from "@/components/shared/badges";
import { useTickets } from "@/lib/hooks";
import { formatRelativeTime } from "@/lib/utils";
import type { Ticket } from "@/lib/api/types";

const STATUSES = ["all", "OPEN", "PENDING", "SENT", "RESOLVED", "FAILED"];
const PRIORITIES = ["all", "CRITICAL", "HIGH", "MEDIUM", "LOW"];
const CATEGORIES = ["all", "Network Issue", "Broadband", "Billing", "SIM Replacement", "New Connection", "Fire Emergency", "Escalation"];

function SlaCell({ ticket }: { ticket: Ticket }) {
  if (["RESOLVED", "CLOSED", "SENT"].includes((ticket.status || "").toUpperCase()))
    return <span className="text-xs text-muted-foreground">—</span>;
  if (!ticket.sla_due_at) return <span className="text-xs text-muted-foreground">—</span>;
  const mins = Math.round((new Date(ticket.sla_due_at).getTime() - Date.now()) / 60000);
  const breached = mins < 0;
  const urgent = mins >= 0 && mins < 30;
  return (
    <Badge variant={breached ? "destructive" : urgent ? "warning" : "secondary"} className="font-mono text-[11px]">
      {breached ? `${Math.abs(mins)}m over` : `${mins}m left`}
    </Badge>
  );
}

function exportCsv(rows: Ticket[]) {
  const cols = ["ticket_id", "category", "priority", "status", "customer_name", "mobile", "region", "assigned_executive", "created_at"];
  const header = cols.join(",");
  const body = rows
    .map((r) => cols.map((c) => `"${String((r as unknown as Record<string, unknown>)[c] ?? "").replace(/"/g, '""')}"`).join(","))
    .join("\n");
  const blob = new Blob([`${header}\n${body}`], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `tickets-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

export default function TicketsPage() {
  const [rawSearch, setRawSearch] = useState("");
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("all");
  const [priority, setPriority] = useState("all");
  const [category, setCategory] = useState("all");

  // debounced search
  useEffect(() => {
    const t = setTimeout(() => setQ(rawSearch), 300);
    return () => clearTimeout(t);
  }, [rawSearch]);

  const { data: tickets, isLoading } = useTickets({ q, status, priority, category });
  const rows = tickets ?? [];

  const counts = useMemo(() => {
    const open = rows.filter((t) => !["SENT", "RESOLVED", "CLOSED"].includes((t.status || "").toUpperCase())).length;
    const crit = rows.filter((t) => ["CRITICAL", "HIGH"].includes((t.priority || "").toUpperCase())).length;
    return { total: rows.length, open, crit };
  }, [rows]);

  return (
    <>
      <PageHeader
        title="Ticket Management"
        description="Every complaint and escalation raised by the AI agent or a human executive."
        actions={
          <Button variant="outline" onClick={() => exportCsv(rows)} className="gap-2">
            <Download className="h-4 w-4" /> Export CSV
          </Button>
        }
      />

      <div className="mb-4 flex flex-wrap gap-2 text-sm">
        <Badge variant="secondary" className="px-3 py-1">Total: {counts.total}</Badge>
        <Badge variant="warning" className="px-3 py-1">Open: {counts.open}</Badge>
        <Badge variant="destructive" className="px-3 py-1">High/Critical: {counts.crit}</Badge>
      </div>

      <Card className="p-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
          <div className="relative flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={rawSearch}
              onChange={(e) => setRawSearch(e.target.value)}
              placeholder="Search ticket, customer, phone, summary…"
              className="pl-9"
            />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <SlidersHorizontal className="hidden h-4 w-4 text-muted-foreground sm:block" />
            <Select value={status} onValueChange={setStatus}>
              <SelectTrigger className="w-[130px]"><SelectValue placeholder="Status" /></SelectTrigger>
              <SelectContent>{STATUSES.map((s) => <SelectItem key={s} value={s}>{s === "all" ? "All statuses" : s}</SelectItem>)}</SelectContent>
            </Select>
            <Select value={priority} onValueChange={setPriority}>
              <SelectTrigger className="w-[130px]"><SelectValue placeholder="Priority" /></SelectTrigger>
              <SelectContent>{PRIORITIES.map((s) => <SelectItem key={s} value={s}>{s === "all" ? "All priority" : s}</SelectItem>)}</SelectContent>
            </Select>
            <Select value={category} onValueChange={setCategory}>
              <SelectTrigger className="w-[150px]"><SelectValue placeholder="Category" /></SelectTrigger>
              <SelectContent>{CATEGORIES.map((s) => <SelectItem key={s} value={s}>{s === "all" ? "All categories" : s}</SelectItem>)}</SelectContent>
            </Select>
          </div>
        </div>

        <div className="mt-4 overflow-hidden rounded-lg border border-border">
          <Table>
            <TableHeader>
              <TableRow className="bg-muted/40 hover:bg-muted/40">
                <TableHead>Ticket</TableHead>
                <TableHead>Category</TableHead>
                <TableHead>Priority</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Customer</TableHead>
                <TableHead>Contact</TableHead>
                <TableHead>Account</TableHead>
                <TableHead>Location</TableHead>
                <TableHead>Created</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {isLoading
                ? Array.from({ length: 6 }).map((_, i) => (
                    <TableRow key={i}>
                      {Array.from({ length: 9 }).map((__, j) => (
                        <TableCell key={j}><Skeleton className="h-5 w-full" /></TableCell>
                      ))}
                    </TableRow>
                  ))
                : rows.map((t) => (
                    <TableRow key={t.ticket_id} className="cursor-pointer">
                      <TableCell>
                        <Link href={`/tickets/${t.ticket_id}`} className="font-mono text-xs font-semibold text-primary hover:underline">
                          {t.ticket_id}
                        </Link>
                      </TableCell>
                      <TableCell className="max-w-[220px] truncate text-sm">{t.category}</TableCell>
                      <TableCell><PriorityBadge priority={t.priority} /></TableCell>
                      <TableCell><StatusBadge status={t.status} /></TableCell>
                      <TableCell className="text-sm">{t.customer_name || "—"}</TableCell>
                      <TableCell className="text-sm text-muted-foreground">{t.mobile || "—"}</TableCell>
                      <TableCell className="font-mono text-xs text-muted-foreground">{t.account_no || "—"}</TableCell>
                      <TableCell className="max-w-[160px] truncate text-sm text-muted-foreground">{t.location || "—"}</TableCell>
                      <TableCell className="whitespace-nowrap text-xs text-muted-foreground">{formatRelativeTime(t.created_at)}</TableCell>
                    </TableRow>
                  ))}
              {!isLoading && rows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={9} className="py-10 text-center text-sm text-muted-foreground">
                    No tickets match your filters.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      </Card>
    </>
  );
}
