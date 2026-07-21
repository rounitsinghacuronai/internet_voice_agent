"use client";
import { Ear, Loader2, PhoneForwarded, Volume2, Wrench, PhoneCall } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageHeader, EmptyState } from "@/components/shared/page-header";
import { SentimentDot } from "@/components/shared/badges";
import { useLiveCalls } from "@/lib/hooks";
import { formatDuration, cn } from "@/lib/utils";
import type { CallStage } from "@/lib/api/types";

const STAGES: { key: CallStage; label: string; icon: typeof Ear; className: string }[] = [
  { key: "listening", label: "Listening", icon: Ear, className: "text-primary bg-primary/10" },
  { key: "thinking", label: "Thinking", icon: Loader2, className: "text-violet-500 bg-violet-500/10" },
  { key: "calling_tool", label: "Calling Tool", icon: Wrench, className: "text-amber-500 bg-amber-500/10" },
  { key: "speaking", label: "Speaking", icon: Volume2, className: "text-success bg-success/10" },
  { key: "escalating", label: "Escalating", icon: PhoneForwarded, className: "text-destructive bg-destructive/10" },
];

function StagePipeline({ stage }: { stage: CallStage }) {
  const activeIdx = STAGES.findIndex((s) => s.key === stage);
  return (
    <div className="flex items-center gap-1.5">
      {STAGES.map((s, i) => {
        const Icon = s.icon;
        const active = i === activeIdx;
        return (
          <div
            key={s.key}
            title={s.label}
            className={cn(
              "flex h-7 w-7 items-center justify-center rounded-lg transition-all",
              active ? s.className : "bg-muted text-muted-foreground/40",
            )}
          >
            <Icon className={cn("h-3.5 w-3.5", active && s.key === "thinking" && "animate-spin")} />
          </div>
        );
      })}
    </div>
  );
}

export default function LiveCallsPage() {
  const { data: calls, isLoading } = useLiveCalls();

  return (
    <>
      <PageHeader
        title="Live Call Monitor"
        description="Observe every ongoing AI conversation in real time — intent, tools, sentiment and stage."
        actions={
          <Badge variant="success" className="gap-1.5 px-3 py-1">
            <span className="h-1.5 w-1.5 rounded-full bg-success animate-pulse-ring" />
            {calls?.length ?? 0} active
          </Badge>
        }
      />

      {!isLoading && (!calls || calls.length === 0) ? (
        <EmptyState icon={<PhoneCall className="h-6 w-6" />} title="No active calls" description="Live sessions will appear here the moment a caller connects to the AI agent." />
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 xl:grid-cols-3">
          {(calls ?? []).map((call) => (
            <Card key={call.call_id} className="overflow-hidden">
              <div className="flex items-center justify-between border-b border-border bg-muted/30 px-5 py-3">
                <div className="flex items-center gap-3">
                  <div className="flex h-9 w-9 items-center justify-center rounded-full bg-primary/10 text-primary">
                    <PhoneCall className="h-4 w-4" />
                  </div>
                  <div>
                    <p className="text-sm font-semibold">{call.customer_name}</p>
                    <p className="text-xs text-muted-foreground">{call.phone}</p>
                  </div>
                </div>
                <Badge variant="outline" className="font-mono text-xs">
                  {formatDuration(call.duration_s)}
                </Badge>
              </div>

              <CardContent className="space-y-3 p-5">
                <div className="flex items-center justify-between">
                  <StagePipeline stage={call.stage} />
                  <Badge variant="secondary" className="text-xs">
                    {call.language}
                  </Badge>
                </div>

                <div>
                  <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Current Intent</p>
                  <p className="text-sm font-medium">{call.intent}</p>
                </div>

                <div className="rounded-lg bg-muted/40 p-3">
                  <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">AI Response</p>
                  <p className="mt-0.5 line-clamp-2 text-sm">{call.ai_response}</p>
                </div>

                <div className="flex items-center justify-between text-sm">
                  <div className="flex items-center gap-2">
                    {call.current_tool ? (
                      <Badge variant="warning" className="gap-1 font-mono text-[11px]">
                        <Wrench className="h-3 w-3" /> {call.current_tool}
                      </Badge>
                    ) : (
                      <span className="text-xs text-muted-foreground">No tool running</span>
                    )}
                  </div>
                  <SentimentDot sentiment={call.sentiment} />
                </div>

                <div className="flex gap-2 pt-1">
                  <Button size="sm" variant="outline" className="flex-1">
                    Listen in
                  </Button>
                  <Button size="sm" variant="secondary" className="flex-1">
                    Take over
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </>
  );
}
