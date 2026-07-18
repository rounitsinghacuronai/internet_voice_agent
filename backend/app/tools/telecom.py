"""Mock Syncbroad Networks live-data services (in-process; real OSS/BSS APIs swap in behind
the same signatures). SQLite-backed, seeded with realistic Maharashtra-circle
subscribers. SLA hours mirror standard telecom customer-care SLAs (per published
TRAI QoS benchmarks and typical operator commitments)."""
from __future__ import annotations

import logging
import random
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

# ── SLA resolution hours: category → (L1, L2, final escalation) ─────────────
SLA_HOURS: dict[str, tuple[int, int, int]] = {
    "Mobile - No Network": (24, 48, 72),
    "Mobile - Weak Signal": (48, 96, 168),
    "Mobile - Call Drops": (48, 72, 96),
    "Mobile - Data Not Working": (24, 48, 72),
    "Mobile - SMS Failure": (24, 48, 72),
    "VoLTE / VoWiFi Issue": (48, 72, 96),
    "International Roaming Issue": (12, 24, 48),
    "Broadband - No Internet": (8, 24, 48),
    "Broadband - Slow Speed": (24, 48, 72),
    "Broadband - Frequent Disconnection": (24, 48, 72),
    "Broadband - Red LOS / Fiber Cut": (8, 16, 24),
    "Router / ONT Fault": (24, 48, 72),
    "Wi-Fi Configuration Help": (4, 24, 48),
    "Billing - High Bill": (72, 120, 168),
    "Billing - Incorrect Charges": (72, 120, 168),
    "Billing - Duplicate Invoice Request": (24, 48, 72),
    "Payment - Failed But Debited": (120, 168, 240),
    "Recharge - Failed": (4, 24, 48),
    "Refund / Security Deposit": (120, 168, 240),
    "SIM - Lost / Block Request": (0, 2, 4),
    "SIM - Not Activating": (4, 24, 48),
    "SIM - Replacement": (24, 48, 72),
    "eSIM - Activation Issue": (4, 24, 48),
    "MNP - Port-In Delay": (48, 96, 120),
    "MNP - Port-Out Issue": (48, 96, 120),
    "KYC / Re-verification": (24, 48, 72),
    "New Connection - Installation Delay": (48, 96, 144),
    "Relocation / Shifting Request": (72, 120, 168),
    "Engineer Visit Required": (24, 48, 72),
    "Account - Ownership Transfer": (168, 240, 336),
    "Account - Suspension / Reactivation": (24, 48, 72),
    "Cancellation Request": (72, 120, 168),
    "Enterprise - Leased Line Down": (2, 4, 8),
    "Enterprise - MPLS / VPN Issue": (4, 8, 24),
    "Enterprise - Static IP Issue": (8, 24, 48),
    "Enterprise - SLA Breach": (24, 48, 72),
    "Fraud / Unauthorised Activity": (0, 2, 4),
    "Harassment / Threat Calls": (0, 4, 24),
}

VALID_CATEGORIES = set(SLA_HOURS)

_SEED_CUSTOMERS = [
    # (account_no, name, mobile, address, service_type, plan_name, plan_price_rs,
    #  ont_status, payment_status)
    ("300012345678", "Ramesh Patil", "9820012345", "Kothrud, Pune", "fiber",
     "Fiber 100 Mbps Unlimited + Landline", 799, "OK", "PAID"),
    ("300023456789", "Sunita Deshmukh", "9822233445", "Hadapsar, Pune", "postpaid",
     "Postpaid 599 — 75GB + Unlimited Calls", 599, "", "DUE"),
    ("210034567890", "Abdul Sheikh", "9867554433", "Kharadi, Pune", "fiber",
     "Fiber 300 Mbps Unlimited", 1499, "LOS", "PAID"),
    ("330045678901", "Kavita Jadhav", "9700112233", "Nanded City, Pune", "prepaid",
     "Prepaid 299 — 2GB/day, 28 days", 299, "", "ACTIVE"),
    ("410056789012", "Suresh Wagh", "9922334455", "Hinjewadi, Pune", "enterprise",
     "Enterprise Leased Line 200 Mbps 1:1", 8999, "OK", "PAID"),
    ("880012340001", "Kiran Darkunde", "8624900039", "Wakad, Pune", "fiber",
     "Fiber 100 Mbps Unlimited + Landline", 799, "OK", "PAID"),
    ("880012340002", "Rounit Singh", "7267850755", "Baner, Pune", "fiber",
     "Fiber 300 Mbps Unlimited", 1499, "OK", "PAID"),
]

