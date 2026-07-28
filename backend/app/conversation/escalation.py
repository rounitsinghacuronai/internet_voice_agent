"""Escalation decision engine + CRM-ready summary builder.

Two responsibilities, both pure and testable (no I/O):

  1. EscalationEngine.evaluate(...) — the internal DECISION LAYER. Every turn it
     weighs intent, sentiment, issue severity, failed troubleshooting attempts,
     an explicit human request, and backend tool responses, and returns an
     EscalationDecision (transfer? why? which category? what priority?). The
     ConversationManager turns a positive decision into a strong prompt directive
     so the LLM reliably calls transfer_to_senior_executive — the model still
     performs the call, but the RULES decide when, so it never escalates a simple
     recharge and never misses a fraud case.

  2. build_escalation_summary(...) — the structured handoff summary the executive
     (and future CRM) reads, so the customer NEVER repeats themselves.

Design note — routine NEW consumer connections are handled by
register_new_connection (details → WhatsApp, no human leg). ENTERPRISE / BUSINESS
/ CORPORATE connection requests DO escalate to a senior executive; that split is
encoded in _CATEGORY_RULES below and mirrored in the prompt.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Priorities
HIGH, MEDIUM, LOW = "HIGH", "MEDIUM", "LOW"

# Explicit "get me a human" — script + romanized, mr/hi/en. Word-ish boundaries.
# CRITICAL: Sarvam transcribes a Hindi caller saying "senior executive / agent /
# manager" in DEVANAGARI ("सीनियर एग्ज़िक्यूटिव", "एजेंट"), not Latin. Matching
# only the Latin forms meant a clear transfer request went undetected, the
# escalation directive never fired, and the model merely SAID it was connecting
# the caller without calling transfer_to_senior_executive — so the caller had to
# ask a second time. The Devanagari transliterations below close that gap.
_HUMAN_REQUEST = re.compile(
    r"(human|agent|executive|representative|supervisor|manager|senior|"
    r"real person|talk to (a|someone)|customer care person|"
    r"connect me|transfer me|speak to (a|someone|senior|human)|"
    # Devanagari transliterations of the English support words
    r"सीनियर|सिनियर|एग्ज़िक्यूटिव|एग्जीक्यूटिव|एग्ज़ेक्यूटिव|एक्ज़िक्यूटिव|"
    r"एजेंट|एजंट|ऑफिसर|ऑफ़िसर|अफसर|मैनेजर|मॅनेजर|सुपरवाइजर|सुपरवाइज़र|"
    # Marathi / Hindi human nouns
    r"माणस|माणूस|माणसाशी|व्यक्ती|अधिकारी|प्रतिनिधी|वरिष्ठ|"
    r"आदमी|इंसान|व्यक्ति|प्रतिनिधि|"
    # "talk to / connect me" verb phrases (Devanagari + romanized). The suffix
    # group on 'बात कर' avoids matching 'बात करके' (a narration, not a request).
    r"बात कर(ा|ना|नी|ाओ|वा|ा दो|ा दीजिए|नी है|ना है)|"
    r"कॉल (पिला|लगा|करा|करवा)|कनेक्ट कर|"
    r"baat kara|baat karni|baat karna|kisi se baat|connect kar|"
    r"insaan|aadmi)",
    re.IGNORECASE)

# Explicit REFUSAL of a transfer/human — the mirror image of _HUMAN_REQUEST.
#
# Production evidence (session 929a148d33af): the caller was on a HIGH-priority
# sentiment escalation path (mood stuck "frustrated" after several barge-in
# cancellations of its own — a separate bug — kept failed_attempts/mood past
# rule 5's threshold below). Every turn re-evaluated to should_transfer=True,
# and each one re-called transfer_to_senior_executive, opening a NEW ticket
# every time (ESCCC264025, ESC21A4847D, ESC7A75999B, ESCF46B7D6E in one call).
# The caller said, in Hindi, "मेरे को senior अधिकारी से बात नहीं करनी है" — I do
# NOT want to talk to a senior official — but that sentence still CONTAINS the
# words "senior" and "अधिकारी" from _HUMAN_REQUEST's own keyword list, so had
# it ever reached evaluate() uncancelled it would have matched check 2
# (_HUMAN_REQUEST) and been read as a POSITIVE request for a human — the exact
# opposite of what was said. Neither _HUMAN_REQUEST nor rule 5 (sentiment) has
# any way to notice a negation modifying those same keywords. This pattern
# looks for a negation word within a short window of a transfer/human keyword,
# in either word order (Hindi negation typically follows the object; English
# precedes it), and evaluate() checks it BEFORE any positive-match rule so an
# explicit refusal always wins.
_TRANSFER_REFUSAL = re.compile(
    r"(senior|human|agent|executive|representative|supervisor|manager|transfer|"
    r"सीनियर|सिनियर|एग्ज़िक्यूटिव|एग्जीक्यूटिव|एग्ज़ेक्यूटिव|एक्ज़िक्यूटिव|"
    r"एजेंट|एजंट|ऑफिसर|ऑफ़िसर|अफसर|मैनेजर|मॅनेजर|सुपरवाइजर|सुपरवाइज़र|"
    r"माणस|माणूस|व्यक्ती|अधिकारी|प्रतिनिधी|वरिष्ठ|आदमी|इंसान|व्यक्ति|प्रतिनिधि|"
    r"कनेक्ट|ट्रांसफर)"
    r".{0,30}(नहीं|मत|not\b|don'?t\b|do not\b|no need|nahi\b|nako\b)"
    r"|"
    r"(नहीं|मत|don'?t\b|do not\b|nahi\b|nako\b)"
    r".{0,25}(senior|human|agent|executive|transfer|"
    r"सीनियर|सिनियर|एग्ज़िक्यूटिव|एग्जीक्यूटिव|अधिकारी|माणस|माणूस|आदमी|इंसान|"
    r"कनेक्ट|ट्रांसफर)",
    re.IGNORECASE)

# Category rules: (compiled keyword pattern) -> (reason, category, priority).
# Ordered — first match wins, so the most severe/specific sit at the top.
_CATEGORY_RULES: list[tuple[re.Pattern, tuple[str, str, str]]] = [
    (re.compile(r"(fraud|scam|unauthori[sz]ed|misuse|otp.*(scam|fraud)|"
                r"धोखा|धोका|फ्रॉड|गैरवापर|गलत इस्तेमाल)", re.I),
     ("fraud_or_sim_misuse", "Fraud / Unauthorised Activity", HIGH)),
    (re.compile(r"(fire|आग|जळ|जळाल|आगीमुळे|आग लग|जल गया|pole (damage|broke|fall)|"
                r"खांब|खांबाला|पोल|distribution box|फीडर|feeder|transformer|"
                r"cable.*(burn|fire|cut by)|fiber.*(burn|fire))", re.I),
     ("infrastructure_damage", "Network Infrastructure Damage", HIGH)),
    (re.compile(r"(legal|lawyer|court|notice|consumer forum|कानूनी|कायदेशीर|"
                r"वकील|न्यायालय|कोर्ट|legal action)", re.I),
     ("legal_complaint", "Legal Complaint", HIGH)),
    (re.compile(r"(ownership transfer|transfer.*(ownership|name)|name change|"
                r"number.*(dispute|ownership)|ownership.*number|"
                r"मालकी|हस्तांतरण|नाव बदल|नाम (बदल|ट्रांसफर)|मालिकाना)", re.I),
     ("ownership_or_number_dispute", "Ownership / Number Dispute", HIGH)),
    (re.compile(r"(enterprise|corporate|business (internet|plan|connection|line)|"
                r"leased line|mpls|company connection|bulk|एंटरप्राइज|कॉर्पोरेट|"
                r"व्यवसाय|कंपनी.*(कनेक्शन|लाईन))", re.I),
     ("enterprise_business_request", "Enterprise / Business Connection", HIGH)),
    (re.compile(r"(major outage|whole area|entire (area|building|society)|"
                r"everyone.*(down|no network)|multiple.*(customer|homes)|"
                r"पूर्ण (भाग|परिसर|सोसायटी)|पूरे इलाके|सब का|सगळ्यांचा)", re.I),
     ("major_multi_customer_outage", "Major Area Outage", HIGH)),
    (re.compile(r"(billing dispute|wrong bill.*(again|still)|dispute.*(bill|charge)|"
                r"overcharg|बिल.*(वाद|तक्रार|गलत).*(फिर|अभी भी)|"
                r"disputed (amount|charge))", re.I),
     ("billing_dispute_manual", "Billing Dispute — Manual Review", MEDIUM)),
    (re.compile(r"\b(vip|priority customer|premium customer)\b", re.I),
     ("vip_customer", "VIP Customer", HIGH)),
]

# Backend tool result signals that a human must take over.
_TOOL_HANDOFF_KEYS = ("needs_human", "manual_intervention", "requires_executive")


@dataclass
class EscalationDecision:
    should_transfer: bool = False
    reason: str = ""              # machine reason (e.g. fraud_or_sim_misuse)
    category: str = ""           # human category label
    priority: str = MEDIUM
    source: str = ""             # customer_request | category | failed_attempts | sentiment | tool

    def directive(self) -> str:
        """Prompt directive injected when a transfer is warranted — makes the LLM
        reliably call transfer_to_senior_executive with the right arguments."""
        if not self.should_transfer:
            return ""
        return (
            "[ESCALATION REQUIRED] This call now needs a senior human executive "
            f"(reason: {self.reason}; category: {self.category}; priority: "
            f"{self.priority}). Do NOT keep troubleshooting. In the caller's "
            "current language, briefly reassure them, then call "
            "transfer_to_senior_executive with escalation_reason="
            f"'{self.reason}', issue_category='{self.category}', issue_priority="
            f"'{self.priority}'. The system speaks the handoff message and connects "
            "the call — after calling the tool, say nothing further."
        )


class EscalationEngine:
    """Stateless rule engine. All state (attempts, mood) is passed in per call."""

    def __init__(self, settings):
        self.s = settings

    def evaluate(
        self,
        user_text: str,
        memory,
        mood: str | None = None,
        failed_attempts: int = 0,
        last_tool_results: list[dict] | None = None,
    ) -> EscalationDecision:
        text = user_text or ""

        # 1) Backend tool explicitly asked for a human.
        for res in (last_tool_results or []):
            if isinstance(res, dict) and any(res.get(k) for k in _TOOL_HANDOFF_KEYS):
                return EscalationDecision(
                    True, "backend_manual_intervention",
                    "Manual Intervention Required", HIGH, "tool")

        # 1b) Explicit REFUSAL of a transfer — checked before every text-based
        # rule below (including _HUMAN_REQUEST, which shares the same keyword
        # list and would otherwise read "I don't want a senior" as a request
        # FOR one). Overrides the sentiment rule too, so a caller who is both
        # frustrated AND has just said no to a transfer isn't escalated anyway
        # on the very next turn. Does NOT override a backend tool's own
        # handoff signal (check 1) — that's an objective system state, not a
        # reading of the caller's words.
        if _TRANSFER_REFUSAL.search(text):
            return EscalationDecision(False)

        # 2) Explicit human request — always honoured.
        if _HUMAN_REQUEST.search(text):
            return EscalationDecision(
                True, "customer_requested_human", "Customer Requested Human",
                HIGH, "customer_request")

        # 3) Category / severity rules.
        for pattern, (reason, category, priority) in _CATEGORY_RULES:
            if pattern.search(text):
                return EscalationDecision(True, reason, category, priority, "category")

        # 4) Unresolved after repeated troubleshooting.
        threshold = getattr(self.s, "escalation_failed_attempts", 3)
        if failed_attempts >= threshold:
            return EscalationDecision(
                True, "unresolved_after_attempts",
                "Unresolved After Troubleshooting", HIGH, "failed_attempts")

        # 5) Sustained frustration on an unresolved issue.
        if mood in ("angry", "frustrated") and failed_attempts >= max(2, threshold - 1):
            return EscalationDecision(
                True, "customer_dissatisfied", "Customer Dissatisfied",
                HIGH, "sentiment")

        return EscalationDecision(False)


# ── structured summary (executive handoff / CRM) ─────────────────────────────
_TOOL_LABEL = {
    "verify_customer": "Identity verification",
    "send_otp": "OTP sent", "verify_otp": "OTP verified",
    "get_plan": "Plan lookup", "get_bill": "Bill lookup",
    "get_payment_status": "Payment status check",
    "get_recharge_history": "Recharge history", "get_usage": "Usage check",
    "get_network_status": "Area outage check",
    "get_broadband_status": "ONT/line status", "run_line_diagnostics": "Line diagnostics",
    "restart_ont": "Remote ONT restart", "register_complaint": "Complaint registered",
    "track_complaint": "Ticket tracked", "escalate_complaint": "Ticket escalated",
    "schedule_engineer_visit": "Engineer visit booked",
    "block_sim": "SIM blocked", "request_plan_change": "Plan change",
    "request_sim_swap": "SIM/eSIM swap", "get_plan_catalog": "Plan catalog",
    "log_priority_incident": "Priority incident logged",
    "register_new_connection": "New connection registered",
    "get_new_connection_status": "New connection status checked",
}

_INTENT_HINT = [
    (re.compile(r"bill|recharge|payment|charge|refund", re.I), "Billing / payment"),
    (re.compile(r"net|internet|slow|speed|down|ont|fiber|broadband|wifi", re.I), "Broadband / connectivity"),
    (re.compile(r"sim|otp|number|port|esim", re.I), "SIM / number"),
    (re.compile(r"connection|new|install|enterprise|business", re.I), "New connection"),
    (re.compile(r"fraud|scam|legal|dispute", re.I), "Fraud / dispute"),
]


def _intent(text: str) -> str:
    for pat, label in _INTENT_HINT:
        if pat.search(text or ""):
            return label
    return "General enquiry"


def build_escalation_summary(
    memory,
    tools_used: list[str],
    decision: EscalationDecision,
    last_user_text: str = "",
) -> str:
    """Deterministic, CRM-ready handoff summary. Never fails, never invents —
    every line is a known fact from the call. An optional LLM polish can run in
    the background downstream (like the notification summary)."""
    verified = "Completed" if getattr(memory, "verified", False) else "Not verified"
    lang_map = {"mr": "Marathi", "hi": "Hindi", "en": "English"}
    language = lang_map.get(getattr(memory, "language", "und"), "Auto/Code-mix")

    seen: list[str] = []
    for t in tools_used:
        label = _TOOL_LABEL.get(t)
        if label and label not in seen:
            seen.append(label)
    tools_line = ", ".join(seen) if seen else "None"

    complaints = getattr(memory, "complaints", []) or []
    complaint_id = complaints[-1].ticket_no if complaints else "None"
    troubleshooting = "; ".join(
        s for s in seen if s in (
            "Area outage check", "ONT/line status", "Line diagnostics",
            "Remote ONT restart", "Payment status check", "Bill lookup",
            "Usage check", "Recharge history")) or "None"

    open_issues = getattr(memory, "open_issues", []) or []
    current_status = ("Unresolved — " + "; ".join(open_issues)) if open_issues \
        else "Unresolved — needs executive"

    lines = [
        "── ESCALATION HANDOFF ──",
        f"Customer Name: {getattr(memory, 'name', '') or 'Unverified caller'}",
        f"Registered Number: {getattr(memory, 'mobile', '') or '—'}",
        f"Calling From: {getattr(memory, 'caller_number', '') or '—'}",
        f"Account/Customer ID: {getattr(memory, 'account_no', '') or '—'}",
        f"Language: {language}",
        f"Intent: {_intent(last_user_text)}",
        f"Issue Category: {decision.category or '—'}",
        f"Priority: {decision.priority}",
        f"Verification: {verified}",
        f"Complaint ID: {complaint_id}",
        f"Tools Used: {tools_line}",
        f"Troubleshooting Done: {troubleshooting}",
        f"Current Status: {current_status}",
        f"Reason for Escalation: {decision.reason or 'requested'}",
    ]
    return "\n".join(lines)
