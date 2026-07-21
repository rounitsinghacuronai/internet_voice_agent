/**
 * Runtime configuration for the dashboard.
 *
 * DATA_SOURCE controls the repository layer:
 *   "live" -> real FastAPI endpoints where implemented, mock repos for the rest
 *   "mock" -> mock repositories everywhere (offline / design preview)
 *
 * The UI never reads this directly — it goes through the repository provider
 * in lib/api/index.ts, so swapping to production is a one-line change here.
 */
export const config = {
  // Empty string => same-origin relative requests (production behind nginx:
  // internet.acuronai.com/api/... is proxied to the FastAPI backend).
  // On Mac dev set NEXT_PUBLIC_API_BASE=http://localhost:8000 in .env.local.
  apiBase: process.env.NEXT_PUBLIC_API_BASE ?? "",
  dataSource: (process.env.NEXT_PUBLIC_DATA_SOURCE || "live") as "live" | "mock",
  brand: {
    name: "Syncbroad Networks",
    product: "AI Support Console",
    shortName: "Syncbroad",
  },
  polling: {
    liveCalls: 4_000,
    dashboard: 10_000,
    tickets: 8_000,
    customers: 15_000,
    conversations: 10_000,
    escalations: 10_000,
    notifications: 10_000,
    systemHealth: 20_000,
  },
} as const;

export type DataSource = typeof config.dataSource;