# (area, service affected, reason, ETA hours)
_OUTAGES = [
    ("Kothrud, Pune", "mobile", "4G/5G tower maintenance", 3),
    ("Kharadi, Pune", "fiber", "trunk fiber cut — splicing team on site", 5),
]

_PLAN_CATALOG = {
    "prepaid": "Prepaid packs: 199 (1GB/day, 24d), 299 (2GB/day, 28d), 449 (3GB/day, 28d), "
               "599 (2GB/day, 56d). Add-ons: 19 (1GB same-day), 29 (2GB same-day). "
               "Quote validity/data from the live plan lookup, never from memory.",
    "postpaid": "Postpaid: 399 (40GB), 599 (75GB + Netflix Basic), 999 (150GB family, 2 add-on SIMs). "
                "Data rollover up to 200GB. Bill = rent + add-ons + taxes per invoice.",
    "fiber": "Fiber: 599 (50 Mbps), 799 (100 Mbps + landline), 1499 (300 Mbps + OTT bundle), "
             "3999 (1 Gbps). All unlimited with fair-use at 3.3TB/month.",
    "enterprise": "Enterprise: leased line 1:1 with 99.5% uptime SLA, MPLS, SD-WAN, static IP "
                  "blocks (/30 to /24), managed router option. Commercials per account manager.",
}


