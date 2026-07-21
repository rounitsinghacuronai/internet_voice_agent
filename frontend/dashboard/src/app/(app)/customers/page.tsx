"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { Search } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { PageHeader } from "@/components/shared/page-header";
import { useCustomers } from "@/lib/hooks";
import { inr, initials } from "@/lib/utils";

export default function CustomersPage() {
  const [raw, setRaw] = useState("");
  const [q, setQ] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setQ(raw), 300);
    return () => clearTimeout(t);
  }, [raw]);

  const { data: customers, isLoading } = useCustomers(q);
  const rows = customers ?? [];

  return (
    <>
      <PageHeader title="Customers" description="Full subscriber base with plans, services and account health." />

      <Card className="p-4">
        <div className="relative mb-4 max-w-md">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input value={raw} onChange={(e) => setRaw(e.target.value)} placeholder="Search name, mobile, account, area…" className="pl-9" />
        </div>

        <div className="overflow-hidden rounded-lg border border-border">
          <Table>
            <TableHeader>
              <TableRow className="bg-muted/40 hover:bg-muted/40">
                <TableHead>Customer</TableHead>
                <TableHead>Account</TableHead>
                <TableHead>Service</TableHead>
                <TableHead>Plan</TableHead>
                <TableHead>Monthly</TableHead>
                <TableHead>Line</TableHead>
                <TableHead>Payment</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {isLoading
                ? Array.from({ length: 6 }).map((_, i) => (
                    <TableRow key={i}>{Array.from({ length: 7 }).map((__, j) => <TableCell key={j}><Skeleton className="h-5 w-full" /></TableCell>)}</TableRow>
                  ))
                : rows.map((c) => (
                    <TableRow key={c.account_no} className="cursor-pointer">
                      <TableCell>
                        <Link href={`/customers/${c.account_no}`} className="flex items-center gap-3">
                          <Avatar><AvatarFallback>{initials(c.name)}</AvatarFallback></Avatar>
                          <div>
                            <p className="text-sm font-medium text-foreground hover:text-primary">{c.name}</p>
                            <p className="text-xs text-muted-foreground">{c.mobile} · {c.address}</p>
                          </div>
                        </Link>
                      </TableCell>
                      <TableCell className="font-mono text-xs">{c.account_no}</TableCell>
                      <TableCell><Badge variant="secondary" className="capitalize">{c.service_type}</Badge></TableCell>
                      <TableCell className="max-w-[220px] truncate text-sm">{c.plan_name}</TableCell>
                      <TableCell className="text-sm font-medium">{inr(c.plan_price)}</TableCell>
                      <TableCell>
                        {c.ont_status === "LOS" ? <Badge variant="destructive">LOS</Badge> : c.ont_status ? <Badge variant="success">{c.ont_status}</Badge> : <span className="text-xs text-muted-foreground">—</span>}
                      </TableCell>
                      <TableCell>
                        <Badge variant={c.payment_status === "DUE" ? "warning" : "success"}>{c.payment_status}</Badge>
                      </TableCell>
                    </TableRow>
                  ))}
            </TableBody>
          </Table>
        </div>
      </Card>
    </>
  );
}
