"use client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/shared/page-header";
import { CallsTrendChart, IssuesBarChart, LanguageDonut } from "@/components/dashboard/charts";
import { KpiCard } from "@/components/dashboard/kpi-card";
import { Gauge, PhoneForwarded, Smile, Timer } from "lucide-react";
import { useDashboardStats } from "@/lib/hooks";

const monthly = [
  { label: "Feb", calls: 6820 }, { label: "Mar", calls: 7410 }, { label: "Apr", calls: 8020 },
  { label: "May", calls: 8890 }, { label: "Jun", calls: 9350 }, { label: "Jul", calls: 10120 },
];
const peak = [
  { label: "9a", calls: 42 }, { label: "11a", calls: 88 }, { label: "1p", calls: 71 },
  { label: "3p", calls: 96 }, { label: "5p", calls: 120 }, { label: "7p", calls: 84 }, { label: "9p", calls: 47 },
];

export default function AnalyticsPage() {
  const { data } = useDashboardStats();
  const k = data?.kpis;

  return (
    <>
      <PageHeader title="Analytics" description="Trends, intent distribution, latency and satisfaction across the platform." />

      <div className="mb-6 grid grid-cols-2 gap-4 md:grid-cols-4">
        <KpiCard label="AI Accuracy" value={k ? `${k.ai_resolution_rate}%` : "—"} icon={Gauge} tone="success" delta={3} />
        <KpiCard label="Transfer %" value={k ? `${k.human_escalation_rate}%` : "—"} icon={PhoneForwarded} tone="warning" />
        <KpiCard label="Avg Latency" value={k ? `${k.avg_response_time_s}s` : "—"} icon={Timer} />
        <KpiCard label="CSAT" value={k ? `${k.customer_satisfaction}/5` : "—"} icon={Smile} tone="success" delta={2} />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader><CardTitle>Monthly Call Volume</CardTitle></CardHeader>
          <CardContent><CallsTrendChart data={monthly} /></CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Language Distribution</CardTitle></CardHeader>
          <CardContent>{data && <LanguageDonut data={data.language_distribution} />}</CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Intent / Top Complaints</CardTitle></CardHeader>
          <CardContent>{data && <IssuesBarChart data={data.common_issues} />}</CardContent>
        </Card>
        <Card className="lg:col-span-2">
          <CardHeader><CardTitle>Peak Hours</CardTitle></CardHeader>
          <CardContent><CallsTrendChart data={peak} /></CardContent>
        </Card>
      </div>
    </>
  );
}
