"use client";
import { Activity, Cpu, MemoryStick, TriangleAlert } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/shared/page-header";
import { KpiCard } from "@/components/dashboard/kpi-card";
import { useSystemHealth } from "@/lib/hooks";
import { cn } from "@/lib/utils";

const statusStyle: Record<string, string> = {
  operational: "bg-success/15 text-success",
  degraded: "bg-warning/15 text-warning",
  down: "bg-destructive/15 text-destructive",
  idle: "bg-muted text-muted-foreground",
};

function uptime(s: number) {
  const h = Math.floor(s / 3600);
  const d = Math.floor(h / 24);
  return d > 0 ? `${d}d ${h % 24}h` : `${h}h`;
}

export default function SystemHealthPage() {
  const { data } = useSystemHealth();

  return (
    <>
      <PageHeader
        title="System Health"
        description="Live status of every service in the voice pipeline."
        actions={<Badge variant="success" className="gap-1.5 px-3 py-1"><span className="h-1.5 w-1.5 rounded-full bg-success animate-pulse-ring" /> Auto-refresh</Badge>}
      />

      <div className="mb-6 grid grid-cols-2 gap-4 md:grid-cols-4">
        <KpiCard label="Uptime" value={data ? uptime(data.uptime_seconds) : "—"} icon={Activity} tone="success" />
        <KpiCard label="CPU" value={data?.metrics.cpu_percent == null ? "—" : `${data.metrics.cpu_percent}%`} icon={Cpu} />
        <KpiCard label="Memory" value={data?.metrics.memory_percent == null ? "—" : `${data.metrics.memory_percent}%`} icon={MemoryStick} />
        <KpiCard label="API Errors (24h)" value={data?.metrics.api_errors_24h ?? "—"} icon={TriangleAlert} tone={data?.metrics.api_errors_24h ? "destructive" : "success"} />
      </div>

      <Card>
        <CardHeader><CardTitle>Service Components</CardTitle></CardHeader>
        <CardContent className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {(data?.components ?? []).map((c) => (
            <div key={c.name} className="flex items-center justify-between rounded-lg border border-border p-4">
              <div>
                <p className="text-sm font-medium">{c.name}</p>
                {c.detail && <p className="text-xs text-muted-foreground">{c.detail}</p>}
                {c.latency_ms !== undefined && <p className="text-xs text-muted-foreground">{c.latency_ms} ms</p>}
              </div>
              <Badge className={cn("capitalize", statusStyle[c.status])}>
                <span className={cn("mr-1 h-1.5 w-1.5 rounded-full", c.status === "operational" ? "bg-success" : c.status === "degraded" ? "bg-warning" : c.status === "down" ? "bg-destructive" : "bg-muted-foreground")} />
                {c.status}
              </Badge>
            </div>
          ))}
        </CardContent>
      </Card>
    </>
  );
}
