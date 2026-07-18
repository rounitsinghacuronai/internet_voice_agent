"""Delivery-status store + audit trail.

Two layers, both keyed by ticket_id and call_id:
  1. SQLite table `notifications` (same DB file as the rest of the agent) —
     queryable state for the ops dashboard (/tickets).
  2. Append-only JSONL audit log (`logs/notifications.jsonl`) — every state
     change, never rewritten, safe to ship to S3/CloudWatch.

Optional S3 mirror: set NOTIFY_S3_BUCKET (+ boto3 installed + AWS creds in the
environment) and every audit line is also queued to
s3://<bucket>/notifications/<YYYY-MM-DD>/<ticket_id>-<seq>.json. Failures are
logged and never propagate — AWS being down must not affect notifications,
let alone the call.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

# delivery states
PENDING, RETRYING, SENT, FAILED = "PENDING", "RETRYING", "SENT", "FAILED"


class NotificationStore:
    def __init__(self, db_path: Path, audit_dir: Path,
                 s3_bucket: str = "", s3_prefix: str = "notifications"):
        self.db_path = db_path
        self.audit_path = audit_dir / "notifications.jsonl"
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        self.s3_bucket = s3_bucket
        self.s3_prefix = s3_prefix
        self._seq = 0
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS notifications(
              ticket_id TEXT PRIMARY KEY,
              call_id TEXT, complaint_no TEXT, event_type TEXT, category TEXT,
              priority TEXT, customer_name TEXT, mobile TEXT, account_no TEXT,
              service_type TEXT, location TEXT, summary TEXT,
              status TEXT, attempts INTEGER DEFAULT 0,
              follow_up_count INTEGER DEFAULT 0,
              created_at TEXT, delivered_at TEXT, last_error TEXT);
            CREATE INDEX IF NOT EXISTS idx_notif_mobile
              ON notifications(mobile, category, created_at);
            """)

    # ── state persistence (dashboard reads this) ─────────────────────────────
    def upsert(self, t, status: str, attempts: int = 0,
               delivered_at: str | None = None, last_error: str = "") -> None:
        with self._conn() as c:
            c.execute("""
              INSERT INTO notifications(ticket_id, call_id, complaint_no,
                event_type, category, priority, customer_name, mobile,
                account_no, service_type, location, summary, status, attempts,
                follow_up_count, created_at, delivered_at, last_error)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(ticket_id) DO UPDATE SET
                status=excluded.status, attempts=excluded.attempts,
                follow_up_count=excluded.follow_up_count,
                summary=excluded.summary,
                delivered_at=COALESCE(excluded.delivered_at, delivered_at),
                last_error=excluded.last_error
            """, (t.ticket_id, t.call_id, t.complaint_no, t.event_type,
                  t.category, t.priority, t.customer_name, t.mobile,
                  t.account_no, t.service_type, t.location, t.summary, status,
                  attempts, t.follow_up_count, t.created_at.isoformat(),
                  delivered_at, last_error))

    def find_recent(self, mobile: str, account_no: str, category: str,
                    window_min: int) -> sqlite3.Row | None:
        """Duplicate detection: same customer + same category inside the window."""
        cutoff = datetime.now().timestamp() - window_min * 60
        with self._conn() as c:
            rows = c.execute("""
              SELECT * FROM notifications
              WHERE category=? AND (mobile=? OR (account_no != '' AND account_no=?))
              ORDER BY created_at DESC LIMIT 5
            """, (category, mobile or "-", account_no or "-")).fetchall()
        for r in rows:
            try:
                if datetime.fromisoformat(r["created_at"]).timestamp() >= cutoff:
                    return r
            except ValueError:
                continue
        return None

    def bump_follow_up(self, ticket_id: str) -> int:
        with self._conn() as c:
            c.execute("""UPDATE notifications
                         SET follow_up_count = follow_up_count + 1
                         WHERE ticket_id=?""", (ticket_id,))
            row = c.execute("SELECT follow_up_count FROM notifications "
                            "WHERE ticket_id=?", (ticket_id,)).fetchone()
        return int(row["follow_up_count"]) if row else 1

    def search(self, q: str = "", limit: int = 100) -> list[dict]:
        sql = "SELECT * FROM notifications"
        params: tuple = ()
        if q:
            like = f"%{q}%"
            sql += """ WHERE ticket_id LIKE ? OR customer_name LIKE ?
                       OR mobile LIKE ? OR account_no LIKE ? OR category LIKE ?
                       OR priority LIKE ? OR status LIKE ? OR summary LIKE ?"""
            params = (like,) * 8
        sql += " ORDER BY created_at DESC LIMIT ?"
        with self._conn() as c:
            return [dict(r) for r in c.execute(sql, params + (limit,)).fetchall()]

    # ── audit trail (append-only; every state change) ────────────────────────
    def audit(self, ticket_id: str, call_id: str, event: str, **extra) -> None:
        rec = {"ts": datetime.now().isoformat(), "ticket_id": ticket_id,
               "call_id": call_id, "event": event, **extra}
        line = json.dumps(rec, ensure_ascii=False)
        try:
            with self.audit_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError as e:
            log.error("notification audit write failed: %s", e)
        log.info("notify audit %s %s %s", ticket_id, event,
                 {k: v for k, v in extra.items() if k != "message"})
        if self.s3_bucket:
            self._seq += 1
            asyncio.get_event_loop().run_in_executor(
                None, self._s3_put, ticket_id, self._seq, line)

    def _s3_put(self, ticket_id: str, seq: int, line: str) -> None:
        try:
            import boto3                                    # optional
            key = (f"{self.s3_prefix}/{datetime.now():%Y-%m-%d}/"
                   f"{ticket_id}-{seq}.json")
            boto3.client("s3").put_object(
                Bucket=self.s3_bucket, Key=key,
                Body=line.encode("utf-8"),
                ContentType="application/json")
        except Exception as e:                              # noqa: BLE001
            log.warning("notification S3 mirror failed (%s) — local audit intact", e)
