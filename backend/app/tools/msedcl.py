"""Mock MSEDCL live-data services (in-process; real APIs swap in behind the same
signatures). SQLite-backed, seeded with realistic Maharashtra consumers. SOP hours are
the REAL values from the CCCC training manual (pp. 34-36)."""
from __future__ import annotations

import logging
import random
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

# ── Real SOP resolution hours (training manual pp.34-36): category → (L1, L2, final) ──
SOP_HOURS: dict[str, tuple[int, int, int]] = {
    "11 KV - Overhead line breakdown": (11, 15, 29),
    "22 KV - Overhead line breakdown": (11, 15, 29),
    "415 volts - Overhead line breakdown": (11, 15, 29),
    "Average bill": (720, 720, 720),
    "Cable fault": (25, 29, 33),
    "High Bill": (720, 720, 720),
    "Late Bill Receipt": (720, 720, 720),
    "Light fluctuation": (48, 96, 192),
    "Line - Snapped": (4, 8, 12),
    "Low Bill": (720, 720, 720),
    "Meter Burnt": (30, 40, 64),
    "Meter not functioning (No Display)": (184, 256, 328),
    "Meter Reading - Correction": (720, 720, 720),
    "Meter reading not taken": (720, 720, 720),
    "Meter Stuck up / Stop": (184, 256, 328),
    "Non receipt of Bill": (24, 24, 24),
    "Pole - Fell Down": (4, 8, 12),
    "Pole - Leaning": (4, 8, 12),
    "Pole - Shock": (0, 2, 2),
    "Supply Failed - Phase out": (8, 13, 28),
    "Supply Failed - Total Area": (11, 15, 29),
    "Transformer - Burnt": (30, 30, 40),
    "Transformer - Flames": (0, 0, 24),
    "Transformer - Smoke": (0, 6, 8),
    "Transformer - sparking": (0, 6, 8),
    "Voltage - Fluctuation": (48, 96, 144),
    "Voltage - High": (48, 96, 144),
    "Voltage - Dim Supply": (48, 96, 144),
    "Accident - Human Fatal": (0, 0, 2),
    "Accident - Non Fatal": (0, 0, 4),
    "Theft Related Complaint": (720, 720, 720),
    "Change Of Name": (1440, 1440, 1440),
    "Extension / Reduction of Load": (1440, 1440, 1440),
    "New Connection": (720, 888, 1056),
    "DT/Pillar Box-Sparking": (11, 6, 12),
}

VALID_CATEGORIES = set(SOP_HOURS)

_SEED_CONSUMERS = [
    # (consumer_no, name, mobile, address, category, sanctioned_load, meter_status, last_reading_type)
    ("170012345678", "Ramesh Patil", "9820012345", "Kothrud, Pune", "residential", "3 KW", "OK", "normal"),
    ("170023456789", "Sunita Deshmukh", "9822233445", "Hadapsar, Pune", "residential", "5 KW", "OK", "average"),
    ("210034567890", "Abdul Sheikh", "9867554433", "Bhiwandi, Thane", "commercial", "10 KW", "STUCK", "normal"),
    ("330045678901", "Kavita Jadhav", "9700112233", "Nanded City", "residential", "3 KW", "OK", "normal"),
    ("410056789012", "Suresh Wagh", "9922334455", "CIDCO, Chh. Sambhajinagar", "agricultural", "7.5 HP", "OK", "normal"),
]

_OUTAGES = [
    ("Kothrud, Pune", "11 KV feeder maintenance", 3),
    ("Bhiwandi, Thane", "Transformer replacement", 5),
]

_TARIFF = {
    "residential": "LT-I residential: telescopic slabs 0-100, 101-300, 301-500, 500+ units; "
                   "fixed charge + energy charge + FAC per MERC tariff order. Exact per-unit "
                   "rates vary by tariff order — quote from the current bill, never from memory.",
    "commercial": "LT-II commercial: demand + energy charges per MERC order, FAC applies.",
    "agricultural": "LT-IV AG: per-HP or metered per MERC order; solar AG pumps under Magel Tyala Saur Krushi Pump.",
    "industrial": "LT-V industrial: demand + energy charges, ToD slots per MERC order.",
}


