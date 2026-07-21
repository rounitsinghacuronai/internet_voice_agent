"use client";
import Link from "next/link";
import { Headphones, Timer } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { KpiCard } from "@/components/dashboard/kpi-card";
import { PageHeader } from "@/components/shared/page-header";
import { useEscalations } from "@/lib/hooks";
import { formatRelativeTime } from "@/lib/utils";

export default function HumanEscalationsPage() {
  const { data: escalations } = useEscalations();
  const rows = escalations ?? [];
  const avgHandoff = rows.length ? Math.round(rows.reduce((a, e) => a + e.handoff_seconds, 0) / rows.length) : 0;
  const open = rows.filter((e) => !e.resolution).length;

  return (
    <>
      <PageHeader title="Human Escalations" description="AI-to-human handoffs with transfer reason, executive, and resolution." />
      <div className="mb-6 grid grid-cols-2 gap-4 md:grid-cols-4">
        <KpiCard label="Total Escalations" value={rows.length} icon={Headphones} />
        <KpiCard label="Awaiting Resolution" value={open} icon={Headphones} tone="warning" />
        <KpiCard label="Avg Handoff Time" value={`${avgHandoff}s`} icon={Timer} tone="success" />
        <KpiCard label="Resolved" value={rows.length - open} icon={Headphones} tone="success" />
      </div>

      <div className="space-y-3">
        {rows.map((e) => (
          <Card key={e.id}>
            <CardHeader className="flex-row items-center justify-between pb-2">
              <CardTitle className="flex items-center gap-2 text-base">
                <Link href={`/tickets/${e.ticket_id}`} className="font-mono text-sm text-primary hover:underline">{e.ticket_id}</Link>
                <Badge variant="secondary">{e.category}</Badge>
              </CardTitle>
              <div className="flex items-center gap-2">
                {e.resolution ? <Badge variant="success">Resolved</Badge> : <Badge variant="warning">In progress</Badge>}
                <span className="text-xs text-muted-foreground">{formatRelativeTime(e.transferred_at)}</span>
              </div>
            </CardHeader>
            <CardContent className="grid gap-3 md:grid-cols-3">
              <div>
                <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Reason</p>
                <p className="text-sm font-medium">{e.reason}</p>
              </div>
              <div>
                <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Transferred to</p>
                <p className="text-sm font-medium">{e.executive} · <span className="text-muted-foreground">{e.handoff_seconds}s handoff</span></p>
              </div>
              <div className="md:col-span-3">
                <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">AI Summary</p>
                <p className="text-sm">{e.summary}</p>
                {e.resolution && <p className="mt-1 text-sm text-success">{e.resolution}</p>}
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </>
  );
}
