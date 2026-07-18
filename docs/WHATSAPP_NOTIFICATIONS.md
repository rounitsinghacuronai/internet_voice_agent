# WhatsApp Ops Notifications (POC)

When the agent takes a real action — registers a complaint, books an engineer,
blocks a lost SIM, files an eSIM request, logs a fraud incident, or escalates
to a human — a structured ticket lands in the internal WhatsApp operations
group. Automatically, asynchronously, and with **zero added customer latency**.

## Architecture

```
Customer → Exotel → Voice Agent → Conversation Manager
                                        │  (unchanged, knows nothing)
                                   ToolRegistry ── on_event observer (sync, ~0 ms)
                                        │
                              NotificationService (async worker)
                              ├─ relevance filter (tool→event map)
                              ├─ duplicate detection (customer+category window)
                              ├─ Ticket build + AI summary (≤150 words)
                              ├─ retry w/ backoff → delivery status
                              ├─ SQLite state + JSONL audit (+ optional S3)
                              └─ WhatsAppSender  ←— the ONLY swappable part
                                    ├─ personal  → whatsapp_bridge sidecar (POC)
                                    ├─ meta      → WhatsApp Business Cloud API
                                    └─ twilio / exotel → add a class, done
                                        ↓
                              WhatsApp → Operations Group
```

- `NotificationService.notify_event(...)` / `send_ticket(ticket)` are **sync
  and instant** — a bounded-queue `put_nowait`. The turn's TTS never waits.
- The Conversation Manager is untouched; the registry fires a guarded observer.
- No business logic in any sender: text in, message out.

## POC sender: personal WhatsApp via `whatsapp_bridge/`

**Why whatsapp-web.js** (evaluated options): the most maintained WhatsApp-Web
automation library; real group send, persistent `LocalAuth` session (scan the
QR once), reconnection events, delivery IDs. Baileys is lighter (no Chromium)
but API-churns and historically bans more. pywhatkit/pyautogui (opens a browser
tab per message) and DIY Playwright (selector roulette) are not server-viable.

**⚠ Terms-of-service risk**: automating a personal account is against
WhatsApp's ToS — a small but real chance of the number being banned. Use a
dedicated spare number for the POC, keep volume low (this service only sends
actionable tickets, deduplicated), and migrate to the Business API for
production.

### Setup

```bash
cd whatsapp_bridge
npm install
npm start                      # prints a QR code on first run
# scan with the POC WhatsApp account → session persists in ./session/
```

```bash
# .env
WHATSAPP_ENABLED=true
WHATSAPP_PROVIDER=personal
WHATSAPP_GROUP_NAME=Operations          # exact group name; account must be a member
WHATSAPP_BRIDGE_URL=http://127.0.0.1:3001
```

Restart the agent. Register any complaint on a test call — the group message
arrives within seconds; the call itself is unaffected.

## When it notifies (and when it never does)

Notifies on **successful**: complaint registration (all categories — internet
down, broadband/Wi-Fi, SIM, billing dispute, installation…), ticket
escalation, engineer visit booking, SIM block, SIM-swap/eSIM request, priority
(fraud/security) incident, human transfer (incl. callback requests).

Never notifies: greetings, FAQs/knowledge answers, reads (bill/usage/status),
failed or gate-refused tools, resolved-in-conversation simple queries.

## Message format

Structured ticket (see `ticket_formatter.py`): ticket ID `TT-YYYY-XXXX`, CRM
reference (TC number), customer, mobile, account, service, category, location,
priority (HIGH/MEDIUM/LOW derived from category+event), ≤150-word AI summary
(main issue, verification status, troubleshooting done, current status,
recommended action — never a raw transcript), timestamp, automation footer.

## Duplicate detection

Same customer (mobile/account) + same category inside
`WHATSAPP_DEDUP_WINDOW_MIN` (default 60): the existing ticket is updated,
`follow_up_count` incremented, and a compact follow-up is sent — "Customer has
contacted us again regarding this issue." No group spam.

## Delivery status, retries, audit

Status per ticket: `PENDING → (RETRYING…) → SENT | FAILED`, with attempts,
timestamps and last error, stored in the `notifications` table (same SQLite
DB, keyed by ticket + call ID). Retries: exponential backoff + jitter
(`WHATSAPP_RETRY_MAX`, `WHATSAPP_RETRY_BASE_S`). Exhausted retries log FAILED
and stop — the customer conversation is never interrupted. Every state change
is appended to `backend/logs/notifications.jsonl` (audit-grade, never
rewritten); set `NOTIFY_S3_BUCKET` to mirror each line to S3.

## Dashboard

`http://localhost:8000/ops` — searchable ticket table: ticket ID, customer,
category, priority, WhatsApp status + attempts, delivery time, follow-up
count, expandable summary, call ID (links the ticket to transcript/audio
stored under the same call). Backed by `GET /tickets?q=…`.

## Migrating off the personal account

Set `WHATSAPP_PROVIDER=meta` plus `WHATSAPP_META_TOKEN`,
`WHATSAPP_META_PHONE_ID`, `WHATSAPP_META_TO` (comma-separated numbers — the
Cloud API can't post to consumer groups; use a broadcast list or Community
announcement channel). Nothing outside `whatsapp_sender.py` changes. Twilio /
Exotel WhatsApp: add one sender class with the same `send(group, message)`
contract.
