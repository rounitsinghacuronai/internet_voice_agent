"use client";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Bell, Menu, Search, Ticket, User } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useSearch } from "@/lib/hooks";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Sheet, SheetContent, SheetTrigger } from "@/components/ui/sheet";
import { ThemeToggle } from "./theme-toggle";
import { Sidebar } from "./sidebar";
import { useNotifications, useAuthUser } from "@/lib/hooks";
import { formatRelativeTime, initials } from "@/lib/utils";
import { cn } from "@/lib/utils";
import { clearToken, ROLE_LABELS } from "@/lib/auth";

function GlobalSearch() {
  const router = useRouter();
  const [raw, setRaw] = useState("");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const t = setTimeout(() => setQ(raw), 250);
    return () => clearTimeout(t);
  }, [raw]);
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);
  const { data: results, isFetching } = useSearch(q);

  return (
    <div ref={boxRef} className="relative hidden max-w-md flex-1 md:block">
      <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
      <Input
        value={raw}
        onChange={(e) => { setRaw(e.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)}
        placeholder="Search customers, tickets, phone…"
        className="pl-9"
        aria-label="Global search"
      />
      {open && q.trim().length >= 2 && (
        <div className="absolute left-0 right-0 top-11 z-50 overflow-hidden rounded-lg border border-border bg-popover shadow-lg">
          {isFetching && <div className="px-3 py-3 text-sm text-muted-foreground">Searching…</div>}
          {!isFetching && (results ?? []).length === 0 && <div className="px-3 py-3 text-sm text-muted-foreground">No results.</div>}
          {(results ?? []).map((r, i) => (
            <button
              key={i}
              onClick={() => { setOpen(false); setRaw(""); router.push(r.href); }}
              className="flex w-full items-center gap-3 px-3 py-2 text-left text-sm hover:bg-accent"
            >
              <span className={r.type === "ticket" ? "text-warning" : "text-primary"}>
                {r.type === "ticket" ? <Ticket className="h-4 w-4" /> : <User className="h-4 w-4" />}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium">{r.title}</span>
                <span className="block truncate text-xs text-muted-foreground">{r.subtitle}</span>
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function Topbar() {
  const { data: notifications } = useNotifications();
  const user = useAuthUser();
  const unread = (notifications ?? []).filter((n) => !n.read).length;

  const logout = () => {
    clearToken();
    const base = process.env.NEXT_PUBLIC_BASE_PATH || "";
    window.location.href = `${base}/login`;
  };

  return (
    <header className="sticky top-0 z-30 flex h-16 items-center gap-3 border-b border-border bg-background/80 px-4 backdrop-blur">
      <Sheet>
        <SheetTrigger asChild>
          <Button variant="ghost" size="icon" className="lg:hidden">
            <Menu className="h-5 w-5" />
          </Button>
        </SheetTrigger>
        <SheetContent side="left" className="w-64 p-0">
          <Sidebar />
        </SheetContent>
      </Sheet>

      <GlobalSearch />

      <div className="ml-auto flex items-center gap-1">
        <ThemeToggle />

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" className="relative" aria-label="Notifications">
              <Bell className="h-4 w-4" />
              {unread > 0 && (
                <span className="absolute right-1.5 top-1.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] font-semibold text-destructive-foreground">
                  {unread}
                </span>
              )}
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-80">
            <DropdownMenuLabel>Notifications</DropdownMenuLabel>
            <DropdownMenuSeparator />
            {(notifications ?? []).slice(0, 5).map((n) => (
              <DropdownMenuItem key={n.id} className="flex-col items-start gap-0.5 py-2">
                <div className="flex w-full items-center gap-2">
                  <span
                    className={cn(
                      "h-2 w-2 rounded-full",
                      n.type === "critical" ? "bg-destructive" : n.type === "warning" ? "bg-warning" : "bg-primary",
                    )}
                  />
                  <span className="text-sm font-medium">{n.title}</span>
                  <span className="ml-auto text-[11px] text-muted-foreground">{formatRelativeTime(n.created_at)}</span>
                </div>
                <span className="pl-4 text-xs text-muted-foreground">{n.body}</span>
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" className="gap-2 px-2">
              <Avatar>
                <AvatarFallback>{initials(user?.name || user?.username || "?")}</AvatarFallback>
              </Avatar>
              <div className="hidden text-left leading-tight sm:block">
                <div className="text-sm font-medium">{user?.name || user?.username || "—"}</div>
                <div className="text-[11px] text-muted-foreground">{user ? ROLE_LABELS[user.role] : ""}</div>
              </div>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-52">
            <DropdownMenuLabel>{user?.name || user?.username || "My account"}</DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem disabled className="text-xs">Role: {user ? ROLE_LABELS[user.role] : "—"}</DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem className="text-destructive" onClick={logout}>Sign out</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
