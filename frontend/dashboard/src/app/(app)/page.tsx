"use client";
import Link from "next/link";
import {
  AlarmClock,
  Bell,
  CheckCircle2,
  Clock,
  Gauge,
  PhoneCall,
  PhoneForwarded,
  Smile,
  Ticket,
  TriangleAlert,
  Users,
} from "lucide-react";
import { KpiCard } from "@/components/dashboard/kpi-card";
import { CallsTrendChart, IssuesBarChart } from "@/components/dashboard/charts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/shared/page-header";
import { useDashboardStats, useNotifications } from "@/lib/hooks";
import { formatRelativeTime, cn } from "@/lib/utils";

const dash = (v: number | null | undefined) => (v === null || v === undefined ? "—" : v);
const secs = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v / 60)}m ${v % 60}s`);

export default function DashboardPage() {
  const { data, isLoading } = useDashboardStats();
  const { data: notifications } = useNotifications();
  const k = data?.kpis;

  return (
    <>
      <PageHeader
        title="Operations Overview"
        description="Live metrics computed directly from your ticket and customer data."
        actions={
          <Badge variant="success" className="gap-1.5 px-3 py-1">
            <span className="h-1.5 w-1.5 rounded-full bg-success animate-pulse-ring" /> Live
          </Badge>
        }
      />

      <div className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-4">
        <KpiCard label="Today's Tickets" value={dash(k?.todays_tickets)} icon={PhoneCall} loading={isLoading} />
        <KpiCard label="Active Calls" value={dash(k?.active_calls)} icon={Gauge} tone="success" hint="live sessions" loading={isLoading} />
        <KpiCard label="Open Tickets" value={dash(k?.open_tickets)} icon={Ticket} tone="warning" loading={isLoading} />
        <KpiCard label="Critical / High" value={dash(k?.critical_tickets)} icon={TriangleAlert} tone="destructive" loading={isLoading} />
        <KpiCard label="Resolved Tickets" value={dash(k?.resolved_tickets)} icon={CheckCircle2} tone="success" loading={isLoading} />
        <KpiCard label="Escalations" value={dash(k?.transferred_calls)} icon={PhoneForwarded} loading={isLoading} />
        <KpiCard label="Total Tickets" value={dash(k?.total_tickets)} icon={Ticket} loading={isLoading} />
        <KpiCard label="Total Customers" value={dash(k?.total_customers)} icon={Users} loading={isLoading} />
        <KpiCard label="Open Complaints" value={dash(k?.open_complaints)} icon={TriangleAlert} tone="warning" loading={isLoading} />
        <KpiCard label="AI Resolution Rate" value={k?.ai_resolution_rate == null ? "—" : `${k.ai_resolution_rate}%`} icon={Gauge} tone="success" loading={isLoading} />
        <KpiCard label="Escalation Rate" value={k?.human_escalation_rate == null ? "—" : `${k.human_escalation_rate}%`} icon={PhoneForwarded} tone="warning" loading={isLoading} />
        <KpiCard label="Avg Resolution" value={k?.avg_resolution_time_min == null ? "—" : `${k.avg_resolution_time_min}m`} icon={AlarmClock} loading={isLoading} />
      </div>

      <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle>Tickets — Last 7 Days</CardTitle>
            <Badge variant="secondary">Daily</Badge>
          </CardHeader>
          <CardContent>
            <CallsTrendChart data={data?.trend_7d ?? []} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle className="flex items-center gap-2">
              <Bell className="h-4 w-4 text-primary" /> Live Alerts
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2.5">
            {(notifications ?? []).length === 0 && (
              <p className="py-6 text-center text-sm text-muted-foreground">No alerts.</p>
            )}
            {(notifications ?? []).slice(0, 6).map((n) => (
              <div key={n.id} className="flex items-start gap-3 rounded-lg border border-border p-3">
                <span
                  className={cn(
                    "mt-1.5 h-2 w-2 shrink-0 rounded-full",
                    n.type === "critical" ? "bg-destructive animate-pulse-ring" : n.type === "warning" ? "bg-warning" : "bg-primary",
                  )}
                />
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{n.title}</p>
                  <p className="truncate text-xs text-muted-foreground">{n.body}</p>
                  <p className="mt-0.5 text-[11px] text-muted-foreground">{formatRelativeTime(n.created_at)}</p>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>

      <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>Most Common Issues</CardTitle></CardHeader>
          <CardContent>
            {(data?.common_issues ?? []).length === 0 ? (
              <p className="py-10 text-center text-sm text-muted-foreground">No tickets yet.</p>
            ) : (
              <IssuesBarChart data={data!.common_issues} />
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle>Recent Complaints</CardTitle>
            <Link href="/tickets" className="text-xs font-medium text-primary hover:underline">View all</Link>
          </CardHeader>
          <CardContent className="space-y-3">
            {(data?.recent_complaints ?? []).length === 0 && (
              <p className="py-6 text-center text-sm text-muted-foreground">No complaints on record.</p>
            )}
            {(data?.recent_complaints ?? []).map((c) => (
              <div key={c.ticket_no} className="flex items-start gap-3 rounded-lg border border-border p-3">
                <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-warning/15 text-warning">
                  <Ticket className="h-4 w-4" />
                </div>
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{c.category}</p>
                  <p className="truncate text-xs text-muted-foreground">{c.description}</p>
                  <p className="mt-0.5 text-[11px] text-muted-foreground">{c.ticket_no} · {formatRelativeTime(c.created_at)}</p>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>
    </>
  );
}
