"""AI → human call-transfer service (Exotel).

This is the ONLY module that knows how a live call is physically handed to a
senior executive. The conversation stack never talks to Exotel's transfer API
directly — it builds a TransferContext and calls `TransferService.transfer()`,
exactly like the notification layer builds a Ticket and calls a sender. Swapping
Exotel for another carrier means editing only this file.

Design (mirrors whatsapp_sender.py):
  • Fully configurable, nothing hardcoded. With EXOTEL_TRANSFER_ENABLED off or
    credentials missing, it runs in SIMULATION mode: the whole escalation flow —
    summary, WhatsApp handoff, UI stages, spoken message — still executes; only
    the real carrier dial is skipped and logged. So this ships and demos today.
  • Real path: Exotel's "Connect two numbers / connect a call to a number" API
    (POST /v1/Accounts/<sid>/Calls/connect). The live caller leg (CallSid) is
    bridged to the executive number, with the ExoPhone as CallerId and an
    optional StatusCallback so Exotel reports the transfer outcome to us.
  • Retries with backoff; never raises into the call path — a failed transfer
    returns a FAILED result so the caller can be offered a callback instead of
    being dropped into silence.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

import httpx

log = logging.getLogger(__name__)

_PENDING_TTL_S = 120     # a queued transfer is valid this long for Exotel to fetch


class TransferStatus(str, Enum):
    SIMULATED = "simulated"      # no creds — flow ran, real dial skipped
    INITIATED = "initiated"      # Exotel accepted the connect request
    FAILED = "failed"            # all attempts failed
    DISABLED = "disabled"        # transfer feature switched off


@dataclass
class TransferContext:
    """Everything the executive/CRM needs — assembled once, before the dial."""
    escalation_reason: str
    issue_category: str = ""
    issue_priority: str = "MEDIUM"
    summary: str = ""                    # structured, CRM-ready (see escalation.py)
    # customer identity (from verified call memory — never guessed)
    customer_name: str = ""
    customer_id: str = ""                # account number
    mobile: str = ""                     # registered mobile
    caller_number: str = ""              # number the call arrived from
    language: str = "und"
    complaint_id: str = ""
    verification_status: str = "not_verified"
    troubleshooting_done: str = ""
    # telephony linkage (present only on a real Exotel leg)
    call_sid: Optional[str] = None
    from_number: Optional[str] = None
    session_id: str = ""
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class TransferResult:
    status: TransferStatus
    executive: str = ""
    reference: str = ""
    attempts: int = 0
    error: str = ""
    detail: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in (TransferStatus.INITIATED, TransferStatus.SIMULATED)


class TransferService:
    """Isolated, swappable transfer backend. One instance per process."""

    def __init__(self, settings, client: Optional[httpx.AsyncClient] = None):
        self.s = settings
        self._client = client
        self._owns_client = client is None
        # FLOW MODE registry: call_sid → {number, executive, ts}. Set when the AI
        # escalates; read by the /exotel/transfer-destination endpoint that the
        # Connect applet fetches. Only escalated calls have an entry, so normal
        # calls get no number and simply end.
        self._pending: dict[str, dict] = {}

    # ── flow-mode pending-transfer registry ──────────────────────────────────
    def _prune(self) -> None:
        now = time.time()
        for k in [k for k, v in self._pending.items() if now - v["ts"] > _PENDING_TTL_S]:
            self._pending.pop(k, None)

    def record_pending(self, call_sid: str, number: str, executive: str,
                       summary: str = "") -> None:
        if not call_sid:
            return
        self._prune()
        self._pending[call_sid] = {"number": number, "executive": executive,
                                   "summary": summary, "ts": time.time()}

    def pending_for(self, call_sid: str) -> Optional[dict]:
        self._prune()
        return self._pending.get(call_sid)

    # ── configuration introspection ──────────────────────────────────────────
    def _api_key(self) -> str:
        return getattr(self.s, "exotel_transfer_api_key", "") or self.s.exotel_api_key

    def _api_token(self) -> str:
        return getattr(self.s, "exotel_transfer_api_token", "") or self.s.exotel_api_token

    @property
    def _creds_ready(self) -> bool:
        s = self.s
        return bool(
            getattr(s, "exotel_transfer_enabled", False)
            and s.exotel_sid and self._api_key() and self._api_token()
            and s.exotel_transfer_number
        )

    def _client_or_new(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=15.0)
        return self._client

    # ── public API ───────────────────────────────────────────────────────────
    async def transfer(self, ctx: TransferContext) -> TransferResult:
        """Hand the live call to a senior executive. Never raises."""
        label = getattr(self.s, "transfer_executive_label", "Senior Executive")
        if not getattr(self.s, "transfer_enabled", True):
            log.info("transfer: feature disabled — not transferring (call=%s)",
                     ctx.session_id)
            return TransferResult(TransferStatus.DISABLED, executive=label)

        # FLOW HAND-OFF (recommended, seamless) — on a real Exotel leg we do NOT
        # call the click-to-call API (which would re-dial both parties). Instead
        # the bot ends its stream and Exotel's call flow routes the SAME live
        # caller to the next applet — a Connect applet dialing the executive.
        # We just report success; the VoiceSession ending the stream does the work.
        mode = getattr(self.s, "exotel_transfer_mode", "flow")
        if (ctx.call_sid and mode == "flow"
                and getattr(self.s, "exotel_transfer_enabled", False)):
            # Register this call as escalated so the Connect applet's destination
            # fetch (/exotel/transfer-destination) returns the executive's number.
            self.record_pending(ctx.call_sid, self.s.exotel_transfer_number,
                                label, ctx.summary)
            log.info("transfer HANDOFF (flow) call=%s → executive=%s (%s) — ending "
                     "the Voicebot stream; Exotel's Connect applet fetches the "
                     "number and bridges the live caller (no re-dial)",
                     ctx.session_id, label, self.s.exotel_transfer_number)
            return TransferResult(TransferStatus.INITIATED, executive=label,
                                  reference=f"FLOW-{ctx.session_id[:8]}",
                                  detail={"mode": "flow"})

        # SIMULATION — no creds, or a browser/demo leg with no CallSid. The whole
        # escalation experience still runs; we just log the prepared payload so
        # you can verify exactly what WOULD be sent to Exotel.
        if not self._creds_ready or not ctx.call_sid:
            payload = self._build_payload(ctx)
            reason = ("credentials not configured" if not self._creds_ready
                      else "no call_sid (non-Exotel leg)")
            log.info("transfer SIMULATED (%s) call=%s → executive=%s | "
                     "prepared Exotel payload=%s", reason, ctx.session_id,
                     label, payload)
            return TransferResult(
                TransferStatus.SIMULATED, executive=label,
                reference=f"SIM-{ctx.session_id[:8]}", detail={"payload": payload})

        # REAL path — Exotel connect with retries.
        return await self._exotel_connect(ctx, label)

    # ── Exotel connect ───────────────────────────────────────────────────────
    def _build_payload(self, ctx: TransferContext) -> dict:
        """Exotel 'connect a live call to a number' request body. Kept here (not
        inline in the request) so SIMULATION mode can log the exact same thing a
        real call would send — the single source of truth for the carrier
        contract."""
        s = self.s
        # Transfer the EXISTING live leg (identified by CallSid) to the executive.
        # Deliberately NO `From`: sending the caller's number as From makes Exotel
        # place a brand-new call to them (the "both parties re-dialled" bug).
        # CallSid + To + CallerId tells Exotel to bridge the caller already on the
        # line to `To`.
        payload = {
            "CallSid": ctx.call_sid or "",
            "CallType": "trans",
            "To": s.exotel_transfer_number,
            "CallerId": s.exotel_caller_id or s.exotel_transfer_number,
        }
        if getattr(s, "exotel_transfer_callback_url", ""):
            payload["StatusCallback"] = s.exotel_transfer_callback_url
        return payload

    async def _exotel_connect(self, ctx: TransferContext,
                              label: str) -> TransferResult:
        s = self.s
        url = (f"https://{self._api_key()}:{self._api_token()}"
               f"@{s.exotel_subdomain}/v1/Accounts/{s.exotel_sid}/Calls/connect.json")
        payload = self._build_payload(ctx)
        client = self._client_or_new()
        attempts = 0
        last_err = ""
        max_attempts = max(1, getattr(s, "transfer_retry_max", 2))
        base = getattr(s, "transfer_retry_base_s", 1.5)
        for attempt in range(1, max_attempts + 1):
            attempts = attempt
            try:
                r = await client.post(url, data=payload)
                if r.status_code < 400:
                    ref = _extract_sid(r)
                    log.info("transfer INITIATED call=%s → %s (exotel_ref=%s, attempt %d)",
                             ctx.session_id, s.exotel_transfer_number, ref, attempt)
                    return TransferResult(TransferStatus.INITIATED, executive=label,
                                          reference=ref, attempts=attempt)
                last_err = f"HTTP {r.status_code}: {r.text[:200]}"
            except Exception as e:                       # noqa: BLE001
                last_err = str(e)[:200]
            log.warning("transfer attempt %d failed (call=%s): %s",
                        attempt, ctx.session_id, last_err)
            if attempt < max_attempts:
                await asyncio.sleep(base * attempt)
        log.error("transfer FAILED after %d attempts (call=%s): %s",
                  attempts, ctx.session_id, last_err)
        return TransferResult(TransferStatus.FAILED, executive=label,
                              attempts=attempts, error=last_err)

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None


def _extract_sid(resp: httpx.Response) -> str:
    """Best-effort pull of Exotel's Call Sid from a JSON or XML connect response."""
    try:
        data = resp.json()
        call = (data.get("Call") or data.get("call") or {})
        return call.get("Sid") or call.get("sid") or ""
    except Exception:                                    # noqa: BLE001
        import re
        m = re.search(r"<Sid>([^<]+)</Sid>", resp.text or "")
        return m.group(1) if m else ""
