"use client";
import { useEffect, useState } from "react";
import { Bot, MessageSquareText, PhoneForwarded, Search, ShieldCheck, User } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from "@/components/ui/sheet";
import { PageHeader } from "@/components/shared/page-header";
import { SentimentDot } from "@/components/shared/badges";
import { useCalls, useCall } from "@/lib/hooks";
import { formatDuration, formatRelativeTime, formatDateTime, cn } from "@/lib/utils";

function TranscriptSheet({ id, open, onClose }: { id: string | null; open: boolean; onClose: () => void }) {
  const { data: call, isLoading } = useCall(id ?? "");
  return (
    <Sheet open={open} onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right" className="w-[520px] max-w-full">
        {id && (
          <>
            <SheetHeader>
              <SheetTitle className="font-mono text-base">{id}</SheetTitle>
              <SheetDescription>
                {call ? `${call.customer_name || call.caller || "Unknown"} · ${formatDateTime(call.started_at)}` : "Loading…"}
              </SheetDescription>
            </SheetHeader>

            {call && (
              <div className="mt-4 grid grid-cols-2 gap-2 text-sm">
                <div className="rounded-lg border border-border p-2"><p className="text-[11px] uppercase text-muted-foreground">Duration</p><p className="font-medium">{call.duration_s ? formatDuration(call.duration_s) : "—"}</p></div>
                <div className="rounded-lg border border-border p-2"><p className="text-[11px] uppercase text-muted-foreground">Sentiment</p><SentimentDot sentiment={call.sentiment || "neutral"} /></div>
                <div className="rounded-lg border border-border p-2"><p className="text-[11px] uppercase text-muted-foreground">Avg latency</p><p className="font-medium">{call.avg_latency_ms ? `${call.avg_latency_ms} ms` : "—"}</p></div>
                <div className="rounded-lg border border-border p-2"><p className="text-[11px] uppercase text-muted-foreground">Turns</p><p className="font-medium">{call.turns}</p></div>
                {(call.tools ?? []).length > 0 && (
                  <div className="col-span-2 rounded-lg border border-border p-2">
                    <p className="mb-1 text-[11px] uppercase text-muted-foreground">Tools used</p>
                    <div className="flex flex-wrap gap-1">{call.tools!.map((t, i) => <Badge key={i} variant="secondary" className="font-mono text-[11px]">{t}</Badge>)}</div>
                  </div>
                )}
              </div>
            )}

            <div className="mt-4">
              <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Transcript</p>
              {isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}
              <div className="space-y-3">
                {(call?.transcript ?? []).map((m, i) => (
                  <div key={i} className={cn("flex gap-2", m.role === "user" ? "" : "flex-row-reverse")}>
                    <div className={cn("flex h-7 w-7 shrink-0 items-center justify-center rounded-full", m.role === "user" ? "bg-muted" : "bg-primary/15 text-primary")}>
                      {m.role === "user" ? <User className="h-3.5 w-3.5" /> : <Bot className="h-3.5 w-3.5" />}
                    </div>
                    <div className={cn("max-w-[80%] rounded-2xl px-3 py-2 text-sm", m.role === "user" ? "bg-muted" : "bg-primary/10")}>
                      {m.content}
                    </div>
                  </div>
                ))}
                {call && (call.transcript ?? []).length === 0 && (
                  <p className="text-sm text-muted-foreground">No transcript recorded for this call.</p>
                )}
              </div>
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}

export default function AiConversationsPage() {
  const [raw, setRaw] = useState("");
  const [q, setQ] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);
  useEffect(() => {
    const t = setTimeout(() => setQ(raw), 300);
    return () => clearTimeout(t);
  }, [raw]);
  const { data: calls } = useCalls(q);
  const rows = calls ?? [];

  return (
    <>
      <PageHeader title="AI Conversations" description="Every call the AI agent handled — click a row to read the full transcript." />
      <Card className="p-4">
        <div className="relative mb-4 max-w-md">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input value={raw} onChange={(e) => setRaw(e.target.value)} placeholder="Search caller, customer, intent…" className="pl-9" />
        </div>
        <div className="overflow-hidden rounded-lg border border-border">
          <Table>
            <TableHeader>
              <TableRow className="bg-muted/40 hover:bg-muted/40">
                <TableHead>Call ID</TableHead><TableHead>Caller</TableHead><TableHead>Customer</TableHead>
                <TableHead>Lang</TableHead><TableHead>Duration</TableHead><TableHead>Sentiment</TableHead><TableHead>Outcome</TableHead><TableHead>When</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((c) => (
                <TableRow key={c.session_id} className="cursor-pointer" onClick={() => setOpenId(c.session_id)}>
                  <TableCell className="font-mono text-xs text-primary">{c.session_id}</TableCell>
                  <TableCell className="text-sm">{c.caller || "—"}</TableCell>
                  <TableCell className="text-sm">
                    <span className="inline-flex items-center gap-1">{c.customer_name || "Unknown"}{c.verified ? <ShieldCheck className="h-3 w-3 text-success" /> : null}</span>
                  </TableCell>
                  <TableCell><Badge variant="secondary">{c.language || "—"}</Badge></TableCell>
                  <TableCell className="font-mono text-xs">{c.duration_s ? formatDuration(c.duration_s) : c.outcome === "in_progress" ? "live" : "—"}</TableCell>
                  <TableCell>{c.sentiment ? <SentimentDot sentiment={c.sentiment} /> : "—"}</TableCell>
                  <TableCell>
                    {c.outcome === "in_progress" ? <Badge variant="warning">In progress</Badge>
                      : c.escalated ? <Badge variant="warning" className="gap-1"><PhoneForwarded className="h-3 w-3" /> Escalated</Badge>
                      : <Badge variant="success">AI Handled</Badge>}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-xs text-muted-foreground">{formatRelativeTime(c.started_at)}</TableCell>
                </TableRow>
              ))}
              {rows.length === 0 && (
                <TableRow><TableCell colSpan={8} className="py-10 text-center text-sm text-muted-foreground"><MessageSquareText className="mx-auto mb-2 h-6 w-6" />No calls logged yet. Completed calls will appear here.</TableCell></TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      </Card>

      <TranscriptSheet id={openId} open={!!openId} onClose={() => setOpenId(null)} />
    </>
  );
}
