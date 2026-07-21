"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { MessageSquareText, PhoneForwarded, Search } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { PageHeader } from "@/components/shared/page-header";
import { useConversations } from "@/lib/hooks";
import { formatRelativeTime } from "@/lib/utils";

export default function AiConversationsPage() {
  const [raw, setRaw] = useState("");
  const [q, setQ] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setQ(raw), 300);
    return () => clearTimeout(t);
  }, [raw]);
  const { data: convos } = useConversations(q);
  const rows = convos ?? [];

  return (
    <>
      <PageHeader title="AI Conversations" description="Call records handled by the AI agent, derived from ticket activity." />
      <Card className="p-4">
        <div className="relative mb-4 max-w-md">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input value={raw} onChange={(e) => setRaw(e.target.value)} placeholder="Search customer, intent, phone…" className="pl-9" />
        </div>
        <div className="overflow-hidden rounded-lg border border-border">
          <Table>
            <TableHeader>
              <TableRow className="bg-muted/40 hover:bg-muted/40">
                <TableHead>Call ID</TableHead><TableHead>Customer</TableHead><TableHead>Intent</TableHead>
                <TableHead>Summary</TableHead><TableHead>Outcome</TableHead><TableHead>Ticket</TableHead><TableHead>When</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((c) => (
                <TableRow key={c.call_id}>
                  <TableCell className="font-mono text-xs text-primary">{c.call_id}</TableCell>
                  <TableCell className="text-sm">{c.customer_name}<div className="text-xs text-muted-foreground">{c.phone || "—"}</div></TableCell>
                  <TableCell className="max-w-[200px] truncate text-sm">{c.intent || "—"}</TableCell>
                  <TableCell className="max-w-[280px] truncate text-sm text-muted-foreground">{c.summary || "—"}</TableCell>
                  <TableCell>{c.escalated ? <Badge variant="warning" className="gap-1"><PhoneForwarded className="h-3 w-3" /> Escalated</Badge> : <Badge variant="success">AI Handled</Badge>}</TableCell>
                  <TableCell>{c.ticket_id ? <Link href={`/tickets/${c.ticket_id}`} className="font-mono text-xs text-primary hover:underline">{c.ticket_id}</Link> : "—"}</TableCell>
                  <TableCell className="whitespace-nowrap text-xs text-muted-foreground">{formatRelativeTime(c.started_at)}</TableCell>
                </TableRow>
              ))}
              {rows.length === 0 && (
                <TableRow><TableCell colSpan={7} className="py-10 text-center text-sm text-muted-foreground"><MessageSquareText className="mx-auto mb-2 h-6 w-6" />No conversations recorded yet.</TableCell></TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      </Card>
    </>
  );
}
