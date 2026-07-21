"use client";
import { use } from "react";
import Link from "next/link";
import { ArrowLeft, Clock, MapPin, Phone, User } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { PriorityBadge, StatusBadge } from "@/components/shared/badges";
import { useTicket } from "@/lib/hooks";
import { formatDateTime } from "@/lib/utils";

function Field({ label, value, icon }: { label: string; value?: string | null; icon?: React.ReactNode }) {
  return (
    <div>
      <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="mt-0.5 flex items-center gap-1.5 text-sm font-medium">{icon}{value || "—"}</p>
    </div>
  );
}

export default function TicketDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data: t, isLoading } = useTicket(id);

  return (
    <>
      <Link href="/tickets" className="mb-4 inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-4 w-4" /> Back to tickets
      </Link>

      {isLoading ? (
        <Skeleton className="h-64 w-full" />
      ) : !t ? (
        <Card><CardContent className="py-10 text-center text-sm text-muted-foreground">Ticket not found.</CardContent></Card>
      ) : (
        <>
          <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <div className="flex items-center gap-3">
                <h1 className="font-mono text-2xl font-bold tracking-tight">{t.ticket_id}</h1>
                <PriorityBadge priority={t.priority} />
                <StatusBadge status={t.status} />
              </div>
              <p className="mt-1 text-sm text-muted-foreground">{t.category}</p>
            </div>
            <div className="flex gap-2">
              <Button variant="outline">Reassign</Button>
              <Button>Resolve</Button>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <Card className="lg:col-span-2">
              <CardHeader><CardTitle>Issue Summary</CardTitle></CardHeader>
              <CardContent className="space-y-4">
                <p className="rounded-lg bg-muted/40 p-4 text-sm leading-relaxed">{t.summary || "No summary available."}</p>
                <Separator />
                <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
                  <Field label="Customer" value={t.customer_name} icon={<User className="h-3.5 w-3.5 text-muted-foreground" />} />
                  <Field label="Phone" value={t.mobile} icon={<Phone className="h-3.5 w-3.5 text-muted-foreground" />} />
                  <Field label="Account" value={t.account_no} />
                  <Field label="Region" value={t.region} icon={<MapPin className="h-3.5 w-3.5 text-muted-foreground" />} />
                  <Field label="Service" value={t.service_type} />
                  <Field label="Language" value={t.language} />
                  <Field label="Raised by" value={t.raised_by ?? (t.event_type.includes("escal") ? "AI" : "AI")} />
                  <Field label="Complaint No" value={t.complaint_no} />
                  <Field label="Assigned" value={t.assigned_executive} />
                </div>
                {t.resolution_notes && (
                  <>
                    <Separator />
                    <div>
                      <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Resolution Notes</p>
                      <p className="mt-1 text-sm">{t.resolution_notes}</p>
                    </div>
                  </>
                )}
              </CardContent>
            </Card>

            <div className="space-y-4">
              <Card>
                <CardHeader><CardTitle className="text-base">Timeline</CardTitle></CardHeader>
                <CardContent className="space-y-3 text-sm">
                  <div className="flex items-center gap-2">
                    <Clock className="h-3.5 w-3.5 text-muted-foreground" />
                    <span className="text-muted-foreground">Created</span>
                    <span className="ml-auto font-medium">{formatDateTime(t.created_at)}</span>
                  </div>
                  {t.delivered_at && (
                    <div className="flex items-center gap-2">
                      <Clock className="h-3.5 w-3.5 text-muted-foreground" />
                      <span className="text-muted-foreground">Notified</span>
                      <span className="ml-auto font-medium">{formatDateTime(t.delivered_at)}</span>
                    </div>
                  )}
                  {t.sla_due_at && (
                    <div className="flex items-center gap-2">
                      <Clock className="h-3.5 w-3.5 text-muted-foreground" />
                      <span className="text-muted-foreground">SLA due</span>
                      <span className="ml-auto font-medium">{formatDateTime(t.sla_due_at)}</span>
                    </div>
                  )}
                </CardContent>
              </Card>

              <Card>
                <CardHeader><CardTitle className="text-base">Delivery</CardTitle></CardHeader>
                <CardContent className="space-y-2 text-sm">
                  <div className="flex justify-between"><span className="text-muted-foreground">Attempts</span><span className="font-medium">{t.attempts}</span></div>
                  <div className="flex justify-between"><span className="text-muted-foreground">Follow-ups</span><span className="font-medium">{t.follow_up_count}</span></div>
                  <div className="flex justify-between"><span className="text-muted-foreground">Call ID</span><span className="font-mono text-xs">{t.call_id}</span></div>
                  {t.last_error && <div className="rounded-md bg-destructive/10 p-2 text-xs text-destructive">{t.last_error}</div>}
                </CardContent>
              </Card>

              {t.account_no && (
                <Link href={`/customers/${t.account_no}`}>
                  <Button variant="outline" className="w-full">View customer profile</Button>
                </Link>
              )}
            </div>
          </div>
        </>
      )}
    </>
  );
}
