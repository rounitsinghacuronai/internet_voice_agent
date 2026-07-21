"use client";
import Link from "next/link";
import {
  AlarmClock,
  Bell,
  CheckCircle2,
  Clock,
  Gauge,
  Globe2,
  PhoneCall,
  PhoneForwarded,
  Smile,
  Ticket,
  TriangleAlert,
  Users,
} from "lucide-react";
import { KpiCard } from "@/components/dashboard/kpi-card";
import { CallsTrendChart, IssuesBarChart, LanguageDonut } from "@/components/dashboard/charts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/shared/page-header";
import { useDashboardStats, useNotifications } from "@/lib/hooks";
import { formatRelativeTime } from "@/lib/utils";
import { cn } from "@/lib/utils";

const trend = [
  { label: "Mon", calls: 268 },
  { label: "Tue", calls: 312 },
  { label: "Wed", calls: 289 },
  { label: "Thu", calls: 341 },
  { label: "Fri", calls: 402 },
  { label: "Sat", calls: 356 },
  { label: "Sun", calls: 298 },
];

export default function DashboardPage() {
  const { data, isLoading } = useDashboardStats();
  const { data: notifications } = useNotifications();
  const k = data?.kpis;

  return (
    <>
      <PageHeader
        title="Operations Overview"
        description="Real-time health of the AI voice agent across all live customer interactions."
        actions={
          <Badge variant="success" className="gap-1.5 px-3 py-1">
            <span className="h-1.5 w-1.5 rounded-full bg-success animate-pulse-ring" /> Live
          </Badge>
        }
      />

      <div className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-4">
        <KpiCard label="Today's Calls" value={k?.todays_calls ?? "—"} icon={PhoneCall} delta={8} loading={isLoading} />
        <KpiCard label="Active Calls" value={k?.active_calls ?? "—"} icon={Gauge} tone="success" hint="in progress now" loading={isLoading} />
        <KpiCard label="Open Tickets" value={k?.open_tickets ?? "—"} icon={Ticket} tone="warning" loading={isLoading} />
        <KpiCard label="Critical Tickets" value={k?.critical_tickets ?? "—"} icon={TriangleAlert} tone="destructive" loading={isLoading} />
        <KpiCard label="Resolved Tickets" value={k?.resolved_tickets ?? "—"} icon={CheckCircle2} tone="success" delta={5} loading={isLoading} />
        <KpiCard label="Transferred Calls" value={k?.transferred_calls ?? "—"} icon={PhoneForwarded} loading={isLoading} />
        <KpiCard label="Avg Response" value={k ? `${k.avg_response_time_s}s` : "—"} icon={Clock} loading={isLoading} />
        <KpiCard label="Avg Resolution" value={k ? `${k.avg_resolution_time_min}m` : "—"} icon={AlarmClock} loading={isLoading} />
        <KpiCard label="CSAT" value={k ? `${k.customer_satisfaction}/5` : "—"} icon={Smile} tone="success" delta={2} loading={isLoading} />
        <KpiCard label="AI Resolution Rate" value={k ? `${k.ai_resolution_rate}%` : "—"} icon={Gauge} tone="success" loading={isLoading} />
        <KpiCard label="Escalation Rate" value={k ? `${k.human_escalation_rate}%` : "—"} icon={PhoneForwarded} tone="warning" loading={isLoading} />
        <KpiCard label="Avg Call Duration" value={k ? `${Math.round(k.avg_call_duration_s / 60)}m ${k.avg_call_duration_s % 60}s` : "—"} icon={Clock} loading={isLoading} />
      </div>

      <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle>Call Volume — Last 7 Days</CardTitle>
            <Badge variant="secondary">Weekly</Badge>
          </CardHeader>
          <CardContent>
            <CallsTrendChart data={trend} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Globe2 className="h-4 w-4 text-primary" /> Language Distribution
            </CardTitle>
          </CardHeader>
          <CardContent>{data && <LanguageDonut data={data.language_distribution} />}</CardContent>
        </Card>
      </div>

      <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>Most Common Issues</CardTitle>
          </CardHeader>
          <CardContent>{data && <IssuesBarChart data={data.common_issues} />}</CardContent>
        </Card>

        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle>Recent Complaints</CardTitle>
            <Link href="/tickets" className="text-xs font-medium text-primary hover:underline">
              View all
            </Link>
          </CardHeader>
          <CardContent className="space-y-3">
            {(data?.recent_complaints ?? []).map((c) => (
              <div key={c.ticket_no} className="flex items-start gap-3 rounded-lg border border-border p-3">
                <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-warning/15 text-warning">
                  <Ticket className="h-4 w-4" />
                </div>
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{c.category}</p>
                  <p className="truncate text-xs text-muted-foreground">{c.description}</p>
                  <p className="mt-0.5 text-[11px] text-muted-foreground">
                    {c.ticket_no} · {formatRelativeTime(c.created_at)}
                  </p>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle className="flex items-center gap-2">
              <Bell className="h-4 w-4 text-primary" /> Live Alerts
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2.5">
            {(notifications ?? []).map((n) => (
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
    </>
  );
}
