"use client";
import { Building2, Palette, ShieldCheck, Volume2 } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";
import { PageHeader } from "@/components/shared/page-header";
import { config } from "@/lib/config";

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

const ROLES = ["Super Admin", "Admin", "Supervisor", "Executive", "Viewer"];

export default function SettingsPage() {
  return (
    <>
      <PageHeader title="Settings" description="Company, branding, voice behaviour and role-based access." actions={<Button>Save changes</Button>} />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle className="flex items-center gap-2 text-base"><Building2 className="h-4 w-4 text-primary" /> Company</CardTitle></CardHeader>
          <CardContent className="divide-y divide-border">
            <Row label="Company name"><Input defaultValue={config.brand.name} /></Row>
            <Row label="Business hours"><Input defaultValue="09:00 – 21:00 IST" /></Row>
            <Row label="Languages" hint="Supported voice languages"><div className="flex flex-wrap gap-1"><Badge variant="secondary">Hindi</Badge><Badge variant="secondary">Marathi</Badge><Badge variant="secondary">English</Badge></div></Row>
            <Row label="Executive transfer number"><Input defaultValue="+91 20 4000 0000" /></Row>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle className="flex items-center gap-2 text-base"><Palette className="h-4 w-4 text-primary" /> Branding</CardTitle></CardHeader>
          <CardContent className="divide-y divide-border">
            <Row label="Brand color"><div className="flex items-center gap-2"><span className="h-8 w-8 rounded-lg bg-primary" /><Input defaultValue="#3b82f6" className="font-mono" /></div></Row>
            <Row label="Logo" hint="PNG or SVG, max 1MB"><Button variant="outline" className="w-full">Upload logo</Button></Row>
            <Row label="Dark mode default"><Switch defaultChecked /></Row>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle className="flex items-center gap-2 text-base"><Volume2 className="h-4 w-4 text-primary" /> AI &amp; Voice</CardTitle></CardHeader>
          <CardContent className="divide-y divide-border">
            <Row label="Voice speed (TTS pace)" hint="Currently applied to the live agent"><div className="flex items-center gap-2"><Input defaultValue="1.3" className="font-mono" /><Badge variant="success">Active</Badge></div></Row>
            <Row label="Prompt version"><Input defaultValue="prompt-v11-multilingual" className="font-mono" /></Row>
            <Row label="Knowledge base version"><Input defaultValue="v4.2.1" className="font-mono" /></Row>
            <Row label="Barge-in (interruptions)"><Switch defaultChecked /></Row>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle className="flex items-center gap-2 text-base"><ShieldCheck className="h-4 w-4 text-primary" /> Role-Based Access</CardTitle></CardHeader>
          <CardContent>
            <p className="mb-3 text-sm text-muted-foreground">Configure permissions per role. Changes are audit-logged.</p>
            <div className="space-y-2">
              {ROLES.map((r, i) => (
                <div key={r} className="flex items-center justify-between rounded-lg border border-border px-3 py-2">
                  <span className="text-sm font-medium">{r}</span>
                  <div className="flex items-center gap-2">
                    <Badge variant="secondary">{[42, 28, 19, 12, 5][i]} permissions</Badge>
                    <Button variant="ghost" size="sm">Edit</Button>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
    </>
  );
}
