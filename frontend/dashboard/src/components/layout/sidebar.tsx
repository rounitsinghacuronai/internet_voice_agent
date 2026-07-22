"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { navSections } from "./nav";
import { LogoMark } from "./logo";
import { cn } from "@/lib/utils";
import { config } from "@/lib/config";
import { useTickets, useLiveCalls } from "@/lib/hooks";

function SidebarBadge({ kind }: { kind: "critical" | "live" }) {
  const { data: tickets } = useTickets();
  const { data: calls } = useLiveCalls();
  const count =
    kind === "critical"
      ? (tickets ?? []).filter((t) => ["CRITICAL", "HIGH"].includes((t.priority || "").toUpperCase()) && !["SENT", "RESOLVED", "CLOSED"].includes((t.status || "").toUpperCase())).length
      : (calls ?? []).length;
  if (!count) return null;
  return (
    <span
      className={cn(
        "ml-auto inline-flex h-5 min-w-5 items-center justify-center rounded-full px-1.5 text-[11px] font-semibold",
        kind === "critical" ? "bg-destructive text-destructive-foreground" : "bg-success text-success-foreground animate-pulse-ring",
      )}
    >
      {count}
    </span>
  );
}

export function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();

  return (
    <aside className="flex h-full w-64 flex-col bg-sidebar text-sidebar-foreground">
      <div className="flex h-16 items-center gap-3 px-5">
        <div className="flex h-10 w-10 items-center justify-center overflow-hidden rounded-xl bg-white p-1 shadow-sm">
          <LogoMark className="h-full w-full object-contain" />
        </div>
        <div className="leading-tight">
          <div className="text-sm font-bold">{config.brand.shortName}</div>
          <div className="text-[11px] text-sidebar-foreground/60">{config.brand.product}</div>
        </div>
      </div>

      <nav className="flex-1 space-y-6 overflow-y-auto px-3 py-4">
        {navSections.map((section) => (
          <div key={section.title}>
            <div className="px-3 pb-2 text-[11px] font-semibold uppercase tracking-wider text-sidebar-foreground/40">
              {section.title}
            </div>
            <div className="space-y-0.5">
              {section.items.map((item) => {
                const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
                const Icon = item.icon;
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    onClick={onNavigate}
                    className={cn(
                      "group flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                      active
                        ? "bg-primary/15 text-white"
                        : "text-sidebar-foreground/70 hover:bg-white/5 hover:text-white",
                    )}
                  >
                    <Icon className={cn("h-4 w-4 shrink-0", active ? "text-primary" : "")} />
                    <span className="truncate">{item.label}</span>
                    {item.badgeKey && <SidebarBadge kind={item.badgeKey} />}
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </nav>

      <div className="border-t border-white/5 p-4">
        <div className="flex items-center gap-2 rounded-lg bg-white/5 px-3 py-2 text-xs">
          <span className="h-2 w-2 rounded-full bg-success animate-pulse-ring" />
          <span className="text-sidebar-foreground/70">All systems operational</span>
        </div>
      </div>
    </aside>
  );
}
