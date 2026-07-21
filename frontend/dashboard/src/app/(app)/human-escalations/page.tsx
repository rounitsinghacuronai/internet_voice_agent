"use client";
import Link from "next/link";
import { Headphones } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { KpiCard } from "@/components/dashboard/kpi-card";
import { PageHeader, EmptyState } from "@/components/shared/page-header";
import { useEscalations } from "@/lib/hooks";
import { formatRelativeTime } from "@/lib/utils";

export default function HumanEscalationsPage() {
  const { data: escalations, isLoading } = useEscalations();
  const rows = escalations ?? [];
  const open = rows.filter((e) => !e.resolution).length;

  return (
    <>
      <PageHeader title="Human Escalations" description="AI-to-human handoffs derived from escalation tickets." />
      <div className="mb-6 grid grid-cols-2 gap-4 md:grid-cols-3">
        <KpiCard label="Total Escalations" value={rows.length} icon={Headphones} />
        <KpiCard label="Awaiting Resolution" value={open} icon={Headphones} tone="warning" />
        <KpiCard label="Resolved" value={rows.length - open} icon={Headphones} tone="success" />
      </div>

      {!isLoading && rows.length === 0 ? (
        <EmptyState icon={<Headphones className="h-6 w-6" />} title="No escalations" description="Escalation tickets raised by the AI agent will appear here." />
      ) : (
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
                  <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Customer</p>
                  <p className="text-sm font-medium">{e.customer_name || "Unknown"}{e.mobile ? ` · ${e.mobile}` : ""}</p>
                </div>
                <div className="md:col-span-3">
                  <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">AI Summary</p>
                  <p className="text-sm">{e.summary || "—"}</p>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </>
  );
}