class TelecomServices:
    """All live tools + helpers. One SQLite file, thread-safe usage from asyncio
    (each call is short; sqlite3 in serialized mode)."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS customers(
              account_no TEXT PRIMARY KEY, name TEXT, mobile TEXT, address TEXT,
              service_type TEXT, plan_name TEXT, plan_price INTEGER,
              ont_status TEXT, payment_status TEXT);
            CREATE TABLE IF NOT EXISTS complaints(
              ticket_no TEXT PRIMARY KEY, account_no TEXT, category TEXT, description TEXT,
              status TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS otps(mobile TEXT PRIMARY KEY, otp TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS incidents(
              id TEXT PRIMARY KEY, type TEXT, details TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS visits(
              id TEXT PRIMARY KEY, account_no TEXT, slot TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS new_connections(
              application_no TEXT PRIMARY KEY, name TEXT, address TEXT,
              service_type TEXT, plan TEXT, contact_mobile TEXT,
              preferred_slot TEXT, caller_number TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS feedback(
              id TEXT PRIMARY KEY, rating TEXT, comment TEXT, created_at TEXT);
            """)
            # Idempotent seeding: customers are STATIC reference data (never
            # modified at runtime — tickets/OTPs/incidents live in their own
            # tables), so INSERT OR REPLACE on every startup keeps the seed roster
            # authoritative. This both adds newly listed subscribers AND refreshes
            # edited fields (e.g. a corrected Pune address) on an existing
            # telecom.db, without touching complaints, otps or visits.
            before = c.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
            c.executemany("INSERT OR REPLACE INTO customers VALUES (?,?,?,?,?,?,?,?,?)",
                          _SEED_CUSTOMERS)
            after = c.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
            if after - before:
                log.info("Seeded %d new customer(s) (total %d)", after - before, after)

    def _customer(self, account_no: str) -> sqlite3.Row | None:
        with self._conn() as c:
            return c.execute("SELECT * FROM customers WHERE account_no=?", (account_no,)).fetchone()

    # ── identity & security ──────────────────────────────────────────────────
    def verify_customer(self, account_no: str = "", mobile: str = "") -> dict:
        with self._conn() as c:
            row = None
            if account_no:
                row = c.execute("SELECT * FROM customers WHERE account_no=?",
                                (account_no.strip(),)).fetchone()
            if row is None and mobile:
                row = c.execute("SELECT * FROM customers WHERE mobile=?",
                                (mobile.strip(),)).fetchone()
        if row is None:
            return {"verified": False, "reason": "No customer found for the given number."}
        return {"verified": True, "account_no": row["account_no"], "name": row["name"],
                "mobile": row["mobile"], "address": row["address"],
                "service_type": row["service_type"], "plan_name": row["plan_name"]}

    def send_otp(self, mobile: str) -> dict:
        otp = f"{random.randint(0, 999999):06d}"
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO otps VALUES (?,?,?)",
                      (mobile, otp, datetime.now().isoformat()))
        log.info("OTP for %s: %s (mock — printed, not SMSed)", mobile, otp)
        return {"sent": True, "mobile": mobile, "mock_otp_for_demo": otp}

    def verify_otp(self, mobile: str, otp: str) -> dict:
        with self._conn() as c:
            row = c.execute("SELECT otp FROM otps WHERE mobile=?", (mobile,)).fetchone()
        ok = bool(row and row["otp"] == otp.strip())
        return {"verified": ok}

    # ── account, plan, billing ───────────────────────────────────────────────
    def get_plan(self, account_no: str) -> dict:
        row = self._customer(account_no)
        if row is None:
            return {"error": "customer_not_found"}
        rng = random.Random(account_no)
        out = {"account_no": account_no, "service_type": row["service_type"],
               "plan_name": row["plan_name"], "monthly_price_rs": row["plan_price"]}
        if row["service_type"] == "prepaid":
            out["validity_days_left"] = rng.randint(2, 26)
            out["daily_data_gb"] = 2.0
        return out

    def get_bill(self, account_no: str) -> dict:
        row = self._customer(account_no)
        if row is None:
            return {"error": "customer_not_found"}
        if row["service_type"] == "prepaid":
            return {"account_no": account_no, "note": "Prepaid account — no monthly bill. "
                    "Use recharge history and plan validity instead."}
        rng = random.Random(account_no)  # stable per account
        base = row["plan_price"]
        extras = rng.choice([0, 0, 49, 99, 147])   # add-on packs / data top-ups
        amount = round((base + extras) * 1.18, 0)  # +18% GST
        month = (datetime.now().replace(day=1) - timedelta(days=1)).strftime("%B %Y")
        return {"account_no": account_no, "bill_month": month,
                "plan_rent_rs": base, "addon_charges_rs": extras,
                "amount_rs": amount, "includes_gst": True,
                "due_date": (datetime.now() + timedelta(days=10)).strftime("%d %b %Y"),
                "payment_status": row["payment_status"],
                "autopay_enabled": rng.choice([True, False])}

    def get_payment_status(self, account_no: str, txn_ref: str = "") -> dict:
        rng = random.Random((account_no or "") + (txn_ref or ""))
        status = rng.choice(["SUCCESS", "PENDING", "FAILED_DEBITED"])
        out = {"account_no": account_no,
               "txn_ref": txn_ref or f"TXN{rng.randint(10**9, 10**10-1)}",
               "status": status}
        if status == "FAILED_DEBITED":
            out["note"] = "Transaction failed but amount debited — auto-reversal in 5 to 7 working days."
        return out

    def get_recharge_history(self, account_no: str) -> dict:
        row = self._customer(account_no)
        if row is None:
            return {"error": "customer_not_found"}
        rng = random.Random(account_no)
        history = []
        day = datetime.now()
        for _ in range(3):
            day -= timedelta(days=rng.randint(20, 30))
            history.append({"date": day.strftime("%d %b %Y"),
                            "pack_rs": rng.choice([199, 299, 449]),
                            "status": rng.choice(["SUCCESS", "SUCCESS", "SUCCESS", "FAILED"])})
        return {"account_no": account_no, "recharges": history}

    def get_usage(self, account_no: str) -> dict:
        row = self._customer(account_no)
        if row is None:
            return {"error": "customer_not_found"}
        rng = random.Random(account_no + "usage")
        if row["service_type"] == "fiber":
            return {"account_no": account_no, "cycle_data_used_gb": rng.randint(120, 900),
                    "fair_use_limit_tb": 3.3, "throttled": False}
        used = round(rng.uniform(0.2, 2.0), 1)
        return {"account_no": account_no, "data_used_today_gb": used,
                "daily_quota_gb": 2.0, "data_left_today_gb": round(max(0.0, 2.0 - used), 1),
                "quota_exhausted": used >= 2.0}

    # ── network & broadband diagnostics ──────────────────────────────────────
    def get_network_status(self, area: str = "", account_no: str = "") -> dict:
        if account_no:
            row = self._customer(account_no)
            area = row["address"] if row else area
        for out_area, service, reason, eta_h in _OUTAGES:
            if area and (out_area.lower() in area.lower() or area.lower() in out_area.lower()):
                eta = (datetime.now() + timedelta(hours=eta_h)).strftime("%I:%M %p")
                return {"outage": True, "area": out_area, "service": service,
                        "reason": reason, "expected_restoration": eta, "eta_hours": eta_h}
        return {"outage": False, "area": area,
                "note": "No area-level outage on record. If this customer's service is down, "
                        "it is likely an individual fault — run diagnostics (broadband) or "
                        "register the matching Mobile/Broadband complaint category."}

    def get_broadband_status(self, account_no: str) -> dict:
        row = self._customer(account_no)
        if row is None:
            return {"error": "customer_not_found"}
        if row["service_type"] not in ("fiber", "enterprise"):
            return {"error": "not_a_broadband_account",
                    "note": "This account is mobile-only. Use network status / usage instead."}
        rng = random.Random(account_no + "bb")
        los = row["ont_status"] == "LOS"
        return {"account_no": account_no,
                "ont_status": "LOS — no optical signal (red light)" if los else "OK — online",
                "line_state": "DOWN" if los else "UP",
                "last_sync_mbps": 0 if los else rng.choice([98, 102, 297, 301]),
                "wifi_radio": "ON",
                "note": ("Red LOS means no light on the fiber — check area fiber cut first, "
                         "then engineer visit. Router reboot will NOT fix LOS."
                         if los else "Optical line healthy. If the customer still has issues, "
                         "suspect Wi-Fi/router side — run diagnostics or reboot the ONT.")}

    def run_line_diagnostics(self, account_no: str) -> dict:
        row = self._customer(account_no)
        if row is None:
            return {"error": "customer_not_found"}
        rng = random.Random(account_no + "diag")
        los = row["ont_status"] == "LOS"
        if los:
            return {"account_no": account_no, "result": "FAIL",
                    "finding": "No optical signal at ONT (LOS). Fiber path broken between "
                               "pole and premises or area cut.",
                    "recommendation": "Check area outage; if none, schedule engineer visit."}
        finding = rng.choice([
            "Line healthy. Wi-Fi channel congested (channel 6) — 2.4GHz interference likely.",
            "Line healthy. High LAN latency to router only — customer router suspect.",
            "Line healthy end to end. Speed test from exchange normal.",
        ])
        return {"account_no": account_no, "result": "PASS", "finding": finding,
                "recommendation": "If speed complaints persist on Wi-Fi only, suggest 5GHz "
                                  "band / router placement; wired test to isolate."}

    def restart_ont(self, account_no: str) -> dict:
        row = self._customer(account_no)
        if row is None:
            return {"error": "customer_not_found"}
        if row["ont_status"] == "LOS":
            return {"restarted": False,
                    "note": "ONT has no optical signal (LOS) — a reboot cannot fix a fiber "
                            "break. Check area outage or schedule an engineer visit."}
        return {"restarted": True,
                "note": "Remote reboot command sent to the ONT. Service resumes in about "
                        "two to three minutes. Ask the customer to stay on the line."}

    # ── complaints & field ops ───────────────────────────────────────────────
    def register_complaint(self, account_no: str, category: str, description: str) -> dict:
        if category not in VALID_CATEGORIES:
            return {"error": "invalid_category",
                    "valid_examples": sorted(VALID_CATEGORIES)[:12]}
        ticket = f"TC{datetime.now():%y%m}{uuid.uuid4().hex[:6].upper()}"
        with self._conn() as c:
            c.execute("INSERT INTO complaints VALUES (?,?,?,?,?,?)",
                      (ticket, account_no, category, description, "REGISTERED",
                       datetime.now().isoformat()))
        hours = SLA_HOURS[category][0]
        return {"ticket_no": ticket, "category": category, "status": "REGISTERED",
                "sla_hours": hours,
                "note": f"Ticket sent by SMS to registered mobile. Standard resolution {hours} "
                        "hours; auto-escalates to the next level if delayed."}

    def track_complaint(self, complaint_no: str) -> dict:
        with self._conn() as c:
            row = c.execute("SELECT * FROM complaints WHERE ticket_no=?",
                            (complaint_no.strip(),)).fetchone()
        if row is None:
            return {"error": "complaint_not_found"}
        return {"ticket_no": row["ticket_no"], "category": row["category"],
                "status": row["status"], "created_at": row["created_at"]}

    def escalate_complaint(self, complaint_no: str, reason: str = "") -> dict:
        with self._conn() as c:
            row = c.execute("SELECT * FROM complaints WHERE ticket_no=?",
                            (complaint_no.strip(),)).fetchone()
            if row is None:
                return {"error": "complaint_not_found"}
            c.execute("UPDATE complaints SET status='ESCALATED' WHERE ticket_no=?",
                      (complaint_no.strip(),))
        l2 = SLA_HOURS.get(row["category"], (24, 48, 72))[1]
        return {"ticket_no": row["ticket_no"], "status": "ESCALATED",
                "l2_sla_hours": l2,
                "note": f"Escalated to level two. Revised resolution window {l2} hours; "
                        "a senior engineer now owns this ticket."}

    def close_complaint(self, complaint_no: str, resolution_note: str = "") -> dict:
        with self._conn() as c:
            row = c.execute("SELECT * FROM complaints WHERE ticket_no=?",
                            (complaint_no.strip(),)).fetchone()
            if row is None:
                return {"error": "complaint_not_found"}
            c.execute("UPDATE complaints SET status='RESOLVED' WHERE ticket_no=?",
                      (complaint_no.strip(),))
        return {"ticket_no": row["ticket_no"], "status": "RESOLVED",
                "note": "Closed with the customer's confirmation on this call."}

    def schedule_engineer_visit(self, account_no: str, preferred_slot: str = "") -> dict:
        vid = f"EV{uuid.uuid4().hex[:8].upper()}"
        slot = preferred_slot or "tomorrow, 10 AM to 1 PM"
        with self._conn() as c:
            c.execute("INSERT INTO visits VALUES (?,?,?,?)",
                      (vid, account_no, slot, datetime.now().isoformat()))
        return {"scheduled": True, "visit_id": vid, "slot": slot,
                "note": "Engineer visit booked. The engineer calls the registered mobile "
                        "thirty minutes before arriving."}

    # ── SIM / plan lifecycle ─────────────────────────────────────────────────
    def block_sim(self, mobile: str, reason: str = "lost") -> dict:
        ref = f"SB{uuid.uuid4().hex[:8].upper()}"
        log.warning("SIM BLOCK %s: %s (%s)", ref, mobile, reason)
        return {"blocked": True, "reference": ref, "mobile": mobile,
                "note": "SIM blocked with immediate effect — calls, SMS and data stopped. "
                        "A replacement SIM with the same number is free at any store with "
                        "photo ID, or can be couriered after doorstep KYC."}

    def request_plan_change(self, account_no: str, new_plan: str) -> dict:
        ref = f"PC{uuid.uuid4().hex[:8].upper()}"
        return {"submitted": True, "reference": ref, "new_plan": new_plan,
                "note": "Plan change confirmed. Prepaid: applies on next recharge. "
                        "Postpaid/fiber: applies from the next bill cycle; this cycle is "
                        "pro-rated."}

    def request_sim_swap(self, account_no: str, swap_type: str = "replacement") -> dict:
        ref = f"SS{uuid.uuid4().hex[:8].upper()}"
        note = ("eSIM QR code sent to the registered email. Scan it in phone settings; "
                "the physical SIM deactivates once the eSIM registers."
                if swap_type == "esim" else
                f"Replacement SIM request logged; standard processing "
                f"{SLA_HOURS['SIM - Replacement'][0]} hours after in-store or doorstep KYC.")
        return {"submitted": True, "reference": ref, "swap_type": swap_type, "note": note}

    def register_new_connection(self, name: str = "", address: str = "",
                                service_type: str = "", plan: str = "",
                                contact_mobile: str = "", preferred_slot: str = "",
                                caller_number: str = "") -> dict:
        """Log a NEW CONNECTION request. No verification/OTP — the caller is a
        prospect, not an existing subscriber. The request is forwarded to the
        operations WhatsApp group (via the notification observer), which owns
        feasibility check, KYC and installation scheduling from here.

        `contact_mobile` is the number the caller wants us to reach them on;
        `caller_number` is the number the call actually arrived from (caller ID).
        Both are carried through to the ops ticket."""
        app_no = f"NC{datetime.now():%y%m}{uuid.uuid4().hex[:6].upper()}"
        with self._conn() as c:
            c.execute("INSERT INTO new_connections VALUES (?,?,?,?,?,?,?,?,?)",
                      (app_no, name.strip(), address.strip(),
                       service_type.strip(), plan.strip(),
                       (contact_mobile or "").strip(), preferred_slot.strip(),
                       (caller_number or "").strip(), datetime.now().isoformat()))
        log.info("NEW CONNECTION %s: %s / %s / %s @ %s", app_no, name,
                 service_type, plan, address)
        return {"registered": True, "application_no": app_no,
                "name": name, "service_type": service_type, "plan": plan,
                "address": address, "contact_mobile": contact_mobile,
                "preferred_slot": preferred_slot,
                "note": "New connection request logged and forwarded to our "
                        "installation team. They will call the contact number to "
                        "confirm feasibility, KYC and an installation slot."}

    def get_new_connection_status(self, application_no: str) -> dict:
        rng = random.Random(application_no)
        stage = rng.choice(["Document verification", "Feasibility check in your area",
                            "Fiber laying scheduled", "Installation appointment scheduled"])
        return {"application_no": application_no, "stage": stage}

    def get_plan_catalog(self, service_type: str = "prepaid") -> dict:
        return {"service_type": service_type,
                "info": _PLAN_CATALOG.get(service_type.lower(), _PLAN_CATALOG["prepaid"])}

    # ── priority incidents & handoff ─────────────────────────────────────────
    def log_priority_incident(self, type: str, details: str) -> dict:
        iid = f"PRI{uuid.uuid4().hex[:8].upper()}"
        with self._conn() as c:
            c.execute("INSERT INTO incidents VALUES (?,?,?,?)",
                      (iid, type, details, datetime.now().isoformat()))
        log.warning("PRIORITY INCIDENT %s: %s @ %s", iid, type, details)
        return {"logged": True, "incident_id": iid,
                "note": "Fraud & security desk notified on priority."}

    def transfer_to_human(self, reason: str, context_summary: str = "") -> dict:
        log.info("TRANSFER: %s — %s", reason, context_summary)
        return {"transferred": True, "reason": reason,
                "note": "Caller queued to a senior human executive with full context."}

    def record_feedback(self, rating: str, comment: str = "") -> dict:
        fid = f"FB{uuid.uuid4().hex[:8].upper()}"
        with self._conn() as c:
            c.execute("INSERT INTO feedback VALUES (?,?,?,?)",
                      (fid, rating, comment, datetime.now().isoformat()))
        return {"recorded": True, "feedback_id": fid}
