"use client";
import { PhoneCall } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { PageHeader } from "@/components/shared/page-header";
import { useExecutives } from "@/lib/hooks";
import { initials, cn } from "@/lib/utils";

const statusMap = {
  available: { label: "Available", cls: "bg-success/15 text-success", dot: "bg-success" },
  busy: { label: "On Call", cls: "bg-warning/15 text-warning", dot: "bg-warning" },
  offline: { label: "Offline", cls: "bg-muted text-muted-foreground", dot: "bg-muted-foreground" },
};

export default function ExecutivesPage() {
  const { data: execs } = useExecutives();
  const rows = execs ?? [];

  return (
    <>
      <PageHeader title="Executive Panel" description="Live availability, workload and performance across the support team." />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {rows.map((e) => {
          const s = statusMap[e.status];
          return (
            <Card key={e.id}>
              <CardContent className="p-5">
                <div className="flex items-center gap-3">
                  <div className="relative">
                    <Avatar className="h-11 w-11"><AvatarFallback>{initials(e.name)}</AvatarFallback></Avatar>
                    <span className={cn("absolute -bottom-0.5 -right-0.5 h-3 w-3 rounded-full border-2 border-card", s.dot)} />
                  </div>
                  <div className="flex-1">
                    <p className="font-semibold">{e.name}</p>
                    <Badge className={cn("mt-0.5 gap-1", s.cls)}><span className={cn("h-1.5 w-1.5 rounded-full", s.dot)} />{s.label}</Badge>
                  </div>
                </div>

                {e.current_call && (
                  <div className="mt-3 flex items-center gap-2 rounded-lg bg-primary/5 px-3 py-2 text-xs text-primary">
                    <PhoneCall className="h-3.5 w-3.5" /> On call — {e.current_call}
                  </div>
                )}

                <div className="mt-4 grid grid-cols-3 gap-2 text-center">
                  <div className="rounded-lg bg-muted/40 py-2">
                    <p className="text-lg font-bold">{e.calls_today}</p>
                    <p className="text-[10px] uppercase text-muted-foreground">Calls</p>
                  </div>
                  <div className="rounded-lg bg-muted/40 py-2">
                    <p className="text-lg font-bold">{e.avg_resolution_min || "—"}<span className="text-xs">m</span></p>
                    <p className="text-[10px] uppercase text-muted-foreground">Avg Res</p>
                  </div>
                  <div className="rounded-lg bg-muted/40 py-2">
                    <p className="text-lg font-bold">{e.satisfaction || "—"}</p>
                    <p className="text-[10px] uppercase text-muted-foreground">CSAT</p>
                  </div>
                </div>
                <p className="mt-3 text-xs text-muted-foreground">{e.transfers_handled} transfers handled today</p>
              </CardContent>
            </Card>
          );
        })}
      </div>
    </>
  );
}