class MsedclServices:
    """All 14 live tools + helpers. One SQLite file, thread-safe usage from asyncio
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
            CREATE TABLE IF NOT EXISTS consumers(
              consumer_no TEXT PRIMARY KEY, name TEXT, mobile TEXT, address TEXT,
              category TEXT, load TEXT, meter_status TEXT, last_reading_type TEXT);
            CREATE TABLE IF NOT EXISTS complaints(
              sr_no TEXT PRIMARY KEY, consumer_no TEXT, category TEXT, description TEXT,
              status TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS otps(mobile TEXT PRIMARY KEY, otp TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS incidents(
              id TEXT PRIMARY KEY, type TEXT, location TEXT, created_at TEXT);
            """)
            if not c.execute("SELECT 1 FROM consumers LIMIT 1").fetchone():
                c.executemany("INSERT INTO consumers VALUES (?,?,?,?,?,?,?,?)", _SEED_CONSUMERS)
                log.info("Seeded %d consumers", len(_SEED_CONSUMERS))

    # ── 14 live tools ────────────────────────────────────────────────────────
    def verify_consumer(self, consumer_no: str = "", mobile: str = "") -> dict:
        with self._conn() as c:
            row = None
            if consumer_no:
                row = c.execute("SELECT * FROM consumers WHERE consumer_no=?", (consumer_no.strip(),)).fetchone()
            if row is None and mobile:
                row = c.execute("SELECT * FROM consumers WHERE mobile=?", (mobile.strip(),)).fetchone()
        if row is None:
            return {"verified": False, "reason": "No consumer found for the given number."}
        return {"verified": True, "consumer_no": row["consumer_no"], "name": row["name"],
                "mobile": row["mobile"], "address": row["address"], "category": row["category"]}

    def send_otp(self, mobile: str) -> dict:
        otp = f"{random.randint(0, 999999):06d}"
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO otps VALUES (?,?,?)", (mobile, otp, datetime.now().isoformat()))
        log.info("OTP for %s: %s (mock — printed, not SMSed)", mobile, otp)
        return {"sent": True, "mobile": mobile, "mock_otp_for_demo": otp}

    def verify_otp(self, mobile: str, otp: str) -> dict:
        with self._conn() as c:
            row = c.execute("SELECT otp FROM otps WHERE mobile=?", (mobile,)).fetchone()
        ok = bool(row and row["otp"] == otp.strip())
        return {"verified": ok}

    def get_bill(self, consumer_no: str) -> dict:
        with self._conn() as c:
            row = c.execute("SELECT * FROM consumers WHERE consumer_no=?", (consumer_no,)).fetchone()
        if row is None:
            return {"error": "consumer_not_found"}
        rng = random.Random(consumer_no)  # stable per consumer
        units = rng.randint(80, 450)
        amount = round(units * rng.uniform(7.5, 11.0), 0)
        month = (datetime.now().replace(day=1) - timedelta(days=1)).strftime("%B %Y")
        prev_type = row["last_reading_type"]
        return {"consumer_no": consumer_no, "bill_month": month, "units": units,
                "amount_rs": amount, "due_date": (datetime.now() + timedelta(days=10)).strftime("%d %b %Y"),
                "previous_bill_type": prev_type,          # "average" → true-up explanation
                "prompt_payment_discount_pct": 1.0, "digital_payment_discount_pct": 0.25}

    def get_payment_status(self, consumer_no: str, txn_ref: str = "") -> dict:
        rng = random.Random((consumer_no or "") + (txn_ref or ""))
        status = rng.choice(["SUCCESS", "PENDING", "FAILED_DEBITED"])
        out = {"consumer_no": consumer_no, "txn_ref": txn_ref or f"TXN{rng.randint(10**9, 10**10-1)}",
               "status": status}
        if status == "FAILED_DEBITED":
            out["note"] = "Transaction failed but amount debited — auto-reversal in 5 to 7 working days."
        return out

    def register_complaint(self, consumer_no: str, category: str, description: str) -> dict:
        if category not in VALID_CATEGORIES:
            return {"error": "invalid_category",
                    "valid_examples": sorted(VALID_CATEGORIES)[:12]}
        sr = f"SR{datetime.now():%y%m}{uuid.uuid4().hex[:6].upper()}"
        with self._conn() as c:
            c.execute("INSERT INTO complaints VALUES (?,?,?,?,?,?)",
                      (sr, consumer_no, category, description, "REGISTERED", datetime.now().isoformat()))
        hours = SOP_HOURS[category][0]
        return {"sr_no": sr, "category": category, "status": "REGISTERED",
                "sop_hours": hours,
                "note": f"SR sent by SMS to registered mobile. Standard resolution {hours} hours; "
                        "auto-escalates to higher authority if delayed."}

    def track_complaint(self, complaint_no: str) -> dict:
        with self._conn() as c:
            row = c.execute("SELECT * FROM complaints WHERE sr_no=?", (complaint_no.strip(),)).fetchone()
        if row is None:
            return {"error": "complaint_not_found"}
        return {"sr_no": row["sr_no"], "category": row["category"], "status": row["status"],
                "created_at": row["created_at"]}

    def get_outage(self, area: str = "", consumer_no: str = "") -> dict:
        if consumer_no:
            with self._conn() as c:
                row = c.execute("SELECT address FROM consumers WHERE consumer_no=?", (consumer_no,)).fetchone()
            area = row["address"] if row else area
        for out_area, reason, eta_h in _OUTAGES:
            if area and (out_area.lower() in area.lower() or area.lower() in out_area.lower()):
                eta = (datetime.now() + timedelta(hours=eta_h)).strftime("%I:%M %p")
                return {"outage": True, "area": out_area, "reason": reason,
                        "expected_restoration": eta, "eta_hours": eta_h}
        return {"outage": False, "area": area,
                "note": "No area-level outage on record. If the caller's supply is off, it is "
                        "likely an individual fault — offer to register Supply Failed - Phase out "
                        "(only their premises) or Supply Failed - Total Area (whole locality)."}

    def get_meter_details(self, consumer_no: str) -> dict:
        with self._conn() as c:
            row = c.execute("SELECT * FROM consumers WHERE consumer_no=?", (consumer_no,)).fetchone()
        if row is None:
            return {"error": "consumer_not_found"}
        return {"consumer_no": consumer_no, "meter_status": row["meter_status"],
                "sanctioned_load": row["load"],
                "last_reading_type": row["last_reading_type"]}

    def get_new_connection_status(self, application_no: str) -> dict:
        rng = random.Random(application_no)
        stage = rng.choice(["Document verification", "Site inspection scheduled",
                            "Estimate issued — payment pending", "Meter installation scheduled"])
        return {"application_no": application_no, "stage": stage}

    def request_load_change(self, consumer_no: str, new_load: str) -> dict:
        ref = f"LC{uuid.uuid4().hex[:8].upper()}"
        return {"submitted": True, "reference": ref, "new_load": new_load,
                "sop_hours": SOP_HOURS["Extension / Reduction of Load"][0]}

    def request_name_change(self, consumer_no: str, new_name: str) -> dict:
        ref = f"NC{uuid.uuid4().hex[:8].upper()}"
        return {"submitted": True, "reference": ref, "new_name": new_name,
                "note": "U-Form with ownership proof needed at Sub-Division Office; "
                        f"standard processing {SOP_HOURS['Change Of Name'][0]} hours."}

    def get_tariff_info(self, category: str = "residential") -> dict:
        return {"category": category, "info": _TARIFF.get(category.lower(), _TARIFF["residential"])}

    def log_safety_incident(self, type: str, location: str) -> dict:
        iid = f"EMG{uuid.uuid4().hex[:8].upper()}"
        with self._conn() as c:
            c.execute("INSERT INTO incidents VALUES (?,?,?,?)", (iid, type, location, datetime.now().isoformat()))
        log.warning("SAFETY INCIDENT %s: %s @ %s", iid, type, location)
        return {"logged": True, "incident_id": iid,
                "note": "Emergency team notified; field crew dispatched on priority."}

    def transfer_to_human(self, reason: str, context_summary: str = "") -> dict:
        log.info("TRANSFER: %s — %s", reason, context_summary)
        return {"transferred": True, "reason": reason,
                "note": "Caller queued to a senior human executive with full context."}
