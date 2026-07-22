"use client";
import { useParams } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, BadgeCheck, MapPin, Phone, ShieldCheck, Wifi } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { StatusBadge, PriorityBadge } from "@/components/shared/badges";
import { useCustomerProfile } from "@/lib/hooks";
import { inr, initials, formatRelativeTime } from "@/lib/utils";

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-border p-3">
      <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="mt-0.5 text-sm font-semibold">{value}</p>
    </div>
  );
}

export default function CustomerProfilePage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const { data, isLoading } = useCustomerProfile(id);

  if (isLoading) return <Skeleton className="h-96 w-full" />;
  if (!data) return <Card><CardContent className="py-10 text-center text-sm text-muted-foreground">Customer not found.</CardContent></Card>;

  const c = data.customer;
  const bill = data.bill as { amount_rs?: number; due_date?: string };
  const usage = data.usage as { cycle_data_used_gb?: number; data_used_today_gb?: number };

  return (
    <>
      <Link href="/customers" className="mb-4 inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-4 w-4" /> Back to customers
      </Link>

      <Card className="mb-6">
        <CardContent className="flex flex-col gap-4 p-6 sm:flex-row sm:items-center">
          <Avatar className="h-16 w-16 text-lg"><AvatarFallback>{initials(c.name)}</AvatarFallback></Avatar>
          <div className="flex-1">
            <div className="flex items-center gap-2">
              <h1 className="text-xl font-bold">{c.name}</h1>
              {data.verification_status === "VERIFIED" && (
                <Badge variant="success" className="gap-1"><ShieldCheck className="h-3 w-3" /> Verified</Badge>
              )}
            </div>
            <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted-foreground">
              <span className="flex items-center gap-1"><Phone className="h-3.5 w-3.5" /> {c.mobile}</span>
              <span className="flex items-center gap-1"><MapPin className="h-3.5 w-3.5" /> {c.address}</span>
              <span className="flex items-center gap-1"><BadgeCheck className="h-3.5 w-3.5" /> {c.account_no}</span>
            </div>
          </div>
          <div className="flex gap-2">
            <Badge variant="secondary" className="capitalize">{c.service_type}</Badge>
            <Badge variant={c.payment_status === "DUE" ? "warning" : "success"}>{c.payment_status}</Badge>
          </div>
        </CardContent>
      </Card>

      <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Plan" value={<span className="line-clamp-1">{c.plan_name}</span>} />
        <Stat label="Monthly" value={inr(c.plan_price)} />
        <Stat label="Outstanding" value={bill?.amount_rs ? inr(bill.amount_rs) : "—"} />
        <Stat label="Data Used" value={usage?.cycle_data_used_gb ? `${usage.cycle_data_used_gb} GB` : usage?.data_used_today_gb ? `${usage.data_used_today_gb} GB` : "—"} />
      </div>

      <Tabs defaultValue="tickets">
        <TabsList>
          <TabsTrigger value="tickets">Tickets ({data.tickets.length})</TabsTrigger>
          <TabsTrigger value="complaints">Complaints ({data.complaints.length})</TabsTrigger>
          <TabsTrigger value="services">Services</TabsTrigger>
        </TabsList>

        <TabsContent value="tickets">
          <Card>
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow className="bg-muted/40 hover:bg-muted/40">
                    <TableHead>Ticket</TableHead><TableHead>Category</TableHead><TableHead>Priority</TableHead><TableHead>Status</TableHead><TableHead>Created</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.tickets.length === 0 ? (
                    <TableRow><TableCell colSpan={5} className="py-8 text-center text-sm text-muted-foreground">No tickets for this customer.</TableCell></TableRow>
                  ) : data.tickets.map((t) => (
                    <TableRow key={t.ticket_id}>
                      <TableCell><Link href={`/tickets/${t.ticket_id}`} className="font-mono text-xs text-primary hover:underline">{t.ticket_id}</Link></TableCell>
                      <TableCell className="text-sm">{t.category}</TableCell>
                      <TableCell><PriorityBadge priority={t.priority} /></TableCell>
                      <TableCell><StatusBadge status={t.status} /></TableCell>
                      <TableCell className="text-xs text-muted-foreground">{formatRelativeTime(t.created_at)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="complaints">
          <Card>
            <CardContent className="space-y-3 p-5">
              {data.complaints.length === 0 ? (
                <p className="py-6 text-center text-sm text-muted-foreground">No complaint history.</p>
              ) : data.complaints.map((c2) => (
                <div key={c2.ticket_no} className="rounded-lg border border-border p-3">
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-xs text-primary">{c2.ticket_no}</span>
                    <Badge variant="secondary">{c2.status}</Badge>
                  </div>
                  <p className="mt-1 text-sm font-medium">{c2.category}</p>
                  <p className="text-xs text-muted-foreground">{c2.description}</p>
                </div>
              ))}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="services">
          <Card>
            <CardHeader><CardTitle className="flex items-center gap-2 text-base"><Wifi className="h-4 w-4 text-primary" /> Current Services</CardTitle></CardHeader>
            <CardContent className="grid grid-cols-2 gap-3 md:grid-cols-3">
              <Stat label="Service Type" value={<span className="capitalize">{c.service_type}</span>} />
              <Stat label="ONT Status" value={c.ont_status || "N/A"} />
              {data.broadband && <Stat label="Line State" value={String((data.broadband as { line_state?: string }).line_state ?? "—")} />}
              {data.broadband && <Stat label="Sync Speed" value={`${(data.broadband as { last_sync_mbps?: number }).last_sync_mbps ?? 0} Mbps`} />}
              <Stat label="Verification" value={data.verification_status} />
              <Stat label="Payment" value={c.payment_status} />
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </>
  );
}
