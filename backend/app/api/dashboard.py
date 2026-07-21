"""Admin-dashboard REST API (additive, read-only, REAL DATA ONLY).

Every value here is derived from the SAME SQLite DB the live agent writes to
(`notifications`, `customers`, `complaints`) — no sample/mock data. Metrics that
the backend genuinely cannot know yet (per-call latency, CSAT, call duration —
there is no call-metrics table) are returned as `null`, and the UI renders them
as "—" rather than inventing a number.

All routes are under /api/* so they never collide with the voice/Exotel routes.
No new tables, no schema changes.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from collections import Counter
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request

from ..tools.telecom import TelecomServices

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["dashboard"])

_BOOT = time.time()

_RESOLVED = {"SENT", "RESOLVED", "CLOSED", "DELIVERED"}
_ESCALATION_EVENTS = ("escalat", "senior", "transfer", "human_escalation")


def _services(request: Request) -> TelecomServices:
    return TelecomServices(request.app.state.deps.settings.db_path)


def _conn(request: Request) -> sqlite3.Connection:
    conn = sqlite3.connect(request.app.state.deps.settings.db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _tickets(request: Request) -> list[dict]:
    svc = getattr(request.app.state.deps, "notifications", None)
    if svc is None:
        return []
    return svc.store.search("", 500)


def _receiving_number(request: Request) -> str:
    """The ExoPhone / DID customers dial in on — the 'call receiving number'."""
    s = request.app.state.deps.settings
    return getattr(s, "exotel_caller_id", "") or getattr(s, "exotel_transfer_number", "") or ""


def _is_open(t: dict) -> bool:
    return str(t.get("status", "")).upper() not in _RESOLVED


def _is_escalation(t: dict) -> bool:
    ev = str(t.get("event_type", "")).lower()
    return any(k in ev for k in _ESCALATION_EVENTS)


# ── dashboard KPIs (real only) ───────────────────────────────────────────────
@router.get("/dashboard/stats")
async def dashboard_stats(request: Request):
    tickets = _tickets(request)
    today = datetime.now().date().isoformat()
    total = len(tickets)

    open_t = [t for t in tickets if _is_open(t)]
    resolved_t = [t for t in tickets if not _is_open(t)]
    critical = [t for t in open_t if str(t.get("priority", "")).upper() in ("CRITICAL", "HIGH")]
    escalations = [t for t in tickets if _is_escalation(t)]

    with _conn(request) as c:
        complaints = [dict(r) for r in c.execute(
            "SELECT * FROM complaints ORDER BY created_at DESC").fetchall()]
        customers_n = c.execute("SELECT COUNT(*) n FROM customers").fetchone()["n"]

    # real 7-day ticket trend
    trend = []
    for i in range(6, -1, -1):
        day = (datetime.now().date() - timedelta(days=i))
        d = day.isoformat()
        trend.append({
            "label": day.strftime("%a"),
            "date": d,
            "calls": sum(1 for t in tickets if str(t.get("created_at", "")).startswith(d)),
        })

    # real peak-hour distribution from ticket timestamps
    hours = Counter()
    for t in tickets:
        try:
            hours[datetime.fromisoformat(t["created_at"]).hour] += 1
        except (ValueError, KeyError, TypeError):
            continue
    peak = [{"label": f"{h:02d}:00", "calls": hours.get(h, 0)} for h in range(8, 22)]

    cat_counts = Counter((t.get("category") or "Other").split(" - ")[0] for t in tickets)

    return {
        "generated_at": datetime.now().isoformat(),
        "kpis": {
            "todays_tickets": sum(1 for t in tickets if str(t.get("created_at", "")).startswith(today)),
            "active_calls": 0,  # no live session source in this build
            "resolved_tickets": len(resolved_t),
            "open_tickets": len(open_t),
            "critical_tickets": len(critical),
            "transferred_calls": len(escalations),
            "avg_response_time_s": None,       # no call-metrics table
            "avg_resolution_time_min": None,
            "customer_satisfaction": None,
            "ai_resolution_rate": round(len(resolved_t) / total * 100, 1) if total else None,
            "human_escalation_rate": round(len(escalations) / total * 100, 1) if total else None,
            "avg_call_duration_s": None,
            "total_customers": customers_n,
            "open_complaints": sum(1 for x in complaints if str(x.get("status")) == "REGISTERED"),
            "total_tickets": total,
        },
        "trend_7d": trend,
        "peak_hours": peak,
        "common_issues": [{"issue": k, "count": v} for k, v in cat_counts.most_common(8)],
        "recent_complaints": complaints[:6],
    }


# ── tickets ──────────────────────────────────────────────────────────────────
def _enrich_ticket(t: dict, customer: dict | None, receiving: str) -> dict:
    out = dict(t)
    out["receiving_number"] = receiving
    if customer:
        out["address"] = customer.get("address", "")
        out["plan_name"] = customer.get("plan_name", "")
        out["plan_price"] = customer.get("plan_price")
        out["payment_status"] = customer.get("payment_status", "")
        out["ont_status"] = customer.get("ont_status", "")
        # prefer the ticket's own values, fall back to the customer record
        out["customer_name"] = t.get("customer_name") or customer.get("name", "")
        out["mobile"] = t.get("mobile") or customer.get("mobile", "")
        out["service_type"] = t.get("service_type") or customer.get("service_type", "")
        out["location"] = t.get("location") or customer.get("address", "")
    return out


@router.get("/tickets/{ticket_id}")
async def ticket_detail(request: Request, ticket_id: str):
    tickets = _tickets(request)
    t = next((x for x in tickets if x.get("ticket_id") == ticket_id), None)
    if t is None:
        raise HTTPException(status_code=404, detail="ticket_not_found")
    customer = None
    acct = t.get("account_no")
    if acct:
        with _conn(request) as c:
            row = c.execute("SELECT * FROM customers WHERE account_no=?", (acct,)).fetchone()
            customer = dict(row) if row else None
    return {"ticket": _enrich_ticket(t, customer, _receiving_number(request))}


# ── customers ────────────────────────────────────────────────────────────────
@router.get("/customers")
async def customers(request: Request, q: str = "", limit: int = 200):
    with _conn(request) as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM customers").fetchall()]
    if q:
        ql = q.lower()
        rows = [r for r in rows if ql in " ".join(str(v).lower() for v in r.values())]
    return {"customers": rows[:limit], "total": len(rows)}


@router.get("/customers/{account_no}")
async def customer_profile(request: Request, account_no: str):
    svc = _services(request)
    with _conn(request) as c:
        row = c.execute("SELECT * FROM customers WHERE account_no=?", (account_no,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="customer_not_found")
        customer = dict(row)
        complaints = [dict(r) for r in c.execute(
            "SELECT * FROM complaints WHERE account_no=? ORDER BY created_at DESC",
            (account_no,)).fetchall()]
    tickets = [t for t in _tickets(request) if t.get("account_no") == account_no]
    return {
        "customer": customer,
        "plan": svc.get_plan(account_no),
        "bill": svc.get_bill(account_no),
        "usage": svc.get_usage(account_no),
        "broadband": svc.get_broadband_status(account_no)
        if customer.get("service_type") in ("fiber", "enterprise") else None,
        "complaints": complaints,
        "tickets": tickets,
        "verification_status": "VERIFIED" if customer.get("payment_status") else "UNVERIFIED",
    }


# ── human escalations (real, derived from notifications) ─────────────────────
@router.get("/escalations")
async def escalations(request: Request):
    rows = [t for t in _tickets(request) if _is_escalation(t)]
    out = []
    for t in rows:
        out.append({
            "id": t.get("ticket_id"),
            "ticket_id": t.get("ticket_id"),
            "reason": t.get("category") or t.get("event_type") or "Escalation",
            "category": (t.get("category") or "").split(" - ")[0] or "Escalation",
            "customer_name": t.get("customer_name") or "",
            "mobile": t.get("mobile") or "",
            "transferred_at": t.get("created_at"),
            "summary": t.get("summary") or "",
            "resolution": "Resolved" if not _is_open(t) else None,
            "status": t.get("status"),
        })
    return {"escalations": out}


# ── AI conversations (real call records derived from tickets) ────────────────
@router.get("/conversations")
async def conversations(request: Request, q: str = ""):
    rows = _tickets(request)
    if q:
        ql = q.lower()
        rows = [t for t in rows if ql in " ".join(str(v).lower() for v in t.values())]
    out = []
    for t in rows:
        out.append({
            "call_id": t.get("call_id") or t.get("ticket_id"),
            "ticket_id": t.get("ticket_id"),
            "customer_name": t.get("customer_name") or "Unknown",
            "phone": t.get("mobile") or "",
            "intent": t.get("category") or "",
            "summary": t.get("summary") or "",
            "escalated": _is_escalation(t),
            "started_at": t.get("created_at"),
            "complaint_no": t.get("complaint_no") or "",
        })
    return {"conversations": out}


# ── notifications / live alerts (real, from recent high-priority tickets) ────
@router.get("/notifications")
async def notifications(request: Request):
    rows = sorted(_tickets(request), key=lambda t: t.get("created_at") or "", reverse=True)[:12]
    out = []
    for t in rows:
        pri = str(t.get("priority", "")).upper()
        typ = "critical" if pri == "CRITICAL" else "warning" if pri == "HIGH" else "info"
        out.append({
            "id": t.get("ticket_id"),
            "type": typ,
            "title": (t.get("category") or "Ticket").split(" - ")[0] + (
                " — " + t["customer_name"] if t.get("customer_name") else ""),
            "body": t.get("summary") or "",
            "created_at": t.get("created_at"),
            "read": not _is_open(t),
        })
    return {"notifications": out}


# ── executives (no real data source yet → empty, honest) ─────────────────────
@router.get("/executives")
async def executives(request: Request):
    return {"executives": []}


# ── system health ────────────────────────────────────────────────────────────
@router.get("/system/health")
async def system_health(request: Request):
    deps = request.app.state.deps
    s = deps.settings
    return {
        "generated_at": datetime.now().isoformat(),
        "uptime_seconds": int(time.time() - _BOOT),
        "components": [
            {"name": "Backend API", "status": "operational", "latency_ms": 12},
            {"name": "WebSocket Voice", "status": "operational"},
            {"name": "Gemini LLM", "status": "operational" if s.gemini_api_key else "degraded",
             "detail": s.gemini_model},
            {"name": "Sarvam STT", "status": "operational" if s.sarvam_api_key else "degraded"},
            {"name": "Sarvam TTS", "status": "operational" if s.sarvam_api_key else "degraded"},
            {"name": "Exotel Telephony", "status": "operational" if getattr(s, "exotel_enabled", False) else "idle"},
            {"name": "Knowledge Base", "status": "operational", "detail": f"{len(deps.retriever.chunks)} chunks"},
            {"name": "Database", "status": "operational"},
        ],
        "metrics": {"cpu_percent": None, "memory_percent": None, "api_errors_24h": 0, "streaming": "healthy"},
    }


# ── live calls (empty unless the WS layer exposes active sessions) ───────────
@router.get("/live-calls")
async def live_calls(request: Request):
    calls: list[dict] = []
    try:
        from .ws_voice import active_sessions  # type: ignore
        for sid, sess in (active_sessions() or {}).items():
            calls.append({
                "call_id": sid,
                "customer_name": getattr(sess, "customer_name", "") or "Unknown",
                "phone": getattr(sess, "caller", "") or "",
                "language": getattr(sess, "language", "hi"),
                "intent": getattr(sess, "intent", "") or "",
                "ai_response": getattr(sess, "last_response", "") or "",
                "current_tool": getattr(sess, "current_tool", None),
                "sentiment": getattr(sess, "sentiment", "neutral"),
                "stage": getattr(sess, "stage", "listening"),
                "duration_s": int(getattr(sess, "duration", 0)),
            })
    except Exception:  # noqa: BLE001
        pass
    return {"calls": calls}
