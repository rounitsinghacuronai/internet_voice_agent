"use client";
import { useEffect, useState } from "react";
import { BookOpen, Loader2, RefreshCw, Search } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/shared/page-header";
import { useKbSearch, useKbReload, useSystemHealth, useAuthUser } from "@/lib/hooks";
import { can } from "@/lib/auth";

const CATEGORIES = ["FAQs", "Plans", "Broadband", "SIM", "Billing", "Policies", "Troubleshooting"];

export default function KnowledgeBasePage() {
  const [raw, setRaw] = useState("");
  const [q, setQ] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setQ(raw), 350);
    return () => clearTimeout(t);
  }, [raw]);
  const { data: results, isFetching } = useKbSearch(q);
  const reload = useKbReload();
  const { data: health } = useSystemHealth();
  const user = useAuthUser();
  const canReload = can(user?.role, "kb:reload");
  const kbComp = health?.components.find((c) => c.name === "Knowledge Base");

  return (
    <>
      <PageHeader
        title="Knowledge Base"
        description="Grounding content the AI retrieves from. Search runs against the live index."
        actions={
          canReload ? (
            <Button variant="outline" className="gap-2" onClick={() => reload.mutate()} disabled={reload.isPending}>
              {reload.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
              {reload.isSuccess ? `Reloaded (${reload.data?.chunks})` : "Reload index"}
            </Button>
          ) : null
        }
      />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Badge variant="success" className="px-3 py-1">{kbComp?.detail ?? "index loaded"}</Badge>
        {CATEGORIES.map((c) => <Badge key={c} variant="secondary" className="px-3 py-1">{c}</Badge>)}
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base"><BookOpen className="h-4 w-4 text-primary" /> Search the knowledge base</CardTitle>
          <div className="relative mt-2 max-w-lg">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input value={raw} onChange={(e) => setRaw(e.target.value)} placeholder="e.g. LOS red light, porting, plan upgrade…" className="pl-9" />
          </div>
        </CardHeader>
        <CardContent className="space-y-2">
          {isFetching && <p className="py-4 text-center text-sm text-muted-foreground"><Loader2 className="mx-auto h-4 w-4 animate-spin" /></p>}
          {!isFetching && q.length >= 2 && (results ?? []).length === 0 && (
            <p className="py-6 text-center text-sm text-muted-foreground">No matches for “{q}”.</p>
          )}
          {(results ?? []).map((r, i) => (
            <div key={i} className="rounded-lg border border-border p-3">
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium">{String(r.title ?? r.category ?? "Result")}</span>
                {typeof r.score === "number" && <Badge variant="secondary">score {r.score.toFixed(2)}</Badge>}
              </div>
              {r.text && <p className="mt-1 line-clamp-3 text-sm text-muted-foreground">{String(r.text)}</p>}
            </div>
          ))}
          {q.length < 2 && <p className="py-6 text-center text-sm text-muted-foreground">Type at least 2 characters to search.</p>}
        </CardContent>
      </Card>
    </>
  );
}
