import {
  Activity,
  BadgeCheck,
  BarChart3,
  BookOpen,
  Building2,
  FileClock,
  Headphones,
  LayoutDashboard,
  MessageSquareText,
  PhoneCall,
  Plug,
  Settings,
  Ticket,
  Users,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  label: string;
  href: string;
  icon: LucideIcon;
  badgeKey?: "critical" | "live";
}

export interface NavSection {
  title: string;
  items: NavItem[];
}

export const navSections: NavSection[] = [
  {
    title: "Operations",
    items: [
      { label: "Dashboard", href: "/", icon: LayoutDashboard },
      { label: "Live Calls", href: "/live-calls", icon: PhoneCall, badgeKey: "live" },
      { label: "Tickets", href: "/tickets", icon: Ticket, badgeKey: "critical" },
      { label: "Customers", href: "/customers", icon: Users },
    ],
  },
  {
    title: "Intelligence",
    items: [
      { label: "AI Conversations", href: "/ai-conversations", icon: MessageSquareText },
      { label: "Human Escalations", href: "/human-escalations", icon: Headphones },
      { label: "Knowledge Base", href: "/knowledge-base", icon: BookOpen },
      { label: "Analytics", href: "/analytics", icon: BarChart3 },
    ],
  },
  {
    title: "Team",
    items: [{ label: "Executives", href: "/executives", icon: BadgeCheck }],
  },
  {
    title: "Platform",
    items: [
      { label: "Settings", href: "/settings", icon: Settings },
      { label: "Audit Logs", href: "/audit-logs", icon: FileClock },
      { label: "API Integrations", href: "/api-integrations", icon: Plug },
      { label: "System Health", href: "/system-health", icon: Activity },
    ],
  },
];

export const allNavItems = navSections.flatMap((s) => s.items);
export const brandIcon = Building2;
