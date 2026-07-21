"use client";
import { useState } from "react";
import { BookOpen, FileText, Search } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/shared/page-header";

const CATEGORIES = [
  { name: "FAQs", count: 48 }, { name: "Plans", count: 22 }, { name: "Broadband", count: 31 },
  { name: "SIM", count: 14 }, { name: "Billing", count: 27 }, { name: "Policies", count: 12 }, { name: "Troubleshooting", count: 39 },
];
const ARTICLES = [
  { title: "Fiber LOS (red light) — diagnosis & engineer dispatch", category: "Troubleshooting", updated: "2d ago" },
  { title: "Postpaid billing cycle & GST breakdown", category: "Billing", updated: "5d ago" },
  { title: "SIM replacement & porting (MNP) process", category: "SIM", updated: "1w ago" },
  { title: "Fiber 100 / 300 Mbps plan comparison", category: "Plans", updated: "3d ago" },
  { title: "New connection feasibility & KYC requirements", category: "Policies", updated: "6d ago" },
  { title: "Area outage lookup & ETA communication script", category: "Troubleshooting", updated: "12h ago" },
];

export default function KnowledgeBasePage() {
  const [q, setQ] = useState("");
  const filtered = ARTICLES.filter((a) => a.title.toLowerCase().includes(q.toLowerCase()));

  return (
    <>
      <PageHeader
        title="Knowledge Base"
        description="Grounding content the AI retrieves from — FAQs, plans, policies and troubleshooting."
        actions={<Button className="gap-2"><FileText className="h-4 w-4" /> New Article</Button>}
      />

      <div className="mb-4 flex items-center gap-2">
        <Badge variant="secondary" className="px-3 py-1">Knowledge version v4.2.1</Badge>
        <Badge variant="success" className="px-3 py-1">412 chunks indexed</Badge>
        <Button variant="outline" size="sm">Reload index</Button>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-4">
        <Card className="lg:col-span-1">
          <CardHeader><CardTitle className="text-base">Categories</CardTitle></CardHeader>
          <CardContent className="space-y-1">
            {CATEGORIES.map((c) => (
              <div key={c.name} className="flex items-center justify-between rounded-lg px-3 py-2 text-sm hover:bg-accent">
                <span className="flex items-center gap-2"><BookOpen className="h-4 w-4 text-muted-foreground" />{c.name}</span>
                <Badge variant="secondary">{c.count}</Badge>
              </div>
            ))}
          </CardContent>
        </Card>

        <Card className="lg:col-span-3">
          <CardHeader>
            <div className="relative max-w-md">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search articles…" className="pl-9" />
            </div>
          </CardHeader>
          <CardContent className="space-y-2">
            {filtered.map((a) => (
              <div key={a.title} className="flex items-center gap-3 rounded-lg border border-border p-3 hover:bg-accent/40">
                <FileText className="h-4 w-4 text-primary" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{a.title}</p>
                  <p className="text-xs text-muted-foreground">{a.category} · updated {a.updated}</p>
                </div>
                <Button variant="ghost" size="sm">Edit</Button>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>
    </>
  );
}
