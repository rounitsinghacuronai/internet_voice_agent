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
