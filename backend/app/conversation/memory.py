"""Structured call memory. Two layers:
  1. slots      — facts the agent must never re-ask (consumer no, mobile, name, lang…)
  2. history    — chat messages for the LLM (bounded)
Slots update deterministically from tool results (verify_consumer fills name/consumer_no),
so memory never depends on the model remembering to write things down."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field


@dataclass
class ComplaintRecord:
    sr_no: str
    category: str
    description: str
    eta_hours: int | None = None


@dataclass
class CallMemory:
    session_id: str = ""
    # identity slots
    consumer_no: str | None = None
    mobile: str | None = None
    name: str | None = None
    location: str | None = None
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

    # ── deterministic slot extraction ────────────────────────────────────────
    # digit-boundary lookarounds (not \b — Devanagari letters are word chars)
    _CONSUMER_RE = re.compile(r"(?<!\d)(\d{12})(?!\d)")
    _MOBILE_RE = re.compile(r"(?<!\d)([6-9]\d{9})(?!\d)")

    def scan_user_text(self, text: str) -> None:
        # join digit groups spoken with pauses ("1700 1234 5678"), keep words apart
        digits = re.sub(r"(?<=\d)[\s\-]+(?=\d)", "", text)
        if not self.consumer_no and (m := self._CONSUMER_RE.search(digits)):
            self.consumer_no = m.group(1)
        mobile_zone = digits.replace(self.consumer_no, " ") if self.consumer_no else digits
        if not self.mobile and (m := self._MOBILE_RE.search(mobile_zone)):
            self.mobile = m.group(1)

    def absorb_tool_result(self, tool: str, args: dict, result: dict) -> None:
        if tool == "verify_consumer" and result.get("verified"):
            self.verified = True
            self.verified_at = time.time()
            self.consumer_no = result.get("consumer_no") or self.consumer_no
            self.mobile = result.get("mobile") or self.mobile
            self.name = result.get("name") or self.name
            self.location = result.get("address") or self.location
        elif tool == "verify_otp" and result.get("verified"):
            self.otp_verified = True
        elif tool == "register_complaint" and result.get("sr_no"):
            self.complaints.append(ComplaintRecord(
                sr_no=result["sr_no"],
                category=args.get("category", ""),
                description=args.get("description", ""),
                eta_hours=result.get("sop_hours"),
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
        if self.consumer_no:
            lines.append(f"Consumer number: {self.consumer_no}")
        if self.mobile:
            lines.append(f"Registered mobile: {self.mobile}")
        if self.location:
            lines.append(f"Location: {self.location}")
        lines.append(f"Identity verified: {'YES' if self.verified else 'NO'}")
        if self.otp_verified:
            lines.append("OTP verified: YES")
        for c in self.complaints:
            eta = f", ETA ~{c.eta_hours}h" if c.eta_hours else ""
            lines.append(f"Complaint registered this call: {c.category} SR {c.sr_no}{eta}")
        if self.open_issues:
            lines.append("Unresolved topics to return to: " + "; ".join(self.open_issues))
        if len(lines) == 2 and not self.verified:
            lines.append("Nothing known yet — you still need the consumer or mobile number.")
        return "\n".join(lines)

    def trimmed_history(self, max_turns: int) -> list[dict]:
        if len(self.history) <= max_turns:
            return self.history
        return self.history[-max_turns:]

    def snapshot(self) -> dict:
        return {
            "consumer_no": self.consumer_no, "mobile": self.mobile, "name": self.name,
            "language": self.language, "verified": self.verified,
            "complaints": [c.__dict__ for c in self.complaints],
            "open_issues": self.open_issues,
        }
