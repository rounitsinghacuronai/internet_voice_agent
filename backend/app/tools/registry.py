"""Tool registry: OpenAI-style schemas + dispatch + HARD GATES.

Gates are code, not prompt (lesson from run 192, where the model invented a ticket
number after a failed verification):
  • verify-gate : writes (register_complaint / plan change / SIM ops / ONT restart /
                  engineer visit / escalate / close) refuse unless verify_customer
                  succeeded within VERIFY_TTL_S — enforced HERE.
  • otp-gate    : plan change and SIM swap/eSIM additionally require verify_otp success.
  • priority (fraud/security) tools are NEVER gated.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable

from ..config import Settings
from ..conversation.memory import CallMemory
from ..conversation.numbers import EXPECTED_LENGTHS
from .telecom import TelecomServices

log = logging.getLogger(__name__)

_WRITE_TOOLS = {"register_complaint", "request_plan_change", "request_sim_swap",
                "block_sim", "restart_ont", "schedule_engineer_visit",
                "escalate_complaint", "close_complaint"}
_OTP_TOOLS = {"request_plan_change", "request_sim_swap"}
_UNGATED = {"verify_customer", "send_otp", "verify_otp", "get_new_connection_status",
            "register_new_connection", "get_plan_catalog", "get_network_status",
            "track_complaint", "log_priority_incident", "transfer_to_human",
            "record_feedback", "search_knowledge", "end_call"}

# Number Recognition Engine hard gate: these tools take a number-type argument
# that must be complete and well-formed before the backend is ever called.
# Prevents the LLM from calling verify_customer/send_otp/verify_otp with a
# partial number fragment (e.g. mid-collection, or a garbled STT read) —
# "Never trigger verification until the full number has been collected."
_NUMBER_ARG_FIELDS: dict[str, dict[str, str]] = {
    # tool name → {arg name: number-type key in EXPECTED_LENGTHS}
    "verify_customer": {"account_no": "account_no", "mobile": "mobile"},
    "send_otp": {"mobile": "mobile"},
    "verify_otp": {"mobile": "mobile", "otp": "otp"},
    "block_sim": {"mobile": "mobile"},
}


def _validate_number_args(name: str, args: dict) -> str | None:
    """Return an error message if a number-type argument is present but the
    wrong length (partial/garbled), else None. Absent args are fine — some
    tools accept EITHER account_no OR mobile."""
    fields = _NUMBER_ARG_FIELDS.get(name)
    if not fields:
        return None
    for arg_name, kind in fields.items():
        val = args.get(arg_name)
        if not val:
            continue
        digits = "".join(ch for ch in str(val) if ch.isdigit())
        expected = EXPECTED_LENGTHS.get(kind)
        if expected is not None and len(digits) != expected:
            return (f"Refused: {arg_name} has {len(digits)} digits, expected {expected}. "
                     "This looks like a partial or misheard number — collect the complete "
                     "number from the caller before calling this tool again.")
    return None


def _fn(name: str, desc: str, props: dict, required: list[str] | None = None) -> dict:
    return {"type": "function",
            "function": {"name": name, "description": desc,
                         "parameters": {"type": "object", "properties": props,
                                        "required": required or []}}}


def build_schemas() -> list[dict]:
    S = {"type": "string"}
    return [
        _fn("search_knowledge",
            "Retrieve grounded Syncbroad Networks policy/procedure/troubleshooting knowledge: plan "
            "rules, recharge and billing policy, SIM/eSIM/MNP process, KYC, broadband and "
            "Wi-Fi troubleshooting steps, roaming, enterprise services, app/website help. "
            "Use for HOW-things-work questions — never for a specific customer's live data.",
            {"query": {**S, "description": "The caller's question, any language."},
             "category": {**S, "description": "Optional filter: mobile|broadband|billing|sim|network|account|enterprise|complaints|safety|general."}},
            ["query"]),
        _fn("verify_customer",
            "Look up and verify the caller by 12-digit account number or registered 10-digit "
            "mobile. Call this the moment the caller provides either number.",
            {"account_no": S, "mobile": S}),
        _fn("send_otp", "Send OTP to the registered mobile. ONLY needed before a plan change "
            "or SIM swap/eSIM request — never to read information.", {"mobile": S}, ["mobile"]),
        _fn("verify_otp", "Verify the OTP the caller reads back.", {"mobile": S, "otp": S},
            ["mobile", "otp"]),
        _fn("get_plan", "Current plan: name, price, validity/data quota. Requires verification.",
            {"account_no": S}, ["account_no"]),
        _fn("get_bill", "Latest postpaid/fiber bill: month, rent, add-ons, amount with GST, "
            "due date, payment status, AutoPay.", {"account_no": S}, ["account_no"]),
        _fn("get_payment_status", "Status of a payment (success / pending / failed-but-debited).",
            {"account_no": S, "txn_ref": S}, ["account_no"]),
        _fn("get_recharge_history", "Last prepaid recharges with dates, amounts and status.",
            {"account_no": S}, ["account_no"]),
        _fn("get_usage", "Live data usage: used today / remaining quota (mobile) or cycle "
            "usage vs fair-use (fiber).", {"account_no": S}, ["account_no"]),
        _fn("get_network_status", "Check for a known area outage (mobile tower or fiber cut) "
            "by account number or area name, with restoration ETA. No verification needed.",
            {"area": S, "account_no": S}),
        _fn("get_broadband_status", "Live ONT/line status for a fiber account: LOS (red light) "
            "or online, line up/down, last sync speed.", {"account_no": S}, ["account_no"]),
        _fn("run_line_diagnostics", "Remote end-to-end diagnostics on a broadband line — "
            "finds fiber faults vs Wi-Fi/router-side issues. Run BEFORE booking an engineer.",
            {"account_no": S}, ["account_no"]),
        _fn("restart_ont", "Remotely reboot the customer's ONT/router. Service returns in "
            "two to three minutes. Useless for LOS (fiber break). Requires verification.",
            {"account_no": S}, ["account_no"]),
        _fn("register_complaint",
            "Register a complaint/ticket. YOU pick the exact category (e.g. 'Broadband - No "
            "Internet', 'Mobile - Call Drops', 'Recharge - Failed', 'Billing - High Bill', "
            "'SIM - Lost / Block Request') and YOU write the one-line description from what "
            "the caller said. Requires prior successful verification.",
            {"account_no": S, "category": S, "description": S},
            ["account_no", "category", "description"]),
        _fn("track_complaint", "Track an existing ticket by its number.", {"complaint_no": S},
            ["complaint_no"]),
        _fn("escalate_complaint", "Escalate an open ticket to level two when the SLA is "
            "breached or the caller is dissatisfied after a genuine attempt.",
            {"complaint_no": S, "reason": S}, ["complaint_no"]),
        _fn("close_complaint", "Close a ticket ONLY when the caller confirms on this call "
            "that the issue is resolved.", {"complaint_no": S, "resolution_note": S},
            ["complaint_no"]),
        _fn("schedule_engineer_visit", "Book a field engineer visit (broadband faults that "
            "remote diagnostics could not fix, installations, relocations). Requires "
            "verification.", {"account_no": S, "preferred_slot": S}, ["account_no"]),
        _fn("block_sim", "Immediately block a SIM (lost/stolen phone, fraud). Requires "
            "verification but NEVER OTP — the caller may not have the SIM.",
            {"mobile": S, "reason": S}, ["mobile"]),
        _fn("request_plan_change", "Submit a plan upgrade/downgrade. Requires verification + OTP.",
            {"account_no": S, "new_plan": S}, ["account_no", "new_plan"]),
        _fn("request_sim_swap", "Request a physical SIM replacement or physical-to-eSIM "
            "conversion. swap_type: replacement | esim. Requires verification + OTP.",
            {"account_no": S, "swap_type": S}, ["account_no"]),
        _fn("register_new_connection",
            "Log a NEW CONNECTION request from a prospective customer and forward it "
            "to the operations team. NO verification and NO OTP — the caller is not an "
            "existing subscriber. Collect and pass: name, installation address, "
            "service_type (mobile/prepaid|postpaid|fiber|enterprise), the plan they "
            "want, a contact_mobile to reach them on, and a preferred_slot (preferred "
            "callback/installation time). Call this once you have these details.",
            {"name": S, "address": S, "service_type": S, "plan": S,
             "contact_mobile": S, "preferred_slot": S},
            ["name", "address", "service_type", "contact_mobile"]),
        _fn("get_new_connection_status", "Stage of an EXISTING new-connection "
            "application (caller already has an application number). No verification "
            "needed.", {"application_no": S}, ["application_no"]),
        _fn("get_plan_catalog", "Current plan catalog notes for a service type "
            "(prepaid|postpaid|fiber|enterprise). Never quote pack prices from memory.",
            {"service_type": S}),
        _fn("log_priority_incident", "Log a fraud/security incident (SIM-swap fraud, OTP "
            "scam, stolen device, unauthorised activity, threat/harassment calls). Never gated.",
            {"type": S, "details": S}, ["type", "details"]),
        _fn("transfer_to_human", "Transfer to a senior human executive with a one-line context "
            "summary.", {"reason": S, "context_summary": S}, ["reason"]),
        _fn("record_feedback", "Record the caller's satisfaction at the end of the call "
            "(rating: satisfied|neutral|dissatisfied, optional comment).",
            {"rating": S, "comment": S}, ["rating"]),
        _fn("end_call",
            "Disconnect the call. Call this IN THE SAME TURN as your spoken official "
            "closing line, when the caller has confirmed nothing else is needed, says "
            "goodbye, or asks to hang up. The call ends only after your final words "
            "finish playing. NEVER call this while an issue is still unresolved or "
            "before speaking the official closing.",
            {"reason": {**S, "description": "resolved | caller_goodbye | caller_request"}}),
    ]


class ToolRegistry:
    def __init__(self, settings: Settings, services: TelecomServices, retriever=None,
                 on_event=None):
        self.s = settings
        self.svc = services
        self.retriever = retriever          # injected after RAG init
        # Optional observer fired after EVERY tool dispatch:
        #   on_event(tool_name, args, result, memory_snapshot, call_id)
        # Wired to NotificationService.notify_event in main.py. MUST be a
        # plain sync callable that returns instantly — it runs on the turn's
        # hot path and is therefore guarded and never awaited.
        self.on_event = on_event
        self.schemas = build_schemas()
        self._map: dict[str, Callable[..., Any]] = {
            n: getattr(services, n) for n in (
                "verify_customer", "send_otp", "verify_otp", "get_plan", "get_bill",
                "get_payment_status", "get_recharge_history", "get_usage",
                "get_network_status", "get_broadband_status", "run_line_diagnostics",
                "restart_ont", "register_complaint", "track_complaint",
                "escalate_complaint", "close_complaint", "schedule_engineer_visit",
                "block_sim", "request_plan_change", "request_sim_swap",
                "register_new_connection", "get_new_connection_status",
                "get_plan_catalog",
                "log_priority_incident", "transfer_to_human", "record_feedback")
        }

    async def dispatch(self, name: str, args: dict, memory: CallMemory) -> dict:
        start = time.perf_counter()
        result = await self._dispatch_inner(name, args, memory)
        latency = (time.perf_counter() - start) * 1000
        log.info("tool %s %.0fms → %s", name, latency, _short(result))
        memory.absorb_tool_result(name, args, result)
        if self.on_event is not None:
            try:                              # observer must never hurt the turn
                self.on_event(name, args, result, memory.snapshot(),
                              memory.session_id)
            except Exception:
                log.exception("tool on_event observer failed (ignored)")
        return result

    async def _dispatch_inner(self, name: str, args: dict, memory: CallMemory) -> dict:
        if name == "end_call":
            # No backend work — ConversationManager sets its end flag and the
            # transport disconnects after the final audio finishes playing.
            return {"status": "ok", "message": "Call will disconnect after your "
                    "current spoken lines finish playing. Say nothing further."}
        if name == "search_knowledge":
            if self.retriever is None:
                return {"error": "knowledge_unavailable"}
            return await self.retriever.search(args.get("query", ""), args.get("category"))

        fn = self._map.get(name)
        if fn is None:
            return {"error": f"unknown_tool:{name}"}

        # New-connection requests are forwarded with the caller-ID origin number
        # (the number the call actually arrived from) — injected from call memory,
        # never trusted from the model.
        if name == "register_new_connection" and getattr(memory, "caller_number", None):
            args["caller_number"] = memory.caller_number

        # ── number-format gate (Number Recognition Engine) ──
        if (err := _validate_number_args(name, args)) is not None:
            log.warning("tool %s refused: %s", name, err)
            return {"error": "invalid_number_format", "message": err}

        # ── hard gates ──
        if name in _WRITE_TOOLS and name not in _UNGATED:
            if not memory.verify_fresh(self.s.verify_ttl_s):
                return {"error": "verification_required",
                        "message": "Refused: customer identity not verified in this call. "
                                   "Verify with verify_customer first; never invent a result."}
            if name in _OTP_TOOLS and not memory.otp_verified:
                return {"error": "otp_required",
                        "message": "Refused: this change needs OTP confirmation. "
                                   "Use send_otp then verify_otp first."}
            # writes always use the VERIFIED account number, not whatever the model typed
            if memory.account_no and "account_no" in args:
                args["account_no"] = memory.account_no

        try:
            result = await asyncio.to_thread(fn, **_clean(args, fn))
        except TypeError as e:
            return {"error": "bad_arguments", "detail": str(e)}
        except Exception as e:  # pragma: no cover
            log.exception("tool %s failed", name)
            return {"error": "tool_failure", "detail": str(e)}
        return result


import inspect
from functools import lru_cache


@lru_cache(maxsize=64)
def _param_names(fn: Callable) -> frozenset:
    """Cached parameter-name set for a tool callable. Tool signatures are static
    for the process lifetime, so reflecting once per function (instead of on
    every dispatch) removes an inspect.signature() call from the turn hot path."""
    return frozenset(inspect.signature(fn).parameters)


def _clean(args: dict, fn: Callable) -> dict:
    params = _param_names(fn)
    return {k: v for k, v in args.items() if k in params and v is not None}


def _short(obj: dict) -> str:
    s = json.dumps(obj, ensure_ascii=False)
    return s[:180] + ("…" if len(s) > 180 else "")
