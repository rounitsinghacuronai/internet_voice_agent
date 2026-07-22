"""Per-call logging — one row per voice call in the same SQLite DB.

Written by the voice agent lifecycle (VoiceSession.run / _teardown) so EVERY
call shows up on the dashboard, not just the ones that raise a ticket. All
writes are best-effort: a logging failure must never affect a live call, so the
call sites wrap these in try/except and this module also swallows its own
sqlite errors.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

_STORES: dict[str, "CallLogStore"] = {}


def get_call_log(settings) -> "CallLogStore":
    """Process-wide singleton keyed by db path."""
    path = str(settings.db_path)
    store = _STORES.get(path)
    if store is None:
        store = CallLogStore(Path(path))
        _STORES[path] = store
    return store


class CallLogStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        try:
            with self._conn() as c:
                c.executescript("""
                CREATE TABLE IF NOT EXISTS calls(
                  session_id TEXT PRIMARY KEY,
                  call_sid TEXT, caller TEXT, receiving_number TEXT,
                  language TEXT, customer_name TEXT, account_no TEXT,
                  verified INTEGER DEFAULT 0,
                  intent TEXT, outcome TEXT, escalated INTEGER DEFAULT 0,
                  summary TEXT, turns INTEGER DEFAULT 0,
                  started_at TEXT, ended_at TEXT, duration_s INTEGER DEFAULT 0);
                CREATE INDEX IF NOT EXISTS idx_calls_started
                  ON calls(started_at DESC);
                """)
        except sqlite3.Error as e:  # pragma: no cover
            log.warning("call_log init failed: %s", e)

    def start(self, session_id: str, call_sid: str | None, caller: str,
              receiving_number: str) -> None:
        try:
            with self._conn() as c:
                c.execute("""
                  INSERT INTO calls(session_id, call_sid, caller,
                    receiving_number, started_at, outcome)
                  VALUES (?,?,?,?,?, 'in_progress')
                  ON CONFLICT(session_id) DO NOTHING
                """, (session_id, call_sid or "", caller or "",
                      receiving_number or "", datetime.now().isoformat()))
        except sqlite3.Error as e:  # pragma: no cover
            log.warning("call_log start failed: %s", e)

    def end(self, session_id: str, duration_s: int, snap: dict | None = None,
            outcome: str = "completed", escalated: bool = False,
            intent: str = "", summary: str = "", turns: int = 0) -> None:
        snap = snap or {}
        try:
            with self._conn() as c:
                c.execute("""
                  UPDATE calls SET
                    ended_at=?, duration_s=?, language=?, customer_name=?,
                    account_no=?, verified=?, outcome=?, escalated=?,
                    intent=?, summary=?, turns=?
                  WHERE session_id=?
                """, (datetime.now().isoformat(), int(duration_s),
                      snap.get("language") or "", snap.get("name") or "",
                      snap.get("account_no") or "", 1 if snap.get("verified") else 0,
                      outcome, 1 if escalated else 0, intent, summary, turns,
                      session_id))
        except sqlite3.Error as e:  # pragma: no cover
            log.warning("call_log end failed: %s", e)

    def search(self, q: str = "", limit: int = 200) -> list[dict]:
        sql = "SELECT * FROM calls"
        params: tuple = ()
        if q:
            like = f"%{q}%"
            sql += (" WHERE session_id LIKE ? OR call_sid LIKE ? OR caller LIKE ?"
                    " OR customer_name LIKE ? OR account_no LIKE ? OR intent LIKE ?")
            params = (like,) * 6
        sql += " ORDER BY started_at DESC LIMIT ?"
        try:
            with self._conn() as c:
                return [dict(r) for r in c.execute(sql, params + (limit,)).fetchall()]
        except sqlite3.Error as e:  # pragma: no cover
            log.warning("call_log search failed: %s", e)
            return []

    def get(self, session_id: str) -> dict | None:
        try:
            with self._conn() as c:
                row = c.execute("SELECT * FROM calls WHERE session_id=?",
                                (session_id,)).fetchone()
                return dict(row) if row else None
        except sqlite3.Error:  # pragma: no cover
            return None
