"""JWT auth + role-based access control for the admin dashboard.

Self-contained (stdlib only — no PyJWT dependency): HMAC-SHA256 signed tokens,
PBKDF2 password hashing, a `users` table in the same SQLite DB. Applied only to
the /api/* admin endpoints; the voice agent and its webhooks stay open.

Roles (low → high): viewer < executive < supervisor < admin < super_admin.
A default super-admin is seeded on first use (ADMIN_USER / ADMIN_PASS env,
default admin / admin — change it).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])

ROLES = ["viewer", "executive", "supervisor", "admin", "super_admin"]
_RANK = {r: i for i, r in enumerate(ROLES)}
_TOKEN_TTL = 12 * 3600
_seeded: set[str] = set()


# ── secret (stable across restarts) ──────────────────────────────────────────
def _secret(db_path: Path) -> bytes:
    env = os.getenv("JWT_SECRET")
    if env:
        return env.encode()
    f = db_path.parent / ".jwt_secret"
    try:
        if f.exists():
            return f.read_bytes()
        sec = os.urandom(32)
        f.write_bytes(sec)
        return sec
    except OSError:
        return b"insecure-dev-secret-change-me"


# ── password hashing (PBKDF2) ────────────────────────────────────────────────
def hash_pw(pw: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 120_000)
    return f"{salt.hex()}${dk.hex()}"


def verify_pw(pw: str, stored: str) -> bool:
    try:
        salt_hex, hash_hex = stored.split("$", 1)
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt_hex), 120_000)
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


# ── JWT (HS256, stdlib) ──────────────────────────────────────────────────────
def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(seg: str) -> bytes:
    return base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4))


def make_token(claims: dict, secret: bytes) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {**claims, "exp": int(time.time()) + _TOKEN_TTL}
    segs = [_b64(json.dumps(header).encode()), _b64(json.dumps(payload).encode())]
    sig = hmac.new(secret, ".".join(segs).encode(), hashlib.sha256).digest()
    segs.append(_b64(sig))
    return ".".join(segs)


def verify_token(token: str, secret: bytes) -> dict | None:
    try:
        h, p, s = token.split(".")
        expected = hmac.new(secret, f"{h}.{p}".encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(_unb64(s), expected):
            return None
        payload = json.loads(_unb64(p))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


# ── users store ──────────────────────────────────────────────────────────────
def _conn(request: Request) -> sqlite3.Connection:
    conn = sqlite3.connect(request.app.state.deps.settings.db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_users(request: Request) -> None:
    dbp = str(request.app.state.deps.settings.db_path)
    with _conn(request) as c:
        c.execute("""
          CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
            name TEXT, role TEXT DEFAULT 'viewer', created_at TEXT)""")
        if dbp not in _seeded:
            n = c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
            if n == 0:
                user = os.getenv("ADMIN_USER", "admin")
                pw = os.getenv("ADMIN_PASS", "admin")
                c.execute("INSERT INTO users(username, password_hash, name, role, created_at) VALUES (?,?,?,?,?)",
                          (user, hash_pw(pw), "Administrator", "super_admin", datetime.now().isoformat()))
                log.warning("auth: seeded default super-admin '%s' (change the password!)", user)
            _seeded.add(dbp)


# ── dependencies ─────────────────────────────────────────────────────────────
def require_auth(request: Request, authorization: str = Header(default="")) -> dict:
    token = authorization[7:] if authorization.lower().startswith("bearer ") else authorization
    secret = _secret(Path(request.app.state.deps.settings.db_path))
    claims = verify_token(token, secret) if token else None
    if not claims:
        raise HTTPException(status_code=401, detail="not_authenticated")
    return claims


def require_role(min_role: str):
    def dep(request: Request, authorization: str = Header(default="")) -> dict:
        claims = require_auth(request, authorization)
        if _RANK.get(claims.get("role", "viewer"), 0) < _RANK.get(min_role, 99):
            raise HTTPException(status_code=403, detail=f"requires_{min_role}")
        return claims
    return dep


# ── endpoints ────────────────────────────────────────────────────────────────
class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(request: Request, body: LoginIn):
    _ensure_users(request)
    with _conn(request) as c:
        row = c.execute("SELECT * FROM users WHERE username=?", (body.username,)).fetchone()
    if row is None or not verify_pw(body.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="invalid_credentials")
    secret = _secret(Path(request.app.state.deps.settings.db_path))
    user = {"id": row["id"], "username": row["username"], "name": row["name"], "role": row["role"]}
    return {"token": make_token({"sub": row["username"], **user}, secret), "user": user}


@router.get("/me")
async def me(request: Request, authorization: str = Header(default="")):
    claims = require_auth(request, authorization)
    return {"user": {"username": claims.get("username"), "name": claims.get("name"), "role": claims.get("role")}}


# ── user management (super-admin only) ───────────────────────────────────────
class UserIn(BaseModel):
    username: str
    password: str = ""
    name: str = ""
    role: str = "viewer"


@router.get("/users")
async def list_users(request: Request, authorization: str = Header(default="")):
    require_role("super_admin")(request, authorization)
    _ensure_users(request)
    with _conn(request) as c:
        rows = [dict(r) for r in c.execute("SELECT id, username, name, role, created_at FROM users ORDER BY username").fetchall()]
    return {"users": rows}


@router.post("/users")
async def create_user(request: Request, body: UserIn, authorization: str = Header(default="")):
    require_role("super_admin")(request, authorization)
    _ensure_users(request)
    if body.role not in ROLES:
        raise HTTPException(status_code=400, detail="invalid_role")
    if not body.password:
        raise HTTPException(status_code=400, detail="password_required")
    try:
        with _conn(request) as c:
            cur = c.execute("INSERT INTO users(username, password_hash, name, role, created_at) VALUES (?,?,?,?,?)",
                            (body.username, hash_pw(body.password), body.name, body.role, datetime.now().isoformat()))
            row = c.execute("SELECT id, username, name, role FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="username_taken")
    return {"user": dict(row)}


@router.delete("/users/{user_id}")
async def delete_user(request: Request, user_id: int, authorization: str = Header(default="")):
    claims = require_role("super_admin")(request, authorization)
    with _conn(request) as c:
        row = c.execute("SELECT username, role FROM users WHERE id=?", (user_id,)).fetchone()
        if row and row["username"] == claims.get("username"):
            raise HTTPException(status_code=400, detail="cannot_delete_self")
        if row and row["role"] == "super_admin":
            n = c.execute("SELECT COUNT(*) n FROM users WHERE role='super_admin'").fetchone()["n"]
            if n <= 1:
                raise HTTPException(status_code=400, detail="cannot_delete_last_admin")
        c.execute("DELETE FROM users WHERE id=?", (user_id,))
    return {"deleted": True}
