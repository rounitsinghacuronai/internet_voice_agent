"use client";
import { useEffect, useState } from "react";
import { Building2, Check, Loader2, Palette, ShieldCheck, Volume2 } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { PageHeader } from "@/components/shared/page-header";
import { useSettings, useSaveSettings } from "@/lib/hooks";
import type { AdminSettings } from "@/lib/api/types";

function Row({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <p className="text-sm font-medium">{label}</p>
        {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      </div>
      <div className="sm:w-72">{children}</div>
    </div>
  );
}

export default function SettingsPage() {
  const { data } = useSettings();
  const save = useSaveSettings();
  const [form, setForm] = useState<AdminSettings | null>(null);
  useEffect(() => { if (data && !form) setForm(data); }, [data, form]);

  if (!form) return <PageHeader title="Settings" description="Loading…" />;
  const set = <K extends keyof AdminSettings>(k: K, v: AdminSettings[K]) => setForm({ ...form, [k]: v });

  return (
    <>
      <PageHeader
        title="Settings"
        description="Company, branding, voice behaviour — persisted and applied to the live agent."
        actions={
          <Button onClick={() => save.mutate(form)} disabled={save.isPending} className="gap-2">
            {save.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : save.isSuccess ? <Check className="h-4 w-4" /> : null}
            {save.isSuccess ? "Saved" : "Save changes"}
          </Button>
        }
      />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle className="flex items-center gap-2 text-base"><Building2 className="h-4 w-4 text-primary" /> Company</CardTitle></CardHeader>
          <CardContent className="divide-y divide-border">
            <Row label="Company name"><Input value={form.company_name} onChange={(e) => set("company_name", e.target.value)} /></Row>
            <Row label="Business hours"><Input value={form.business_hours} onChange={(e) => set("business_hours", e.target.value)} /></Row>
            <Row label="Languages"><div className="flex flex-wrap gap-1">{form.languages.map((l) => <Badge key={l} variant="secondary">{l}</Badge>)}</div></Row>
            <Row label="Executive transfer number"><Input value={form.executive_transfer_number} onChange={(e) => set("executive_transfer_number", e.target.value)} placeholder="+91…" /></Row>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle className="flex items-center gap-2 text-base"><Palette className="h-4 w-4 text-primary" /> Branding</CardTitle></CardHeader>
          <CardContent className="divide-y divide-border">
            <Row label="Brand color"><div className="flex items-center gap-2"><span className="h-8 w-8 rounded-lg border border-border" style={{ background: form.brand_color }} /><Input value={form.brand_color} onChange={(e) => set("brand_color", e.target.value)} className="font-mono" /></div></Row>
            <Row label="Dark mode default"><Switch checked={form.dark_mode_default} onCheckedChange={(v) => set("dark_mode_default", v)} /></Row>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle className="flex items-center gap-2 text-base"><Volume2 className="h-4 w-4 text-primary" /> AI &amp; Voice</CardTitle></CardHeader>
          <CardContent className="divide-y divide-border">
            <Row label="Voice speed (TTS pace)" hint="Applied to the running agent on save">
              <div className="flex items-center gap-2">
                <Input type="number" step="0.1" min="0.7" max="1.5" value={form.voice_pace} onChange={(e) => set("voice_pace", parseFloat(e.target.value))} className="font-mono" />
                <Badge variant="success">Live</Badge>
              </div>
            </Row>
            <Row label="Prompt version"><Input value={form.prompt_version} onChange={(e) => set("prompt_version", e.target.value)} className="font-mono" /></Row>
            <Row label="Knowledge base version"><Input value={form.kb_version} onChange={(e) => set("kb_version", e.target.value)} className="font-mono" /></Row>
            <Row label="Barge-in (interruptions)"><Switch checked={form.barge_in} onCheckedChange={(v) => set("barge_in", v)} /></Row>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle className="flex items-center gap-2 text-base"><ShieldCheck className="h-4 w-4 text-primary" /> Role-Based Access</CardTitle></CardHeader>
          <CardContent>
            <p className="mb-3 text-sm text-muted-foreground">Roles available in the console. Manage staff in the Executives panel.</p>
            <div className="space-y-2">
              {["Super Admin", "Admin", "Supervisor", "Executive", "Viewer"].map((r, i) => (
                <div key={r} className="flex items-center justify-between rounded-lg border border-border px-3 py-2">
                  <span className="text-sm font-medium">{r}</span>
                  <Badge variant="secondary">{[42, 28, 19, 12, 5][i]} permissions</Badge>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
    </>
  );
}
