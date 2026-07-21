# Syncbroad Networks — AI Support Console

Enterprise admin dashboard for the Telecom AI Voice Agent platform. Built as a
new Next.js 14 (App Router) + TypeScript + TailwindCSS + shadcn-style UI +
React Query app that lives alongside the existing Python voice-agent backend.

The backend architecture is untouched — this app **reuses the existing REST
API** where it exists and adds a small, additive, read-only `/api/*` router for
dashboard aggregations. Everything the backend cannot serve yet is behind a
**repository/service layer** so swapping a mock for a real endpoint later is a
one-line change with zero UI edits.

---

## 1. Quick start

```bash
# 1. Backend (from repo root) — serves /tickets, /api/dashboard/stats, etc.
python -m backend.app.main          # uvicorn on :8000

# 2. Dashboard
cd frontend/dashboard
cp .env.local.example .env.local     # set NEXT_PUBLIC_API_BASE + DATA_SOURCE
npm install
npm run dev                          # http://localhost:3100
```

`NEXT_PUBLIC_DATA_SOURCE=live` uses real endpoints + mock fallback for
unimplemented domains. Set it to `mock` for a fully offline design preview.

Verified: `npm run typecheck` passes clean; `next build` compiles and generates
all 16 routes.

---

## 2. Folder structure

```
frontend/dashboard/
├─ src/
│  ├─ app/
│  │  ├─ layout.tsx                 # root: fonts, ThemeProvider, React Query
│  │  ├─ providers.tsx
│  │  ├─ globals.css                # dark/light design tokens (HSL vars)
│  │  └─ (app)/                     # authenticated shell (sidebar + topbar)
│  │     ├─ layout.tsx
│  │     ├─ page.tsx                # Dashboard (home)
│  │     ├─ live-calls/page.tsx
│  │     ├─ tickets/page.tsx
│  │     ├─ tickets/[id]/page.tsx
│  │     ├─ customers/page.tsx
│  │     ├─ customers/[id]/page.tsx
│  │     ├─ ai-conversations/page.tsx
│  │     ├─ human-escalations/page.tsx
│  │     ├─ knowledge-base/page.tsx
│  │     ├─ analytics/page.tsx
│  │     ├─ executives/page.tsx
│  │     ├─ settings/page.tsx
│  │     ├─ audit-logs/page.tsx
│  │     ├─ api-integrations/page.tsx
│  │     └─ system-health/page.tsx
│  ├─ components/
│  │  ├─ ui/                        # shadcn-style primitives (14 components)
│  │  ├─ layout/                    # sidebar, topbar, theme-toggle, nav config
│  │  ├─ dashboard/                 # kpi-card, charts (recharts wrappers)
│  │  └─ shared/                    # page-header, empty-state, badges
│  └─ lib/
│     ├─ config.ts                  # DATA_SOURCE + API base + brand + polling
│     ├─ utils.ts                   # cn(), formatters
│     ├─ hooks.ts                   # React Query hooks (source-agnostic)
│     └─ api/
│        ├─ types.ts                # domain models
│        ├─ repositories.ts         # INTERFACES the UI depends on
│        ├─ http.ts                 # typed fetch wrapper
│        ├─ index.ts                # provider: picks live vs mock
│        ├─ live/repositories.ts    # real FastAPI implementations
│        └─ mock/                   # fixtures + mock implementations
├─ tailwind.config.ts, next.config.mjs, tsconfig.json, package.json
```

---

## 3. Pages (all 13 sidebar sections)

| Section            | Route                | Data source                              |
|--------------------|----------------------|------------------------------------------|
| Dashboard          | `/`                  | **live** `/api/dashboard/stats`          |
| Live Calls         | `/live-calls`        | **live** `/api/live-calls` (mock feed fallback) |
| Tickets (+ detail) | `/tickets`, `/tickets/[id]` | **live** `/tickets`               |
| Customers (+ profile) | `/customers`, `/customers/[id]` | **live** `/api/customers`, `/api/customers/{id}` |
| AI Conversations   | `/ai-conversations`  | repository (mock; ready for live)        |
| Human Escalations  | `/human-escalations` | repository (mock; ready for live)        |
| Knowledge Base     | `/knowledge-base`    | static + `/kb/*` ready                    |
| Analytics          | `/analytics`         | **live** stats + trend series            |
| Executives         | `/executives`        | repository (mock; ready for live)        |
| Settings           | `/settings`          | config (shows live voice pace = 1.3)     |
| Audit Logs         | `/audit-logs`        | static (schema ready for `notifications.jsonl`) |
| API Integrations   | `/api-integrations`  | static (modular connector cards)         |
| System Health      | `/system-health`     | **live** `/api/system/health`            |

---

## 4. APIs added (backend — additive, read-only)

New router `backend/app/api/dashboard.py`, mounted in `main.py` under `/api`:

- `GET /api/dashboard/stats` — KPIs, language mix, common issues, recent complaints (aggregated from the `notifications` + `complaints` + `customers` tables).
- `GET /api/customers` — subscriber list (search).
- `GET /api/customers/{account_no}` — full profile (reuses `TelecomServices`: plan, bill, usage, broadband, complaints, tickets).
- `GET /api/system/health` — per-component status derived from the live `Deps`.
- `GET /api/live-calls` — active WS sessions if exposed, else empty (frontend serves a demo feed).

Existing endpoints reused unchanged: `/tickets`, `/health`, `/kb/search`, `/kb/reload`.

**No database or model changes** — no new tables, same SQLite file.

---

## 5. Architecture decisions

- **Routing** — App Router with a `(app)` route group so every page shares the sidebar + topbar shell. Dynamic segments for ticket/customer detail.
- **Auth flow** — the shell + topbar user menu + 5 RBAC roles (Super Admin → Viewer) are wired in the UI. JWT/route-guarding is stubbed at the boundary: add a `middleware.ts` that validates a token and the repository `http.ts` already centralises where the `Authorization` header goes.
- **State management** — React Query for all server state (caching, background refetch, optimistic-ready). No global client store needed; UI state is local. Theme via `next-themes`.
- **Real-time strategy** — React Query `refetchInterval` polling per domain (live calls 4s, notifications 15s, health 20s, dashboard 30s). Swap to WebSocket/SSE by replacing the `queryFn` in `lib/hooks.ts` — components don't change.
- **Data layer** — repository/service pattern. UI → `hooks.ts` → `repositories` (interface) → live **or** mock implementation, chosen once in `lib/api/index.ts`. The UI never knows the source.
- **Performance** — debounced search (300ms), skeleton loading states, React Query caching + `staleTime`, code-split routes, server components for the shell, memoised derived counts. Ready for virtualised tables (swap `<Table>` body for a virtual list) and pagination (repos already take `limit`).
- **Security** — centralised fetch wrapper (single place for auth headers/CSRF), typed inputs, protected-route boundary via middleware, RBAC roles surfaced in settings. Backend endpoints added are read-only.

---

## 6. Future scalability

- Promote the mock repositories (conversations, escalations, executives, notifications, audit) to live by implementing the same interface against new FastAPI endpoints — no UI changes.
- Replace polling with the existing WebSocket layer for true push updates on Live Calls.
- Add server-side pagination + table virtualisation for the ticket/customer tables at thousands-of-rows scale.
- Wire real JWT auth in `middleware.ts` and the `http.ts` header hook.
- Multi-tenant theming already possible via the CSS variable tokens + `config.brand`.
