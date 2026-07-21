# Deployment — Backend + Admin Dashboard

This covers pushing to GitHub, running the full stack on **macOS**, and deploying
the **admin dashboard** on the existing **EC2 server** at
`https://internet.acuronai.com/admin`.

- Backend: FastAPI voice agent (unchanged architecture) — port **8000**.
- Dashboard: Next.js 14 admin console in `frontend/dashboard/` — port **3100**.

---

## 1. Push to GitHub

The commit is already prepared for you in this repo (`origin` →
`github.com/rounitsinghacuronai/internet_voice_agent`, branch `main`). From your
Mac, in the project folder:

```bash
git log --oneline -1        # confirm the "admin dashboard + deploy" commit is there
git push origin main
```

If prompted for credentials, use a GitHub Personal Access Token as the password
(Settings → Developer settings → Tokens), or set up SSH / `gh auth login`.

> Note: `node_modules/`, `.next/`, `.venv/`, `.env`, and local `.claude/` config
> are git-ignored and will not be pushed.

---

## 2. Run locally on macOS

One-time setup, then run both services together:

```bash
./scripts/setup_mac.sh      # venv + pip + npm install, creates .env files
./scripts/dev.sh            # backend :8000 + dashboard :3100 (Ctrl-C stops both)
```

Open **http://localhost:3100**. The dashboard talks to the local backend
(`frontend/dashboard/.env.local` → `NEXT_PUBLIC_API_BASE=http://localhost:8000`).

Add your keys to `.env` (`SARVAM_API_KEY`, `GEMINI_API_KEY`) for full voice
functionality — the dashboard's live data (tickets, customers, stats, health)
works even without them.

Production-style local run (optional):

```bash
cd frontend/dashboard && npm run build && npm run start   # serves :3100
```

---

## 3. Deploy the dashboard on the EC2 server

Prerequisite: the backend is already deployed via `deploy/deploy_internet.sh`
(so `/opt/syncbroad-internet`, the `internet.acuronai.com` nginx vhost, and the
TLS cert exist).

```bash
# 1. Sync the latest code to the server's app dir (same method you already use)
#    e.g. on the server:
cd /opt/syncbroad-internet && git pull

# 2. Build + wire the dashboard (idempotent, non-destructive)
chmod +x deploy/deploy_dashboard.sh
sudo ./deploy/deploy_dashboard.sh
```

What the script does:

1. Ensures Node.js 18+ (installs Node 20 if missing).
2. Builds `frontend/dashboard` with `NEXT_PUBLIC_BASE_PATH=/admin` and an empty
   `NEXT_PUBLIC_API_BASE` (same-origin — nginx proxies `/api`, `/tickets`, etc.
   to the backend).
3. Creates + starts the `syncbroad-dashboard` systemd service on `127.0.0.1:3100`.
4. Adds **one** `location /admin` block into the existing `:443` vhost (never a
   new cert, never touches other sites), tests `nginx -t`, and reloads.

Result:

| URL | Serves |
|---|---|
| `https://internet.acuronai.com/`      | FastAPI backend (voice agent) |
| `https://internet.acuronai.com/admin` | Next.js admin dashboard |
| `https://internet.acuronai.com/api/…` | Backend dashboard endpoints |

Operate it:

```bash
sudo systemctl status syncbroad-dashboard
sudo journalctl -u syncbroad-dashboard -f
# re-deploy after new commits:
cd /opt/syncbroad-internet && git pull && sudo ./deploy/deploy_dashboard.sh
```

Override defaults via env, e.g. a different subpath or port:

```bash
BASE_PATH=/console DASH_PORT=3200 sudo ./deploy/deploy_dashboard.sh
```

---

## 4. Files added for deployment

```
DEPLOYMENT.md                          # this runbook
scripts/setup_mac.sh                   # one-time local setup (venv + npm)
scripts/dev.sh                         # run backend + dashboard together on Mac
deploy/deploy_dashboard.sh             # server deploy (build + systemd + nginx)
deploy/syncbroad-dashboard.service     # reference systemd unit
deploy/nginx-admin-location.conf       # reference nginx /admin snippet
```

---

## 5. Troubleshooting

- **502 on /admin** — dashboard service not up: `sudo systemctl status syncbroad-dashboard`, check `journalctl`.
- **/admin assets 404** — the app was built without `NEXT_PUBLIC_BASE_PATH=/admin`; re-run the deploy script (it sets it).
- **Dashboard shows mock-only data** — `NEXT_PUBLIC_DATA_SOURCE` should be `live`; the backend must be reachable at the same origin (server) or `localhost:8000` (Mac).
- **nginx -t fails after wiring** — the script prints the exact `include` line to add manually inside the `:443` server block if auto-injection can't find it.
