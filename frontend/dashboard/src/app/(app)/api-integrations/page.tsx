"use client";
import { CreditCard, Mail, MessageCircle, Phone, Plug, Receipt, Users2, Webhook } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { PageHeader } from "@/components/shared/page-header";

const INTEGRATIONS = [
  { name: "Exotel", desc: "Telephony & call transfer", icon: Phone, status: "connected" },
  { name: "WhatsApp", desc: "Ops ticket notifications", icon: MessageCircle, status: "connected" },
  { name: "CRM", desc: "Customer records sync", icon: Users2, status: "available" },
  { name: "SMS Gateway", desc: "OTP & alerts", icon: MessageCircle, status: "available" },
  { name: "Email", desc: "Transactional email", icon: Mail, status: "available" },
  { name: "Payment Gateway", desc: "Bill payments & recharge", icon: CreditCard, status: "available" },
  { name: "Billing System", desc: "Invoice & plan sync", icon: Receipt, status: "available" },
  { name: "Custom Webhooks", desc: "Outbound event stream", icon: Webhook, status: "available" },
];

export default function ApiIntegrationsPage() {
  return (
    <>
      <PageHeader
        title="API Integrations"
        description="Modular connectors — each isolated behind a service interface for easy extension."
        actions={<Button className="gap-2"><Plug className="h-4 w-4" /> Add Integration</Button>}
      />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {INTEGRATIONS.map((it) => {
          const Icon = it.icon;
          const connected = it.status === "connected";
          return (
            <Card key={it.name}>
              <CardContent className="p-5">
                <div className="flex items-start justify-between">
                  <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-primary/10 text-primary"><Icon className="h-5 w-5" /></div>
                  <Badge variant={connected ? "success" : "secondary"}>{connected ? "Connected" : "Available"}</Badge>
                </div>
                <p className="mt-3 font-semibold">{it.name}</p>
                <p className="text-sm text-muted-foreground">{it.desc}</p>
                <div className="mt-4 flex items-center justify-between">
                  <div className="flex items-center gap-2 text-xs text-muted-foreground"><Switch defaultChecked={connected} /> {connected ? "Enabled" : "Disabled"}</div>
                  <Button variant="ghost" size="sm">Configure</Button>
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>
    </>
  );
}
