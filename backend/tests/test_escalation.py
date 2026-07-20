"""AI → human escalation — offline, deterministic tests.

Covers the decision engine (when to transfer and, crucially, when NOT to), the
CRM-ready summary, and the Exotel transfer service in both simulation and
disabled modes. No network, no Exotel, no LLM.
"""
from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.config import get_settings
from backend.app.conversation.escalation import (
    EscalationEngine, build_escalation_summary, EscalationDecision)
from backend.app.telephony.transfer_service import (
    TransferService, TransferContext, TransferStatus)


# ── a minimal memory stand-in (mirrors CallMemory's read surface) ──
@dataclass
class _Ticket:
    ticket_no: str = "TC2607ABC123"
    category: str = "Broadband - No Internet"
    description: str = "net down"
    eta_hours: int | None = 8


@dataclass
class _Mem:
    session_id: str = "sess1234abcd"
    name: str = "Ramesh Patil"
    account_no: str = "300012345678"
    mobile: str = "9820012345"
    caller_number: str = "9700099000"
    language: str = "hi"
    verified: bool = True
    complaints: list = field(default_factory=lambda: [_Ticket()])
    open_issues: list = field(default_factory=lambda: ["fiber still down"])


def _engine(**over):
    s = get_settings().model_copy()
    for k, v in over.items():
        setattr(s, k, v)
    return EscalationEngine(s), _Mem()


# ── decision engine: SHOULD escalate ──────────────────────────────────────────
def test_explicit_human_request_escalates():
    eng, mem = _engine()
    for text in ["I want to talk to a human", "मला माणसाशी बोलायचंय",
                 "किसी अधिकारी से बात करनी है", "connect me to a senior executive"]:
        d = eng.evaluate(text, mem)
        assert d.should_transfer and d.source == "customer_request", text


def test_category_rules_escalate_with_priority():
    eng, mem = _engine()
    cases = {
        "someone did OTP fraud and took money": "fraud_or_sim_misuse",
        "there is a fire near the telecom pole": "infrastructure_damage",
        "I want an enterprise leased line for my company": "enterprise_business_request",
        "I need SIM ownership transfer to my name": "ownership_or_number_dispute",
        "I will take legal action / consumer court": "legal_complaint",
        "the whole area has no network, everyone is down": "major_multi_customer_outage",
    }
    for text, reason in cases.items():
        d = eng.evaluate(text, mem)
        assert d.should_transfer and d.reason == reason, (text, d.reason)
        assert d.priority in ("HIGH", "MEDIUM")


def test_failed_attempts_threshold_escalates():
    eng, mem = _engine(escalation_failed_attempts=3)
    assert not eng.evaluate("still slow", mem, failed_attempts=2).should_transfer
    d = eng.evaluate("still not working", mem, failed_attempts=3)
    assert d.should_transfer and d.source == "failed_attempts"


def test_sustained_frustration_escalates():
    eng, mem = _engine(escalation_failed_attempts=3)
    d = eng.evaluate("this is useless", mem, mood="angry", failed_attempts=2)
    assert d.should_transfer and d.source == "sentiment"


def test_backend_tool_can_force_handoff():
    eng, mem = _engine()
    d = eng.evaluate("ok", mem, last_tool_results=[{"needs_human": True}])
    assert d.should_transfer and d.source == "tool"


# ── decision engine: should NOT escalate (the important guard) ────────────────
def test_routine_issues_never_escalate():
    eng, mem = _engine()
    for text in ["I want to recharge 299", "what is my data balance",
                 "tell me my plan details", "what's my complaint status",
                 "how do I set the APN", "please restart my router",
                 "what is my wifi password", "my internet is a bit slow"]:
        d = eng.evaluate(text, mem, failed_attempts=0)
        assert not d.should_transfer, text


def test_directive_names_the_tool_when_escalating():
    d = EscalationDecision(True, "customer_requested_human",
                           "Customer Requested Human", "HIGH", "customer_request")
    assert "transfer_to_senior_executive" in d.directive()
    assert EscalationDecision(False).directive() == ""


# ── summary ───────────────────────────────────────────────────────────────────
def test_summary_has_all_handoff_fields():
    mem = _Mem()
    d = EscalationDecision(True, "customer_requested_human",
                           "Customer Requested Human", "HIGH", "customer_request")
    summary = build_escalation_summary(
        mem, ["verify_customer", "get_broadband_status", "run_line_diagnostics"],
        d, "my internet is down")
    for token in ("Ramesh Patil", "9820012345", "9700099000", "300012345678",
                  "Hindi", "Customer Requested Human", "HIGH", "Completed",
                  "TC2607ABC123", "Line diagnostics", "Reason for Escalation"):
        assert token in summary, token


# ── transfer service ──────────────────────────────────────────────────────────
def _ctx(**over):
    base = dict(escalation_reason="customer_requested_human",
                issue_category="Customer Requested Human", issue_priority="HIGH",
                summary="…", customer_name="Ramesh", mobile="9820012345",
                session_id="sess1234abcd")
    base.update(over)
    return TransferContext(**base)


def test_transfer_simulates_without_credentials():
    s = get_settings().model_copy()
    s.transfer_enabled = True
    s.exotel_transfer_enabled = False
    svc = TransferService(s)
    res = asyncio.run(svc.transfer(_ctx(call_sid="CA123")))
    assert res.status == TransferStatus.SIMULATED and res.ok
    assert res.executive == s.transfer_executive_label
    # the exact Exotel payload is prepared/logged even in simulation
    assert res.detail["payload"]["To"] == s.exotel_transfer_number or \
        "To" in res.detail["payload"]


def test_transfer_disabled_returns_disabled():
    s = get_settings().model_copy()
    s.transfer_enabled = False
    svc = TransferService(s)
    res = asyncio.run(svc.transfer(_ctx(call_sid="CA123")))
    assert res.status == TransferStatus.DISABLED and not res.ok


def test_transfer_payload_shape_is_exotel_connect():
    s = get_settings().model_copy()
    s.exotel_transfer_number = "08040000000"
    s.exotel_caller_id = "08041110000"
    svc = TransferService(s)
    payload = svc._build_payload(_ctx(call_sid="CA999", from_number="9820012345"))
    assert payload["CallSid"] == "CA999"
    assert payload["To"] == "08040000000"
    assert payload["CallerId"] == "08041110000"
    assert payload["CallType"] == "trans"
