"use client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/shared/page-header";
import { CallsTrendChart, IssuesBarChart } from "@/components/dashboard/charts";
import { KpiCard } from "@/components/dashboard/kpi-card";
import { CheckCircle2, Gauge, PhoneForwarded, Ticket } from "lucide-react";
import { useDashboardStats } from "@/lib/hooks";

export default function AnalyticsPage() {
  const { data } = useDashboardStats();
  const k = data?.kpis;

  return (
    <>
      <PageHeader title="Analytics" description="Trends and distributions derived from real ticket data." />

      <div className="mb-6 grid grid-cols-2 gap-4 md:grid-cols-4">
        <KpiCard label="Total Tickets" value={k?.total_tickets ?? "—"} icon={Ticket} />
        <KpiCard label="Resolved" value={k?.resolved_tickets ?? "—"} icon={CheckCircle2} tone="success" />
        <KpiCard label="AI Resolution" value={k?.ai_resolution_rate == null ? "—" : `${k.ai_resolution_rate}%`} icon={Gauge} tone="success" />
        <KpiCard label="Escalation Rate" value={k?.human_escalation_rate == null ? "—" : `${k.human_escalation_rate}%`} icon={PhoneForwarded} tone="warning" />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>Tickets — Last 7 Days</CardTitle></CardHeader>
          <CardContent><CallsTrendChart data={data?.trend_7d ?? []} /></CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Tickets by Hour</CardTitle></CardHeader>
          <CardContent><CallsTrendChart data={data?.peak_hours ?? []} /></CardContent>
        </Card>
        <Card className="lg:col-span-2">
          <CardHeader><CardTitle>Top Issue Categories</CardTitle></CardHeader>
          <CardContent>
            {(data?.common_issues ?? []).length === 0 ? (
              <p className="py-10 text-center text-sm text-muted-foreground">No tickets yet.</p>
            ) : (
              <IssuesBarChart data={data!.common_issues} />
            )}
          </CardContent>
        </Card>
      </div>
    </>
  );
}
