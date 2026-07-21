"use client";
import { useState } from "react";
import { Search } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { PageHeader } from "@/components/shared/page-header";

const LOGS = [
  { ts: "2026-07-21 13:42", actor: "Rounit Singh", role: "Super Admin", action: "Voice pace changed 1.5 → 1.3", type: "System Config", ip: "10.0.0.4" },
  { ts: "2026-07-21 12:10", actor: "Priya Nair", role: "Supervisor", action: "Ticket TT-2026-8AF8 resolved", type: "Ticket Change", ip: "10.0.0.9" },
  { ts: "2026-07-21 11:03", actor: "Amit Kulkarni", role: "Executive", action: "Took over live call live-4d20", type: "Executive Action", ip: "10.0.0.12" },
  { ts: "2026-07-21 09:55", actor: "System", role: "AI", action: "Knowledge base reindexed (v4.2.1)", type: "Knowledge Update", ip: "127.0.0.1" },
  { ts: "2026-07-20 18:22", actor: "Rounit Singh", role: "Super Admin", action: "Prompt version updated to prompt-v11", type: "Prompt Change", ip: "10.0.0.4" },
  { ts: "2026-07-20 08:00", actor: "Sneha Rao", role: "Executive", action: "Login", type: "Login", ip: "10.0.0.21" },
];

const typeVariant: Record<string, "default" | "secondary" | "warning" | "destructive" | "success"> = {
  "System Config": "warning", "Prompt Change": "warning", "Knowledge Update": "default",
  "Ticket Change": "secondary", "Executive Action": "secondary", Login: "success",
};

export default function AuditLogsPage() {
  const [q, setQ] = useState("");
  const rows = LOGS.filter((l) => JSON.stringify(l).toLowerCase().includes(q.toLowerCase()));

  return (
    <>
      <PageHeader title="Audit Logs" description="Immutable trail of every privileged action across the platform." />
      <Card className="p-4">
        <div className="relative mb-4 max-w-md">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search actor, action, type…" className="pl-9" />
        </div>
        <div className="overflow-hidden rounded-lg border border-border">
          <Table>
            <TableHeader>
              <TableRow className="bg-muted/40 hover:bg-muted/40">
                <TableHead>Timestamp</TableHead><TableHead>Actor</TableHead><TableHead>Role</TableHead><TableHead>Action</TableHead><TableHead>Type</TableHead><TableHead>IP</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((l, i) => (
                <TableRow key={i}>
                  <TableCell className="whitespace-nowrap font-mono text-xs">{l.ts}</TableCell>
                  <TableCell className="text-sm font-medium">{l.actor}</TableCell>
                  <TableCell className="text-sm text-muted-foreground">{l.role}</TableCell>
                  <TableCell className="text-sm">{l.action}</TableCell>
                  <TableCell><Badge variant={typeVariant[l.type] ?? "secondary"}>{l.type}</Badge></TableCell>
                  <TableCell className="font-mono text-xs text-muted-foreground">{l.ip}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </Card>
    </>
  );
}
