"""REST endpoints: health, KB debug search, text-only chat (eval harness uses this)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ..conversation.manager import ConversationManager

log = logging.getLogger(__name__)
router = APIRouter()

# text-only sessions (debug/eval) keyed by id
_sessions: dict[str, ConversationManager] = {}


@router.get("/health")
async def health(request: Request):
    deps = request.app.state.deps
    return {
        "status": "ok",
        "model": deps.settings.gemini_model,
        "kb_chunks": len(deps.retriever.chunks),
        "keys": {"sarvam": bool(deps.settings.sarvam_api_key),
                 "gemini": bool(deps.settings.gemini_api_key)},
    }


@router.get("/tickets")
async def tickets(request: Request, q: str = "", limit: int = 100):
    """Ops dashboard: notification tickets with WhatsApp delivery status.
    Free-text q matches ticket id, customer, mobile, account, category,
    priority, status and summary."""
    svc = getattr(request.app.state.deps, "notifications", None)
    if svc is None:
        return {"tickets": []}
    return {"tickets": svc.store.search(q, min(limit, 500))}


@router.api_route("/exotel/transfer-destination", methods=["GET", "POST"])
async def exotel_transfer_destination(request: Request):
    """Exotel's Connect applet fetches this to decide whom to dial AFTER the
    Voicebot applet ends. Only calls the AI escalated have a pending entry, so:
      • escalated call  → returns the executive's number (Exotel bridges the
        SAME live caller — a true seamless transfer, no re-dial);
      • normal call     → returns an empty destination, so Exotel connects no one
        and the call simply ends.
    Exotel passes CallSid as a parameter; we match it to the pending registry."""
    params: dict = dict(request.query_params)
    try:
        form = await request.form()
        params.update({k: v for k, v in form.items()})
    except Exception:                                    # noqa: BLE001
        pass
    call_sid = (params.get("CallSid") or params.get("call_sid")
                or params.get("CallSidLegacy") or "")
    svc = getattr(request.app.state.deps, "transfer", None)
    pending = svc.pending_for(call_sid) if (svc and call_sid) else None
    log.info("exotel transfer-destination: call_sid=%s → %s (params=%s)",
             call_sid, "CONNECT " + pending["number"] if pending else "no-connect",
             {k: params.get(k) for k in ("CallSid", "From", "To", "CallFrom")})

    settings = request.app.state.deps.settings
    if not pending:
        # No escalation for this call → no destination → call ends normally.
        return {"fetch_after_attempt": False, "destination": {"numbers": []}}

    number = _exotel_dial_format(pending["number"])
    caller_id = getattr(settings, "exotel_caller_id", "") or ""
    # Return the number in the shapes Exotel's Connect-applet fetch accepts; extra
    # keys are ignored by Exotel, so this maximises compatibility.
    return {
        "fetch_after_attempt": False,
        "destination": {"numbers": [number]},
        "outgoing_phone_number": caller_id,
        "record": True,
    }


def _exotel_dial_format(number: str) -> str:
    """Domestic Exotel dialling wants a leading 0 on a 10-digit mobile."""
    n = "".join(ch for ch in str(number) if ch.isdigit())
    if len(n) == 10 and n[0] in "6789":
        return "0" + n
    return n


@router.api_route("/exotel/dtmf", methods=["GET", "POST"])
async def exotel_dtmf(request: Request):
    """Out-of-band keypad delivery for DUAL-INPUT number capture.

    Point an Exotel Gather / Passthru applet here (or any DTMF webhook) with the
    live CallSid plus the pressed key(s). We match CallSid to the active
    VoiceSession and inject the digit(s) into its capture buffer — the same path
    a streamed `dtmf` event takes. This makes the keypad work even when the
    account's Voicebot streaming applet does not forward inline DTMF.

    Accepts (query or form): CallSid, and either `digits` (a collected string
    like "9876543210") or `digit` (a single key). '*' and '#' are honoured."""
    from .ws_voice import session_for_call
    params: dict = dict(request.query_params)
    try:
        form = await request.form()
        params.update({k: v for k, v in form.items()})
    except Exception:                                    # noqa: BLE001
        pass
    call_sid = (params.get("CallSid") or params.get("call_sid") or "")
    digits = str(params.get("digits") or params.get("digit")
                 or params.get("Digits") or "")
    sess = session_for_call(call_sid)
    if sess is None:
        log.info("exotel dtmf webhook: no live session for call_sid=%s", call_sid)
        return {"ok": False, "reason": "no_active_session"}
    n = await sess.inject_dtmf(digits)
    log.info("exotel dtmf webhook: call_sid=%s injected %d digit(s)", call_sid, n)
    return {"ok": True, "injected": n}


@router.api_route("/exotel/transfer-status", methods=["GET", "POST"])
async def exotel_transfer_status(request: Request):
    """Callback Exotel posts the outcome of a call transfer to (configure as
    EXOTEL_TRANSFER_CALLBACK_URL). We log it for the ops trail; a real ACD/CRM
    integration would update the ticket's assigned-executive + final status here.
    Accepts form-encoded or query params (Exotel uses form posts)."""
    data: dict = dict(request.query_params)
    try:
        form = await request.form()
        data.update({k: v for k, v in form.items()})
    except Exception:                                    # noqa: BLE001
        pass
    log.info("exotel transfer-status callback: %s", data or "<empty>")
    return {"received": True}


@router.get("/kb/search")
async def kb_search(request: Request, q: str, category: str | None = None):
    return await request.app.state.deps.retriever.search(q, category)


@router.post("/kb/reload")
async def kb_reload(request: Request):
    await request.app.state.deps.retriever.build()
    return {"reloaded": True, "chunks": len(request.app.state.deps.retriever.chunks)}


class ChatIn(BaseModel):
    session_id: str
    text: str
    lang_hint: str = "unknown"


@router.post("/chat")
async def chat(request: Request, body: ChatIn):
    """Text-mode conversation — same manager, no audio. Used by evaluation/run_eval.py."""
    deps = request.app.state.deps
    mgr = _sessions.get(body.session_id)
    if mgr is None:
        mgr = ConversationManager(deps.settings, deps.llm, deps.tools, body.session_id)
        _sessions[body.session_id] = mgr
        greeting = mgr.greeting()
        if not body.text.strip():
            return {"replies": [greeting.text], "memory": mgr.memory.snapshot()}
    replies: list[str] = []
    async for chunk in mgr.run_turn(body.text, body.lang_hint):
        if chunk.kind == "sentence":
            replies.append(chunk.text)
    return {"replies": replies, "language": mgr.memory.language, "memory": mgr.memory.snapshot()}


@router.delete("/chat/{session_id}")
async def chat_reset(session_id: str):
    _sessions.pop(session_id, None)
    return {"reset": True}
