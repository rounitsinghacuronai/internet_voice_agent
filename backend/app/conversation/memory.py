"""Structured call memory. Two layers:
  1. slots      — facts the agent must never re-ask (account no, mobile, name, lang…)
  2. history    — chat messages for the LLM (bounded)
Slots update deterministically from tool results (verify_customer fills name/account_no),
so memory never depends on the model remembering to write things down."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from .numbers import NumberBuffer, is_correction, normalize_digit_words

# Keyword triggers per field, checked against the AGENT's last utterance
# (native script + romanized), used to decide what to start buffering when
# the caller's next reply looks like a bare number fragment. Module-level
# (not a dataclass field) — it's a constant lookup table, not per-call state.
_FIELD_PROMPTS: dict[str, tuple[str, ...]] = {
    "account_no": ("account number", "account no", "अकाउंट नंबर", "खाते क्रमांक",
                   "खाता नंबर", "ग्राहक क्रमांक", "account number kya hai",
                   "landline number", "broadband account"),
    "mobile": ("mobile number", "mobile no", "registered mobile", "मोबाइल नंबर",
               "मोबाईल क्रमांक"),
    "otp": ("otp", "one time password", "ओटीपी"),
}


@dataclass
class ComplaintRecord:
    ticket_no: str
    category: str
    description: str
    eta_hours: int | None = None


@dataclass
class CallMemory:
    session_id: str = ""
    # identity slots
    account_no: str | None = None
    mobile: str | None = None
    # The number the call actually arrived from (caller ID / Exotel `from`).
    # Distinct from `mobile` (the REGISTERED mobile, filled after verification):
    # both are forwarded to the ops WhatsApp group so the team can see who called
    # even when it differs from the account's registered number.
    caller_number: str | None = None
    name: str | None = None
    location: str | None = None
    service_type: str | None = None          # prepaid|postpaid|fiber|enterprise
    plan_name: str | None = None
    language: str = "und"                    # engine-owned: mr|hi|en|und
    # verification
    verified: bool = False
    verified_at: float = 0.0
    otp_verified: bool = False
    # issue tracking
    complaints: list[ComplaintRecord] = field(default_factory=list)
    open_issues: list[str] = field(default_factory=list)     # unresolved topics
    resolved_issues: list[str] = field(default_factory=list)
    # LLM history
    history: list[dict] = field(default_factory=list)
    # Number Recognition Engine: cross-utterance buffer for a number currently
    # being collected (account_no/mobile/otp spoken in fragments).
    number_buffer: NumberBuffer = field(default_factory=NumberBuffer)

    # ── deterministic slot extraction ────────────────────────────────────────
    # digit-boundary lookarounds (not \b — Devanagari letters are word chars)
    _ACCOUNT_RE = re.compile(r"(?<!\d)(\d{12})(?!\d)")
    _MOBILE_RE = re.compile(r"(?<!\d)([6-9]\d{9})(?!\d)")

    def scan_user_text(self, text: str) -> None:
        # Turn spoken digit words ("one seven zero...") into digit characters
        # first, so a number spoken entirely as words in one utterance is
        # still caught by the same contiguous-run regexes below.
        text = normalize_digit_words(text)
        # join digit groups spoken with pauses ("3000 1234 5678"), keep words apart
        digits = re.sub(r"(?<=\d)[\s\-]+(?=\d)", "", text)
        correcting = is_correction(text)
        if (not self.account_no or correcting) and (m := self._ACCOUNT_RE.search(digits)):
            self.account_no = m.group(1)
        mobile_zone = digits.replace(self.account_no, " ") if self.account_no else digits
        if (not self.mobile or correcting) and (m := self._MOBILE_RE.search(mobile_zone)):
            self.mobile = m.group(1)

    # ── Number Recognition Engine: cross-utterance collection ───────────────

    def field_requested_by(self, assistant_text: str) -> str | None:
        """Which number-type field (if any) the agent's last line was asking for."""
        low = assistant_text.lower()
        for slot, phrases in _FIELD_PROMPTS.items():
            if any(p in low for p in phrases):
                return slot
        return None

    def start_number_collection(self, field_name: str) -> None:
        if not self.number_buffer.active or self.number_buffer.field != field_name:
            self.number_buffer.start(field_name)

    def feed_number_fragment(self, text: str) -> tuple[str, bool]:
        """Feed one utterance into the active number buffer.

        Returns (accumulated_digits, is_complete). When complete, the
        relevant slot (account_no/mobile/otp) is written directly
        and the buffer clears itself, ready for the next collection.
        """
        if is_correction(text) and self.number_buffer.digits:
            digits = self.number_buffer.correct_last(text)
            complete = len(digits) == (self.number_buffer.expected_len or -1)
        else:
            digits, complete = self.number_buffer.feed(text)
        if complete:
            field_name = self.number_buffer.field
            if field_name in ("account_no", "mobile"):
                setattr(self, field_name, digits)
            self.number_buffer.clear()
        return digits, complete

    def absorb_tool_result(self, tool: str, args: dict, result: dict) -> None:
        if tool == "verify_customer" and result.get("verified"):
            self.verified = True
            self.verified_at = time.time()
            self.account_no = result.get("account_no") or self.account_no
            self.mobile = result.get("mobile") or self.mobile
            self.name = result.get("name") or self.name
            self.location = result.get("address") or self.location
            self.service_type = result.get("service_type") or self.service_type
            self.plan_name = result.get("plan_name") or self.plan_name
        elif tool == "verify_otp" and result.get("verified"):
            self.otp_verified = True
        elif tool == "register_complaint" and result.get("ticket_no"):
            self.complaints.append(ComplaintRecord(
                ticket_no=result["ticket_no"],
                category=args.get("category", ""),
                description=args.get("description", ""),
                eta_hours=result.get("sla_hours"),
            ))

    def verify_fresh(self, ttl_s: int) -> bool:
        return self.verified and (time.time() - self.verified_at) < ttl_s

    # ── prompt rendering ─────────────────────────────────────────────────────
    def render_block(self) -> str:
        """Compact factual block injected into the system prompt every turn —
        the model reads state instead of trusting its own recall."""
        lines = ["[CALL MEMORY — facts already known; NEVER ask for these again]"]
        if self.name:
            lines.append(f"Caller name: {self.name}")
        if self.account_no:
            lines.append(f"Account number: {self.account_no}")
        if self.mobile:
            lines.append(f"Registered mobile: {self.mobile}")
        if self.location:
            lines.append(f"Location: {self.location}")
        if self.service_type:
            lines.append(f"Service type: {self.service_type}")
        if self.plan_name:
            lines.append(f"Current plan: {self.plan_name}")
        lines.append(f"Identity verified: {'YES' if self.verified else 'NO'}")
        if self.otp_verified:
            lines.append("OTP verified: YES")
        for c in self.complaints:
            eta = f", ETA ~{c.eta_hours}h" if c.eta_hours else ""
            lines.append(f"Ticket registered this call: {c.category} {c.ticket_no}{eta}")
        if self.open_issues:
            lines.append("Unresolved topics to return to: " + "; ".join(self.open_issues))
        if len(lines) == 2 and not self.verified:
            lines.append("Nothing known yet — you still need the account or mobile number.")
        return "\n".join(lines)

    def trimmed_history(self, max_turns: int) -> list[dict]:
        if len(self.history) <= max_turns:
            return self.history
        return self.history[-max_turns:]

    def snapshot(self) -> dict:
        return {
            "account_no": self.account_no, "mobile": self.mobile, "name": self.name,
            "caller_number": self.caller_number,
            "location": self.location,
            "service_type": self.service_type, "plan_name": self.plan_name,
            "language": self.language, "verified": self.verified,
            "complaints": [c.__dict__ for c in self.complaints],
            "open_issues": self.open_issues,
        }
