"use client";
import { use, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, Check, Clock, CreditCard, Loader2, MapPin, Phone, PhoneIncoming, User, Wifi } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { PriorityBadge, StatusBadge } from "@/components/shared/badges";
import { useTicket, useTicketActions, useExecutives, useAuthUser } from "@/lib/hooks";
import { formatDateTime, inr } from "@/lib/utils";
import { can } from "@/lib/auth";

const STATUS_OPTIONS = ["OPEN", "PENDING", "SENT", "RESOLVED", "CLOSED"];

function Field({ label, value, icon }: { label: string; value?: string | number | null; icon?: React.ReactNode }) {
  const shown = value === undefined || value === null || value === "" ? "—" : value;
  return (
    <div>
      <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="mt-0.5 flex items-center gap-1.5 text-sm font-medium">{icon}{shown}</p>
    </div>
  );
}

export default function TicketDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data: t, isLoading } = useTicket(id);
  const { data: execs } = useExecutives();
  const { setStatus, assign, addNotes } = useTicketActions(id);
  const user = useAuthUser();
  const canWrite = can(user?.role, "ticket:write");
  const [notes, setNotes] = useState("");
  useEffect(() => { if (t?.resolution_notes) setNotes(t.resolution_notes); }, [t?.resolution_notes]);

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
            {canWrite && (
              <Button onClick={() => setStatus.mutate("RESOLVED")} disabled={setStatus.isPending}>
                {setStatus.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                Mark Resolved
              </Button>
            )}
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <Card className="lg:col-span-2">
              <CardHeader><CardTitle>Issue</CardTitle></CardHeader>
              <CardContent className="space-y-4">
                <p className="rounded-lg bg-muted/40 p-4 text-sm leading-relaxed">{t.summary || "No summary available."}</p>

                <div>
                  <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Customer</p>
                  <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
                    <Field label="Name" value={t.customer_name} icon={<User className="h-3.5 w-3.5 text-muted-foreground" />} />
                    <Field label="Account No" value={t.account_no} />
                    <Field label="Address" value={t.address ?? t.location} icon={<MapPin className="h-3.5 w-3.5 text-muted-foreground" />} />
                  </div>
                </div>

                <Separator />
                <div>
                  <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Contact</p>
                  <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
                    <Field label="Contact Number" value={t.mobile} icon={<Phone className="h-3.5 w-3.5 text-muted-foreground" />} />
                    <Field label="Call Receiving Number" value={t.receiving_number} icon={<PhoneIncoming className="h-3.5 w-3.5 text-muted-foreground" />} />
                    <Field label="Language" value={t.language} />
                  </div>
                </div>

                <Separator />
                <div>
                  <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Service &amp; Plan</p>
                  <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
                    <Field label="Service Type" value={t.service_type} icon={<Wifi className="h-3.5 w-3.5 text-muted-foreground" />} />
                    <Field label="Plan" value={t.plan_name} />
                    <Field label="Monthly" value={t.plan_price != null ? inr(t.plan_price) : "—"} icon={<CreditCard className="h-3.5 w-3.5 text-muted-foreground" />} />
                    <Field label="Payment Status" value={t.payment_status} />
                    <Field label="ONT / Line" value={t.ont_status} />
                    <Field label="Complaint No" value={t.complaint_no} />
                  </div>
                </div>

                {t.account_no && (
                  <Link href={`/customers/${t.account_no}`}>
                    <Button variant="outline" className="mt-2 w-full sm:w-auto">View full customer profile</Button>
                  </Link>
                )}
              </CardContent>
            </Card>

            <div className="space-y-4">
              {/* Actions — all wired to the backend, gated by role */}
              {canWrite && (
                <Card>
                  <CardHeader><CardTitle className="text-base">Actions</CardTitle></CardHeader>
                  <CardContent className="space-y-3">
                    <div>
                      <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Status</p>
                      <Select value={t.status?.toUpperCase()} onValueChange={(v) => setStatus.mutate(v)}>
                        <SelectTrigger><SelectValue /></SelectTrigger>
                        <SelectContent>{STATUS_OPTIONS.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}</SelectContent>
                      </Select>
                    </div>
                    <div>
                      <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Assigned Executive</p>
                      <Select value={t.assigned_executive || ""} onValueChange={(v) => assign.mutate(v)}>
                        <SelectTrigger><SelectValue placeholder="Unassigned" /></SelectTrigger>
                        <SelectContent>
                          {(execs ?? []).length === 0 && <SelectItem value="—" disabled>No executives — add in Executives</SelectItem>}
                          {(execs ?? []).map((e) => <SelectItem key={e.id} value={e.name}>{e.name} · {e.role}</SelectItem>)}
                        </SelectContent>
                      </Select>
                    </div>
                    <div>
                      <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Resolution Notes</p>
                      <Textarea value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Add notes…" />
                      <Button size="sm" className="mt-2 w-full" onClick={() => addNotes.mutate(notes)} disabled={addNotes.isPending}>
                        {addNotes.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : null} Save notes
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              )}

              <Card>
                <CardHeader><CardTitle className="text-base">Classification</CardTitle></CardHeader>
                <CardContent className="space-y-2 text-sm">
                  <div className="flex justify-between"><span className="text-muted-foreground">Category</span><span className="font-medium">{t.category || "—"}</span></div>
                  <div className="flex justify-between"><span className="text-muted-foreground">Raised by</span><Badge variant="secondary">{t.event_type?.includes("human") ? "Human" : "AI"}</Badge></div>
                  <div className="flex justify-between"><span className="text-muted-foreground">Call ID</span><span className="font-mono text-xs">{t.call_id || "—"}</span></div>
                </CardContent>
              </Card>

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
                </CardContent>
              </Card>
            </div>
          </div>
        </>
      )}
    </>
  );
}
