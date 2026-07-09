"""Tool registry: OpenAI-style schemas + dispatch + HARD GATES.

Gates are code, not prompt (lesson from run 192, where the model invented a complaint
number after a failed verification):
  • verify-gate : writes (register_complaint / name / load change) refuse unless
                  verify_consumer succeeded within VERIFY_TTL_S — enforced HERE.
  • otp-gate    : name/load changes additionally require verify_otp success.
  • safety tools are NEVER gated.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable

from ..config import Settings
from ..conversation.memory import CallMemory
from .msedcl import MsedclServices

log = logging.getLogger(__name__)

_WRITE_TOOLS = {"register_complaint", "request_name_change", "request_load_change"}
_OTP_TOOLS = {"request_name_change", "request_load_change"}
_UNGATED = {"verify_consumer", "send_otp", "verify_otp", "get_new_connection_status",
            "get_tariff_info", "track_complaint", "log_safety_incident",
            "transfer_to_human", "search_knowledge"}


def _fn(name: str, desc: str, props: dict, required: list[str] | None = None) -> dict:
    return {"type": "function",
            "function": {"name": name, "description": desc,
                         "parameters": {"type": "object", "properties": props,
                                        "required": required or []}}}


def build_schemas() -> list[dict]:
    S = {"type": "string"}
    return [
        _fn("search_knowledge",
            "Retrieve grounded MSEDCL policy/procedure/SOP/safety knowledge: billing rules, "
            "discounts, complaint process, forms, escalation times, safety guidance, app/website "
            "help. Use for HOW-things-work questions — never for a specific consumer's live data.",
            {"query": {**S, "description": "The caller's question, any language."},
             "category": {**S, "description": "Optional filter: billing|safety|complaints|connections|general."}},
            ["query"]),
        _fn("verify_consumer",
            "Look up and verify the caller by 12-digit consumer number or registered mobile. "
            "Call this the moment the caller provides either number.",
            {"consumer_no": S, "mobile": S}),
        _fn("send_otp", "Send OTP to the registered mobile. ONLY needed before a name change "
            "or load change — never to read information.", {"mobile": S}, ["mobile"]),
        _fn("verify_otp", "Verify the OTP the caller reads back.", {"mobile": S, "otp": S}, ["mobile", "otp"]),
        _fn("get_bill", "Latest bill: month, units, amount, due date, whether previous bill was "
            "average/estimated.", {"consumer_no": S}, ["consumer_no"]),
        _fn("get_payment_status", "Status of a bill payment (success / pending / failed-but-debited).",
            {"consumer_no": S, "txn_ref": S}, ["consumer_no"]),
        _fn("register_complaint",
            "Register a complaint. YOU pick the exact category (e.g. 'Supply Failed - Phase out', "
            "'Supply Failed - Total Area', 'High Bill', 'Meter Stuck up / Stop', 'Theft Related "
            "Complaint') and YOU write the one-line description from what the caller said. "
            "Requires prior successful verification.",
            {"consumer_no": S, "category": S, "description": S},
            ["consumer_no", "category", "description"]),
        _fn("track_complaint", "Track an existing complaint by SR number.", {"complaint_no": S}, ["complaint_no"]),
        _fn("get_outage", "Check for a known area outage (by consumer number or area name) with "
            "restoration ETA.", {"area": S, "consumer_no": S}),
        _fn("get_meter_details", "Meter status (OK/STUCK/BURNT), sanctioned load, last reading type.",
            {"consumer_no": S}, ["consumer_no"]),
        _fn("get_new_connection_status", "Stage of a new-connection application. No verification needed.",
            {"application_no": S}, ["application_no"]),
        _fn("request_load_change", "Submit load extension/reduction. Requires verification + OTP.",
            {"consumer_no": S, "new_load": S}, ["consumer_no", "new_load"]),
        _fn("request_name_change", "Submit change of name. Requires verification + OTP.",
            {"consumer_no": S, "new_name": S}, ["consumer_no", "new_name"]),
        _fn("get_tariff_info", "Live tariff structure notes for a consumer category. Never quote "
            "per-unit rates from memory.", {"category": S}),
        _fn("log_safety_incident", "Log an electrical safety emergency (wire down, shock, "
            "transformer fire, pole collapse, sparking). Never gated.", {"type": S, "location": S},
            ["type", "location"]),
        _fn("transfer_to_human", "Transfer to a senior human executive with a one-line context summary.",
            {"reason": S, "context_summary": S}, ["reason"]),
    ]


class ToolRegistry:
    def __init__(self, settings: Settings, services: MsedclServices, retriever=None):
        self.s = settings
        self.svc = services
        self.retriever = retriever          # injected after RAG init
        self.schemas = build_schemas()
        self._map: dict[str, Callable[..., Any]] = {
            n: getattr(services, n) for n in (
                "verify_consumer", "send_otp", "verify_otp", "get_bill", "get_payment_status",
                "register_complaint", "track_complaint", "get_outage", "get_meter_details",
                "get_new_connection_status", "request_load_change", "request_name_change",
                "get_tariff_info", "log_safety_incident", "transfer_to_human")
        }

    async def dispatch(self, name: str, args: dict, memory: CallMemory) -> dict:
        start = time.perf_counter()
        result = await self._dispatch_inner(name, args, memory)
        latency = (time.perf_counter() - start) * 1000
        log.info("tool %s %.0fms → %s", name, latency, _short(result))
        memory.absorb_tool_result(name, args, result)
        return result

    async def _dispatch_inner(self, name: str, args: dict, memory: CallMemory) -> dict:
        if name == "search_knowledge":
            if self.retriever is None:
                return {"error": "knowledge_unavailable"}
            return await self.retriever.search(args.get("query", ""), args.get("category"))

        fn = self._map.get(name)
        if fn is None:
            return {"error": f"unknown_tool:{name}"}

        # ── hard gates ──
        if name in _WRITE_TOOLS and name not in _UNGATED:
            if not memory.verify_fresh(self.s.verify_ttl_s):
                return {"error": "verification_required",
                        "message": "Refused: consumer identity not verified in this call. "
                                   "Verify with verify_consumer first; never invent a result."}
            if name in _OTP_TOOLS and not memory.otp_verified:
                return {"error": "otp_required",
                        "message": "Refused: this change needs OTP confirmation. "
                                   "Use send_otp then verify_otp first."}
            # writes always use the VERIFIED consumer number, not whatever the model typed
            if memory.consumer_no:
                args["consumer_no"] = memory.consumer_no

        try:
            result = await asyncio.to_thread(fn, **_clean(args, fn))
        except TypeError as e:
            return {"error": "bad_arguments", "detail": str(e)}
        except Exception as e:  # pragma: no cover
            log.exception("tool %s failed", name)
            return {"error": "tool_failure", "detail": str(e)}
        return result


def _clean(args: dict, fn: Callable) -> dict:
    import inspect
    params = inspect.signature(fn).parameters
    return {k: v for k, v in args.items() if k in params and v is not None}


def _short(obj: dict) -> str:
    s = json.dumps(obj, ensure_ascii=False)
    return s[:180] + ("…" if len(s) > 180 else "")
