import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

export function PriorityBadge({ priority }: { priority: string }) {
  const p = (priority || "").toUpperCase();
  const map: Record<string, string> = {
    CRITICAL: "bg-destructive/15 text-destructive",
    HIGH: "bg-warning/15 text-warning",
    MEDIUM: "bg-primary/10 text-primary",
    LOW: "bg-muted text-muted-foreground",
  };
  return <Badge className={cn("font-semibold", map[p] || map.LOW)}>{p || "—"}</Badge>;
}

export function StatusBadge({ status }: { status: string }) {
  const s = (status || "").toUpperCase();
  const good = ["SENT", "RESOLVED", "CLOSED"];
  const warn = ["PENDING", "RETRYING"];
  const bad = ["FAILED"];
  const variant = good.includes(s) ? "success" : warn.includes(s) ? "warning" : bad.includes(s) ? "destructive" : "secondary";
  return <Badge variant={variant}>{s || "OPEN"}</Badge>;
}

export function SentimentDot({ sentiment }: { sentiment: string }) {
  const color =
    sentiment === "positive" ? "bg-success" : sentiment === "negative" ? "bg-destructive" : "bg-warning";
  return (
    <span className="inline-flex items-center gap-1.5 text-sm capitalize">
      <span className={cn("h-2 w-2 rounded-full", color)} />
      {sentiment}
    </span>
  );
}
