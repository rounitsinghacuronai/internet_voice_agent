"use client";
import { useEffect, useState } from "react";
import { MessageSquareText, PhoneForwarded, Search, ShieldCheck } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { PageHeader } from "@/components/shared/page-header";
import { useCalls } from "@/lib/hooks";
import { formatDuration, formatRelativeTime } from "@/lib/utils";

export default function AiConversationsPage() {
  const [raw, setRaw] = useState("");
  const [q, setQ] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setQ(raw), 300);
    return () => clearTimeout(t);
  }, [raw]);
  const { data: calls } = useCalls(q);
  const rows = calls ?? [];

  return (
    <>
      <PageHeader title="AI Conversations" description="Every call the AI agent handled, logged in real time." />
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
                <TableHead>Lang</TableHead><TableHead>Duration</TableHead><TableHead>Outcome</TableHead><TableHead>When</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((c) => (
                <TableRow key={c.session_id}>
                  <TableCell className="font-mono text-xs text-primary">{c.session_id}</TableCell>
                  <TableCell className="text-sm">{c.caller || "—"}</TableCell>
                  <TableCell className="text-sm">
                    <span className="inline-flex items-center gap-1">{c.customer_name || "Unknown"}{c.verified ? <ShieldCheck className="h-3 w-3 text-success" /> : null}</span>
                    {c.account_no && <div className="font-mono text-[11px] text-muted-foreground">{c.account_no}</div>}
                  </TableCell>
                  <TableCell><Badge variant="secondary">{c.language || "—"}</Badge></TableCell>
                  <TableCell className="font-mono text-xs">{c.duration_s ? formatDuration(c.duration_s) : c.outcome === "in_progress" ? "live" : "—"}</TableCell>
                  <TableCell>
                    {c.outcome === "in_progress" ? <Badge variant="warning">In progress</Badge>
                      : c.escalated ? <Badge variant="warning" className="gap-1"><PhoneForwarded className="h-3 w-3" /> Escalated</Badge>
                      : <Badge variant="success">AI Handled</Badge>}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-xs text-muted-foreground">{formatRelativeTime(c.started_at)}</TableCell>
                </TableRow>
              ))}
              {rows.length === 0 && (
                <TableRow><TableCell colSpan={7} className="py-10 text-center text-sm text-muted-foreground"><MessageSquareText className="mx-auto mb-2 h-6 w-6" />No calls logged yet. Completed calls will appear here.</TableCell></TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      </Card>
    </>
  );
}
