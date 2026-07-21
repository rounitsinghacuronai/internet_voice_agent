"""Structured call memory. Two layers:
  1. slots      — facts the agent must never re-ask (account no, mobile, name, lang…)
  2. history    — chat messages for the LLM (bounded)
Slots update deterministically from tool results (verify_customer fills name/account_no),
so memory never depends on the model remembering to write things down."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from .numbers import (NumberBuffer, is_correction, normalize_digit_words,
                      wants_remove_last, wants_restart)

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
    # DTMF (keypad) controller — lazily bound to number_buffer on first keypress.
    _dtmf: object = field(default=None, repr=False, compare=False)

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
        """Which number-type field (if any) the agent's last line was asking for.

        When the agent offers a CHOICE ("account number or mobile number"),
        prefer mobile: callers almost always read out their 10-digit mobile, and
        a 12-digit account spoken in one breath is still caught by scan_user_text.
        Arming the 12-digit account buffer for a 10-digit mobile made it keep
        asking for two more digits that never came."""
        low = assistant_text.lower()
        matches = [slot for slot, phrases in _FIELD_PROMPTS.items()
                   if any(p in low for p in phrases)]
        if not matches:
            return None
        if "mobile" in matches and "account_no" in matches:
            return "mobile"
        return matches[0]

    def start_number_collection(self, field_name: str) -> None:
        if not self.number_buffer.active or self.number_buffer.field != field_name:
            self.number_buffer.start(field_name)

    def feed_number_fragment(self, text: str,
                             confidence: float = 1.0) -> tuple[str, bool]:
        """Feed one utterance into the active number buffer.

        Returns (accumulated_digits, is_complete). Handles corrections, explicit
        edits ('remove the last digit'), and restarts ('forget that, start
        again') the way a human executive would — never losing prior digits
        except on an explicit restart. When an EXACT-length identifier completes,
        the matching slot (account_no/mobile) is written and the buffer clears.
        """
        nb = self.number_buffer
        # Explicit restart: caller abandons the number and starts fresh.
        if wants_restart(text):
            field_name = nb.field
            nb.clear()
            if field_name:
                nb.start(field_name)
            return "", False
        # Explicit "remove the last digit".
        if wants_remove_last(text) and nb.digits:
            digits = nb.remove_last()
            return digits, False
        # Single-digit / tail correction ("sorry, last digit is 2").
        if is_correction(text) and nb.digits:
            digits = nb.correct_last(text)
            complete = len(digits) == (nb.expected_len or -1)
        else:
            digits, complete = nb.feed(text, confidence)
        if complete:
            field_name = nb.field
            if field_name in ("account_no", "mobile"):
                setattr(self, field_name, digits)
            nb.clear()
        return digits, complete

    def feed_dtmf_digit(self, key: str, submit_key: str = "#",
                        backspace_key: str = "*"):
        """Feed one keypad event into the active number buffer (DUAL INPUT).

        Returns the DTMFResult from the controller. When an EXACT-length
        identifier is completed by keypad — or a caller presses SUBMIT on a
        valid variable-length one — the matching slot (account_no/mobile) is
        written and the buffer cleared, exactly like the speech path, so the
        rest of the stack (read-back, confirmation, tools) is mode-agnostic."""
        from .dtmf import DTMFController
        nb = self.number_buffer
        if not nb.active:
            return None
        if self._dtmf is None or getattr(self._dtmf, "buf", None) is not nb:
            self._dtmf = DTMFController(nb, submit_key, backspace_key)
        res = self._dtmf.press(key)
        if (res.complete or res.submitted) and res.valid:
            field_name = nb.field
            digits = nb.digits
            if field_name in ("account_no", "mobile"):
                setattr(self, field_name, digits)
            nb.clear()
        return res

    def number_capture_snapshot(self) -> dict | None:
        """Live NUMBER CAPTURE MODE state for the frontend (None when idle)."""
        return self.number_buffer.snapshot() if self.number_buffer.active else None

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
        # NUMBER CAPTURE MODE — expose the in-progress buffer so the agent reads
        # back exactly what's captured (never guessing digits), knows how many
        # remain, and confirms only when it's complete.
        nb = self.number_buffer
        if nb.active and nb.digits:
            snap = nb.snapshot()
            remaining = (f", {snap['expected'] - snap['count']} digit(s) still needed"
                         if snap["expected"] else "")
            mode = {"dtmf": " via keypad", "hybrid": " (spoken + keypad)"}.get(
                snap.get("input_mode", "speech"), "")
            capline = (
                f"[CAPTURING {snap['label']}{mode}] So far: {snap['grouped']} "
                f"({snap['count']} digit(s){remaining}). Read back ONLY these "
                "captured digits; never invent the rest. Confirm only once the "
                "number is complete.")
            if not snap.get("prefix_ok", True):
                capline += (" NOTE: the first digit is not valid for this number "
                            "type — tell the caller and ask them to re-check it.")
            unc = nb.uncertain_tail()
            if unc and snap["count"]:
                a, b = unc
                capline += (f" LOW CONFIDENCE on digits {a + 1}–{b}: if unsure, "
                            "re-ask ONLY that part, not the whole number.")
            lines.append(capline)
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
