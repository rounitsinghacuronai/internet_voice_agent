"""Admin write API — ticket actions, settings persistence, executives CRUD,
global search. Additive and isolated under /api/*. Uses the same SQLite DB.

Schema touches are additive only (ALTER ... ADD COLUMN / CREATE TABLE IF NOT
EXISTS) and guarded, so they are safe to run against the live DB.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .auth import require_role

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["admin"])

# role gates (low → high): viewer < executive < supervisor < admin < super_admin
_EXECUTIVE = require_role("executive")
_SUPERVISOR = require_role("supervisor")
_ADMIN = require_role("admin")


def _db(request: Request) -> Path:
    return Path(request.app.state.deps.settings.db_path)


def _conn(request: Request) -> sqlite3.Connection:
    conn = sqlite3.connect(_db(request))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema(c: sqlite3.Connection) -> None:
    # additive columns on notifications (ignore if they already exist)
    for col in ("assigned_executive TEXT", "resolution_notes TEXT"):
        try:
            c.execute(f"ALTER TABLE notifications ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass
    c.execute("""
      CREATE TABLE IF NOT EXISTS executives(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL, phone TEXT, email TEXT,
        role TEXT DEFAULT 'Executive', status TEXT DEFAULT 'available',
        created_at TEXT)""")


# ── ticket actions ───────────────────────────────────────────────────────────
class StatusIn(BaseModel):
    status: str


class AssignIn(BaseModel):
    executive: str


class NotesIn(BaseModel):
    notes: str


def _update_ticket(request: Request, ticket_id: str, field: str, value: str) -> dict:
    with _conn(request) as c:
        _ensure_schema(c)
        cur = c.execute(f"UPDATE notifications SET {field}=? WHERE ticket_id=?", (value, ticket_id))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="ticket_not_found")
        row = c.execute("SELECT * FROM notifications WHERE ticket_id=?", (ticket_id,)).fetchone()
    return dict(row)


@router.post("/tickets/{ticket_id}/status")
async def set_status(request: Request, ticket_id: str, body: StatusIn, _=Depends(_EXECUTIVE)):
    return {"ticket": _update_ticket(request, ticket_id, "status", body.status.upper())}


@router.post("/tickets/{ticket_id}/assign")
async def assign(request: Request, ticket_id: str, body: AssignIn, _=Depends(_EXECUTIVE)):
    return {"ticket": _update_ticket(request, ticket_id, "assigned_executive", body.executive)}


@router.post("/tickets/{ticket_id}/notes")
async def notes(request: Request, ticket_id: str, body: NotesIn, _=Depends(_EXECUTIVE)):
    return {"ticket": _update_ticket(request, ticket_id, "resolution_notes", body.notes)}


# ── settings (persisted to JSON next to the DB) ──────────────────────────────
def _settings_path(request: Request) -> Path:
    return _db(request).parent / "admin_settings.json"


_DEFAULT_SETTINGS = {
    "company_name": "Syncbroad Networks",
    "brand_color": "#3b82f6",
    "business_hours": "09:00 – 21:00 IST",
    "languages": ["Hindi", "Marathi", "English"],
    "executive_transfer_number": "",
    "voice_pace": 1.2,
    "prompt_version": "prompt-v11-multilingual",
    "kb_version": "v4.2.1",
    "barge_in": True,
    "dark_mode_default": True,
}


@router.get("/settings")
async def get_settings(request: Request):
    p = _settings_path(request)
    data = dict(_DEFAULT_SETTINGS)
    if p.exists():
        try:
            data.update(json.loads(p.read_text()))
        except (OSError, json.JSONDecodeError):
            pass
    # reflect the live pace actually in effect
    try:
        data["voice_pace"] = getattr(request.app.state.deps.settings, "tts_pace", data["voice_pace"])
    except Exception:  # noqa: BLE001
        pass
    return {"settings": data}


@router.post("/settings")
async def save_settings(request: Request, body: dict, _=Depends(_ADMIN)):
    p = _settings_path(request)
    current = dict(_DEFAULT_SETTINGS)
    if p.exists():
        try:
            current.update(json.loads(p.read_text()))
        except (OSError, json.JSONDecodeError):
            pass
    current.update({k: v for k, v in (body or {}).items() if k in _DEFAULT_SETTINGS})
    try:
        p.write_text(json.dumps(current, indent=2, ensure_ascii=False))
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"could not save: {e}")

    # apply voice pace to the RUNNING agent so subsequent calls use it
    try:
        pace = float(current.get("voice_pace"))
        s = request.app.state.deps.settings
        s.tts_pace = pace
        if getattr(s, "speech_pace_max", 0) < pace:
            s.speech_pace_max = pace
        log.info("admin: applied live voice pace = %.2f", pace)
    except (TypeError, ValueError):
        pass
    return {"settings": current, "saved": True}


# ── executives CRUD ──────────────────────────────────────────────────────────
class ExecIn(BaseModel):
    name: str
    phone: str = ""
    email: str = ""
    role: str = "Executive"
    status: str = "available"


@router.get("/executives")
async def list_execs(request: Request):
    with _conn(request) as c:
        _ensure_schema(c)
        rows = [dict(r) for r in c.execute("SELECT * FROM executives ORDER BY name").fetchall()]
    return {"executives": rows}


@router.post("/executives")
async def create_exec(request: Request, body: ExecIn, _=Depends(_SUPERVISOR)):
    with _conn(request) as c:
        _ensure_schema(c)
        cur = c.execute(
            "INSERT INTO executives(name, phone, email, role, status, created_at) VALUES (?,?,?,?,?,?)",
            (body.name, body.phone, body.email, body.role, body.status, datetime.now().isoformat()))
        row = c.execute("SELECT * FROM executives WHERE id=?", (cur.lastrowid,)).fetchone()
    return {"executive": dict(row)}


@router.put("/executives/{exec_id}")
async def update_exec(request: Request, exec_id: int, body: ExecIn, _=Depends(_SUPERVISOR)):
    with _conn(request) as c:
        _ensure_schema(c)
        cur = c.execute(
            "UPDATE executives SET name=?, phone=?, email=?, role=?, status=? WHERE id=?",
            (body.name, body.phone, body.email, body.role, body.status, exec_id))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="executive_not_found")
        row = c.execute("SELECT * FROM executives WHERE id=?", (exec_id,)).fetchone()
    return {"executive": dict(row)}


@router.delete("/executives/{exec_id}")
async def delete_exec(request: Request, exec_id: int, _=Depends(_SUPERVISOR)):
    with _conn(request) as c:
        _ensure_schema(c)
        c.execute("DELETE FROM executives WHERE id=?", (exec_id,))
    return {"deleted": True}


# ── knowledge base reload (supervisor+) ──────────────────────────────────────
@router.post("/kb/reload")
async def kb_reload(request: Request, _=Depends(_SUPERVISOR)):
    await request.app.state.deps.retriever.build()
    return {"reloaded": True, "chunks": len(request.app.state.deps.retriever.chunks)}


# ── global search ────────────────────────────────────────────────────────────
@router.get("/search")
async def search(request: Request, q: str = "", limit: int = 8):
    q = (q or "").strip()
    if not q:
        return {"results": []}
    like = f"%{q}%"
    results: list[dict] = []
    with _conn(request) as c:
        for r in c.execute(
            "SELECT account_no, name, mobile FROM customers "
            "WHERE name LIKE ? OR mobile LIKE ? OR account_no LIKE ? LIMIT ?",
            (like, like, like, limit)).fetchall():
            results.append({"type": "customer", "title": r["name"],
                            "subtitle": f'{r["mobile"]} · {r["account_no"]}',
                            "href": f'/customers/{r["account_no"]}'})
        try:
            for r in c.execute(
                "SELECT ticket_id, category, customer_name, mobile FROM notifications "
                "WHERE ticket_id LIKE ? OR customer_name LIKE ? OR mobile LIKE ? OR category LIKE ? LIMIT ?",
                (like, like, like, like, limit)).fetchall():
                results.append({"type": "ticket", "title": r["ticket_id"],
                                "subtitle": f'{r["category"]} · {r["customer_name"] or r["mobile"] or ""}',
                                "href": f'/tickets/{r["ticket_id"]}'})
        except sqlite3.Error:
            pass
    return {"results": results[: limit * 2]}
