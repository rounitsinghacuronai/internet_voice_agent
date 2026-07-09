"""Enterprise-grade InterruptionManager.

Coordinates barge-in across the full audio pipeline with the following guarantees:

  • State-gated: only SPEAKING and THINKING states are interruptible.
  • Debounced: a configurable cooldown prevents a single loud sound from firing
    multiple barge-ins in rapid succession.
  • Metriced: every interruption event is recorded with timestamp, state, and
    turn number so post-call analysis can distinguish true from false positives.
  • Language-aware: detects when the customer switches language mid-interruption
    and returns that hint to the session so it can update the engine before
    generating the next response.
  • Tool-safe: cooperates with the asyncio.shield pattern in ConversationManager
    so backend writes (register_complaint, OTP verification, etc.) are never
    aborted mid-flight.

This module is deliberately transport-agnostic — it does not touch WebSockets,
asyncio tasks, or audio buffers. Those responsibilities live in ws_voice.py.
The manager answers one question: "should this VAD event trigger barge-in?"
and records the outcome. All task cancellation happens in VoiceSession.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from ..config import Settings
from ..conversation.state import CallState

log = logging.getLogger(__name__)


@dataclass
class InterruptionEvent:
    """Immutable snapshot of one barge-in occurrence."""
    session_id: str
    timestamp: float                  # monotonic seconds
    state_at_interrupt: str           # "speaking" | "thinking"
    turn_no: int                      # which AI turn was interrupted
    speech_ms_at_trigger: float       # accumulated speech ms when barge-in fired
    false_positive_suspect: bool = False  # flagged by post-hoc analysis
    language_hint: Optional[str] = None  # detected language of the interrupting speech


@dataclass
class InterruptionMetrics:
    """Aggregated per-session barge-in statistics for logging and debugging."""
    total: int = 0
    during_speaking: int = 0
    during_thinking: int = 0
    false_positives_suspected: int = 0
    consecutive_max: int = 0          # longest run of interruptions in a row

    def as_dict(self) -> dict:
        return {
            "total_interruptions": self.total,
            "during_speaking": self.during_speaking,
            "during_thinking": self.during_thinking,
            "false_positives_suspected": self.false_positives_suspected,
            "consecutive_max": self.consecutive_max,
        }


class InterruptionManager:
    """Decides whether a VAD speech-start event should trigger barge-in.

    Usage inside VoiceSession._on_audio:
        if self.im.should_interrupt(self.sm.state, speech_ms):
            self.im.record(self.sm.state, turn_no, speech_ms)
            # ... cancel tasks, transition state machine ...
    """

    def __init__(self, session_id: str, settings: Settings):
        self._sid = session_id
        self._s = settings
        self._events: list[InterruptionEvent] = []
        self._metrics = InterruptionMetrics()
        self._last_interrupt_mono: float = 0.0
        self._consecutive_count: int = 0
        self._last_turn_interrupted: int = -1

    # ── public API ───────────────────────────────────────────────────────────

    def should_interrupt(self, state: CallState, speech_ms: float) -> bool:
        """Return True if this VAD event warrants a barge-in.

        Checks (in order):
          1. State must be SPEAKING or THINKING.
          2. Cooldown: at least bargein_cooldown_ms since the last barge-in.
          3. Minimum continuous speech duration already met by the endpointer
             (the caller passes speech_ms from the Endpointer's own counter so
             we don't double-check it here — the endpointer already requires
             bargein_min_speech_ms of continuous speech before emitting
             SPEECH_START while the agent is talking).
        """
        if state not in (CallState.SPEAKING, CallState.THINKING):
            return False

        # Cooldown guard: prevents a single loud event from double-firing
        now = time.monotonic()
        elapsed_ms = (now - self._last_interrupt_mono) * 1000
        if elapsed_ms < self._s.bargein_cooldown_ms:
            log.debug(
                "session %s: barge-in suppressed by cooldown (%.0f ms remaining)",
                self._sid, self._s.bargein_cooldown_ms - elapsed_ms,
            )
            return False

        return True

    def record(
        self,
        state: CallState,
        turn_no: int,
        speech_ms: float = 0.0,
        language_hint: str | None = None,
    ) -> InterruptionEvent:
        """Record that a barge-in was approved and fired.

        Call this immediately after should_interrupt returns True, before
        doing any task cancellation, so the timestamp is accurate.
        """
        now = time.monotonic()
        evt = InterruptionEvent(
            session_id=self._sid,
            timestamp=now,
            state_at_interrupt=state.value,
            turn_no=turn_no,
            speech_ms_at_trigger=speech_ms,
            language_hint=language_hint,
        )
        self._events.append(evt)
        self._last_interrupt_mono = now

        # Update metrics
        self._metrics.total += 1
        if state is CallState.SPEAKING:
            self._metrics.during_speaking += 1
        else:
            self._metrics.during_thinking += 1

        # Track consecutive interruptions (same or adjacent turn)
        if turn_no == self._last_turn_interrupted or turn_no == self._last_turn_interrupted + 1:
            self._consecutive_count += 1
        else:
            self._consecutive_count = 1
        self._last_turn_interrupted = turn_no
        self._metrics.consecutive_max = max(
            self._metrics.consecutive_max, self._consecutive_count
        )

        log.info(
            "session %s: BARGE-IN recorded | state=%s turn=%d speech_ms=%.0f "
            "consecutive=%d lang_hint=%s",
            self._sid, state.value, turn_no, speech_ms,
            self._consecutive_count, language_hint or "unknown",
        )
        return evt

    def flag_false_positive(self) -> None:
        """Mark the most recent interruption as a suspected false positive.

        Called post-hoc if STT returns empty text after a barge-in — a strong
        signal that background noise triggered the VAD.
        """
        if not self._events:
            return
        last = self._events[-1]
        last.false_positive_suspect = True
        self._metrics.false_positives_suspected += 1
        log.info(
            "session %s: barge-in at t=%.3f suspected false positive (empty STT)",
            self._sid, last.timestamp,
        )

    # ── state queries ────────────────────────────────────────────────────────

    @property
    def total_interruptions(self) -> int:
        return self._metrics.total

    @property
    def consecutive_interruptions(self) -> int:
        """How many consecutive turns have had barge-ins — useful for backing
        off barge-in sensitivity if the customer keeps accidentally interrupting."""
        return self._consecutive_count

    def metrics(self) -> dict:
        return self._metrics.as_dict()

    def events(self) -> list[InterruptionEvent]:
        """Return a copy of the event log (read-only view)."""
        return list(self._events)

    def last_event(self) -> InterruptionEvent | None:
        return self._events[-1] if self._events else None

    # ── smart resume helper ──────────────────────────────────────────────────

    def classify_interruption(
        self,
        interrupted_text: str,
        new_utterance: str,
    ) -> str:
        """Heuristically classify the customer's intent after a barge-in.

        Returns one of:
          "topic_change"    — customer switched to a completely different issue
          "follow_up"       — customer is asking something related
          "clarification"   — customer wants the AI to repeat or explain more
          "accidental"      — very short / partial speech, likely accidental
          "language_switch" — customer asked to change language

        This is a lightweight keyword heuristic — no LLM call.  The
        ConversationManager's system prompt + memory block handle the actual
        routing; this hint can be prepended to the user message for context.
        """
        text = new_utterance.lower().strip()

        # Accidental: very short utterances (< 3 chars or only filler words)
        fillers = {"ok", "um", "uh", "hmm", "ah", "oh", "ha", "haan", "हाँ", "ठीक"}
        if len(text) < 4 or text in fillers:
            return "accidental"

        # Language switch patterns
        lang_switch_signals = [
            "hindi", "english", "marathi", "हिंदी में", "मराठीत", "in hindi",
            "in english", "in marathi", "hindi mein", "english mein",
            "please continue in", "बोलो", "बोला",
        ]
        if any(sig in text for sig in lang_switch_signals):
            return "language_switch"

        # Clarification patterns
        clarify_signals = [
            "what", "sorry", "repeat", "again", "phir se", "फिर से",
            "samjha nahi", "समझा नहीं", "punha", "पुन्हा", "pardon",
            "can you say", "bolo", "could you",
        ]
        if any(sig in text for sig in clarify_signals):
            return "clarification"

        # Follow-up: short question starting with question words
        followup_signals = [
            "and", "also", "but", "when", "how long", "kitna", "kab",
            "aur", "aur ek", "ek aur", "एक और", "आणि",
        ]
        if any(text.startswith(sig) or f" {sig} " in text for sig in followup_signals):
            return "follow_up"

        # Default: topic change (the safest assumption — never resume old topic)
        return "topic_change"

    # ── diagnostics ─────────────────────────────────────────────────────────

    def summary_log(self) -> None:
        """Emit a structured summary log line — call at end of session."""
        log.info(
            "session %s: barge-in summary %s",
            self._sid, self._metrics.as_dict(),
        )
