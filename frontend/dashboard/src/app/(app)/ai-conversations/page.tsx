"use client";
import { useEffect, useState } from "react";
import { MessageSquareText, PhoneForwarded, Search } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { PageHeader } from "@/components/shared/page-header";
import { SentimentDot } from "@/components/shared/badges";
import { useConversations } from "@/lib/hooks";
import { formatDuration, formatRelativeTime } from "@/lib/utils";

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
      <PageHeader title="AI Conversations" description="Searchable archive of every AI call — transcript, intent, latency, tokens and sentiment." />
      <Card className="p-4">
        <div className="relative mb-4 max-w-md">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input value={raw} onChange={(e) => setRaw(e.target.value)} placeholder="Search transcripts, intent, phone…" className="pl-9" />
        </div>
        <div className="overflow-hidden rounded-lg border border-border">
          <Table>
            <TableHeader>
              <TableRow className="bg-muted/40 hover:bg-muted/40">
                <TableHead>Call</TableHead><TableHead>Customer</TableHead><TableHead>Intent</TableHead>
                <TableHead>Lang</TableHead><TableHead>Duration</TableHead><TableHead>Turns</TableHead>
                <TableHead>Tokens</TableHead><TableHead>Sentiment</TableHead><TableHead>Outcome</TableHead><TableHead>When</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((c) => (
                <TableRow key={c.call_id}>
                  <TableCell className="font-mono text-xs text-primary">{c.call_id}</TableCell>
                  <TableCell className="text-sm">{c.customer_name}<div className="text-xs text-muted-foreground">{c.phone}</div></TableCell>
                  <TableCell className="max-w-[200px] truncate text-sm">{c.intent}</TableCell>
                  <TableCell><Badge variant="secondary">{c.language}</Badge></TableCell>
                  <TableCell className="font-mono text-xs">{formatDuration(c.duration_s)}</TableCell>
                  <TableCell className="text-sm">{c.turns}</TableCell>
                  <TableCell className="text-sm tabular-nums">{c.tokens.toLocaleString()}</TableCell>
                  <TableCell><SentimentDot sentiment={c.sentiment} /></TableCell>
                  <TableCell>{c.escalated ? <Badge variant="warning" className="gap-1"><PhoneForwarded className="h-3 w-3" /> Escalated</Badge> : <Badge variant="success">AI Resolved</Badge>}</TableCell>
                  <TableCell className="whitespace-nowrap text-xs text-muted-foreground">{formatRelativeTime(c.started_at)}</TableCell>
                </TableRow>
              ))}
              {rows.length === 0 && (
                <TableRow><TableCell colSpan={10} className="py-10 text-center text-sm text-muted-foreground"><MessageSquareText className="mx-auto mb-2 h-6 w-6" />No conversations found.</TableCell></TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      </Card>
    </>
  );
}
