"use client";
import { useState } from "react";
import { Loader2, Plus, Trash2, UserPlus } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { PageHeader, EmptyState } from "@/components/shared/page-header";
import { useExecutives, useExecutiveMutations, useAuthUser } from "@/lib/hooks";
import { initials, cn } from "@/lib/utils";
import { can } from "@/lib/auth";

const STATUS = { available: "bg-success", busy: "bg-warning", offline: "bg-muted-foreground" } as const;
const ROLES = ["Super Admin", "Admin", "Supervisor", "Executive", "Viewer"];

export default function ExecutivesPage() {
  const { data: execs, isLoading } = useExecutives();
  const { create, update, remove } = useExecutiveMutations();
  const user = useAuthUser();
  const canManage = can(user?.role, "exec:manage");
  const [form, setForm] = useState({ name: "", phone: "", email: "", role: "Executive", status: "available" as const });
  const rows = execs ?? [];

  const submit = () => {
    if (!form.name.trim()) return;
    create.mutate(form, { onSuccess: () => setForm({ name: "", phone: "", email: "", role: "Executive", status: "available" }) });
  };

  return (
    <>
      <PageHeader title="Executive Panel" description="Manage your support team — add, edit availability, assign to tickets." />

      {canManage && (
      <Card className="mb-6">
        <CardContent className="p-4">
          <div className="flex flex-col gap-2 md:flex-row md:items-end">
            <div className="flex-1"><label className="text-xs text-muted-foreground">Name</label><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Full name" /></div>
            <div className="flex-1"><label className="text-xs text-muted-foreground">Phone</label><Input value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} placeholder="+91…" /></div>
            <div className="flex-1"><label className="text-xs text-muted-foreground">Email</label><Input value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} placeholder="name@company" /></div>
            <div className="w-40"><label className="text-xs text-muted-foreground">Role</label>
              <Select value={form.role} onValueChange={(v) => setForm({ ...form, role: v })}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>{ROLES.map((r) => <SelectItem key={r} value={r}>{r}</SelectItem>)}</SelectContent>
              </Select>
            </div>
            <Button onClick={submit} disabled={create.isPending} className="gap-2">
              {create.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />} Add
            </Button>
          </div>
        </CardContent>
      </Card>
      )}

      {!isLoading && rows.length === 0 ? (
        <EmptyState icon={<UserPlus className="h-6 w-6" />} title="No executives yet" description="Add your first team member using the form above." />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {rows.map((e) => (
            <Card key={e.id}>
              <CardContent className="p-5">
                <div className="flex items-center gap-3">
                  <div className="relative">
                    <Avatar className="h-11 w-11"><AvatarFallback>{initials(e.name)}</AvatarFallback></Avatar>
                    <span className={cn("absolute -bottom-0.5 -right-0.5 h-3 w-3 rounded-full border-2 border-card", STATUS[e.status] ?? "bg-muted-foreground")} />
                  </div>
                  <div className="flex-1">
                    <p className="font-semibold">{e.name}</p>
                    <p className="text-xs text-muted-foreground">{e.role}</p>
                  </div>
                  {canManage && (
                    <Button variant="ghost" size="icon" onClick={() => remove.mutate(e.id)} aria-label="Remove">
                      <Trash2 className="h-4 w-4 text-destructive" />
                    </Button>
                  )}
                </div>
                <div className="mt-3 space-y-1 text-xs text-muted-foreground">
                  {e.phone && <p>{e.phone}</p>}
                  {e.email && <p>{e.email}</p>}
                </div>
                {canManage && (
                  <div className="mt-3">
                    <Select value={e.status} onValueChange={(v) => update.mutate({ id: e.id, e: { name: e.name, phone: e.phone, email: e.email, role: e.role, status: v as typeof e.status } })}>
                      <SelectTrigger className="h-8"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="available">Available</SelectItem>
                        <SelectItem value="busy">On Call</SelectItem>
                        <SelectItem value="offline">Offline</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                )}
                <Badge className={cn("mt-3 gap-1", e.status === "available" ? "bg-success/15 text-success" : e.status === "busy" ? "bg-warning/15 text-warning" : "bg-muted text-muted-foreground")}>
                  <span className={cn("h-1.5 w-1.5 rounded-full", STATUS[e.status] ?? "bg-muted-foreground")} />
                  {e.status}
                </Badge>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </>
  );
}
