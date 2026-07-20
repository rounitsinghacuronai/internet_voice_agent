"""Ticket model + WhatsApp message rendering.

Pure functions and dataclasses — no I/O, no provider knowledge, fully
unit-testable. The sender receives finished TEXT; no business logic ever
lives inside a WhatsApp implementation.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

# ── priority derivation ──────────────────────────────────────────────────────
_HIGH = {
    "Fraud / Unauthorised Activity", "Harassment / Threat Calls",
    "SIM - Lost / Block Request", "Broadband - No Internet",
    "Broadband - Red LOS / Fiber Cut", "Enterprise - Leased Line Down",
    "Enterprise - SLA Breach", "Enterprise - MPLS / VPN Issue",
    "Mobile - No Network",
}
_LOW = {
    "Billing - Duplicate Invoice Request", "Wi-Fi Configuration Help",
    "KYC / Re-verification", "Account - Ownership Transfer",
}

# event types that always outrank category mapping
_EVENT_PRIORITY = {
    "priority_incident": "HIGH",       # fraud / stolen / harassment fast-path
    "human_escalation": "HIGH",
    "ticket_escalated": "HIGH",
    "sim_blocked": "HIGH",
}


def derive_priority(event_type: str, category: str) -> str:
    if event_type in _EVENT_PRIORITY:
        return _EVENT_PRIORITY[event_type]
    if category in _HIGH:
        return "HIGH"
    if category in _LOW:
        return "LOW"
    return "MEDIUM"


_SERVICE_LABEL = {
    "fiber": "Broadband / Fiber", "postpaid": "Mobile Postpaid",
    "prepaid": "Mobile Prepaid", "enterprise": "Enterprise",
}


@dataclass
class Ticket:
    """One operations ticket — everything the ops group needs, nothing more."""
    ticket_id: str
    event_type: str                     # complaint_registered|engineer_visit|…
    category: str
    priority: str
    summary: str
    # customer metadata (from verified call memory — never guessed)
    customer_name: str = ""
    mobile: str = ""                    # REGISTERED mobile (after verification)
    caller_number: str = ""             # number the call actually arrived from
    account_no: str = ""
    service_type: str = ""
    location: str = ""
    verified: bool = False
    # linkage
    call_id: str = ""                   # VoiceSession id — joins transcript/audio
    complaint_no: str = ""              # backend TC ticket, when one exists
    created_at: datetime = field(default_factory=datetime.now)
    follow_up_count: int = 0


def new_ticket_id(now: datetime | None = None) -> str:
    now = now or datetime.now()
    return f"TT-{now:%Y}-{uuid.uuid4().hex[:4].upper()}"


_EVENT_TITLE = {
    "complaint_registered": "NEW CUSTOMER TICKET",
    "ticket_escalated": "TICKET ESCALATED",
    "engineer_visit": "ENGINEER VISIT BOOKED",
    "sim_blocked": "SIM BLOCKED — LOST/STOLEN",
    "esim_request": "eSIM / SIM SWAP REQUEST",
    "priority_incident": "PRIORITY INCIDENT",
    "human_escalation": "HUMAN ESCALATION",
    "new_connection_request": "NEW CONNECTION REQUEST",
}


def format_message(t: Ticket, group_footer: bool = True) -> str:
    """Render the full structured WhatsApp message for a NEW ticket."""
    title = _EVENT_TITLE.get(t.event_type, "NEW CUSTOMER TICKET")
    lines = [
        f"🚨 {title}",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "",
        "🎫 Ticket ID",
        t.ticket_id,
    ]
    if t.complaint_no:
        lines += ["", "🗂 CRM Reference", t.complaint_no]
    lines += [
        "",
        "👤 Customer",
        t.customer_name or "Unverified caller",
    ]
    if t.mobile:
        label = "📱 Contact Number" if t.event_type == "new_connection_request" \
            else "📱 Registered Mobile"
        lines += ["", label, t.mobile]
    # Number the call actually arrived from (caller ID). Always shown when known
    # — and flagged when it differs from the registered/contact number.
    if t.caller_number:
        origin = t.caller_number
        if t.mobile and origin != t.mobile:
            ref = "contact number" if t.event_type == "new_connection_request" \
                else "registered"
            origin += f"  (differs from {ref})"
        lines += ["", "📞 Call Origin Number", origin]
    if t.account_no:
        lines += ["", "🆔 Account Number", t.account_no]
    lines += [
        "",
        "🌐 Service",
        _SERVICE_LABEL.get(t.service_type, t.service_type or "—"),
        "",
        "📂 Category",
        t.category or t.event_type.replace("_", " ").title(),
    ]
    if t.location:
        loc_label = "📍 Installation Address" \
            if t.event_type == "new_connection_request" else "📍 Location"
        lines += ["", loc_label, t.location]
    lines += [
        "",
        "⚠ Priority",
        t.priority,
        "",
        "📝 AI Summary",
        "",
        t.summary.strip(),
        "",
        "⏰ Time",
        f"{t.created_at:%d %b %Y}",
        f"{t.created_at:%I:%M %p}",
    ]
    if group_footer:
        lines += ["", "Generated automatically by AI Voice Agent"]
    return "\n".join(lines)


def format_follow_up(t: Ticket) -> str:
    """Compact repeat-contact message — never the full ticket again."""
    return "\n".join([
        f"🔁 FOLLOW-UP #{t.follow_up_count} — {t.ticket_id}",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "",
        "Customer has contacted us again regarding this issue.",
        "",
        f"👤 {t.customer_name or 'Unverified caller'}"
        + (f" · 📱 {t.mobile}" if t.mobile else "")
        + (f" · 📞 {t.caller_number}"
           if t.caller_number and t.caller_number != t.mobile else ""),
        f"📂 {t.category}",
        f"⚠ Priority {t.priority}",
        "",
        f"⏰ {datetime.now():%d %b %Y, %I:%M %p}",
        "",
        "Generated automatically by AI Voice Agent",
    ])


def build_summary(event_type: str, args: dict, result: dict, memory: dict,
                  troubleshooting: list[str]) -> str:
    """Deterministic ≤150-word operational summary. An optional LLM pass can
    replace this downstream (background only) — this version never fails and
    never hallucinates: every line is a known fact from the call."""
    parts: list[str] = []

    # New-connection requests have no free-text "issue" — summarise the details
    # the caller gave so the installation team can act without replaying audio.
    if event_type == "new_connection_request":
        nc = [f"New {(args.get('service_type') or '').strip() or 'connection'} "
              "request."]
        if args.get("plan"):
            nc.append(f"Plan wanted: {str(args['plan']).strip()}.")
        if args.get("address"):
            nc.append(f"Install address: {str(args['address']).strip()}.")
        if args.get("preferred_slot"):
            nc.append(f"Preferred slot: {str(args['preferred_slot']).strip()}.")
        nc.append("No verification required (prospective customer).")
        nc.append("Team to confirm feasibility, KYC and installation.")
        return " ".join(" ".join(nc).split()[:150])

    desc = (args.get("description") or args.get("reason")
            or args.get("details") or "").strip()
    if desc:
        # cap the free-text issue description so the status/action lines below
        # always survive the 150-word budget
        desc_words = desc.split()
        if len(desc_words) > 60:
            desc = " ".join(desc_words[:60]) + "…"
        parts.append(desc.rstrip(".") + ".")
    parts.append("Account verified." if memory.get("verified")
                 else "Caller NOT verified.")
    for step in troubleshooting[:4]:
        parts.append(step.rstrip(".") + ".")
    note = (result.get("note") or "").strip()
    if note:
        parts.append(note.split(". ")[0].rstrip(".") + ".")
    if event_type == "engineer_visit" and result.get("slot"):
        parts.append(f"Visit slot: {result['slot']}.")
    words = " ".join(parts).split()
    return " ".join(words[:150])
