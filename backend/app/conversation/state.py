"""Explicit call-state machine — the single source of truth for what a live call is
doing right now. Before this, VoiceSession inferred "is the agent speaking?" from
task.done() checks scattered across the WS layer, which is exactly how the old build's
barge-in bugs crept in (cancelling the TTS task while the LLM generation task kept
running unseen). One enum, one place, every transition logged.

Barge-in is not a special case bolted on top — it is just the transition
SPEAKING|THINKING → INTERRUPTED → LISTENING, like any other."""
from __future__ import annotations

import logging
from enum import Enum

log = logging.getLogger(__name__)


class CallState(Enum):
    IDLE = "idle"                       # no call, or call ended
    LISTENING = "listening"             # caller is talking, STT accumulating
    THINKING = "thinking"               # utterance complete; LLM/tools generating a reply
    SPEAKING = "speaking"               # TTS audio is being streamed to the caller
    INTERRUPTED = "interrupted"         # transient: barge-in just fired, cleanup in flight
    WAITING_FOR_USER = "waiting_for_user"  # agent finished speaking, caller hasn't started


# Legal transitions. Anything else is a bug, not a runtime condition we route around —
# transition() logs a warning and applies it anyway so a call never wedges, but the log
# line is the signal something upstream is wrong.
_TRANSITIONS: dict[CallState, set[CallState]] = {
    CallState.IDLE: {CallState.LISTENING, CallState.SPEAKING},
    CallState.LISTENING: {CallState.THINKING, CallState.SPEAKING, CallState.IDLE,
                          CallState.LISTENING},   # SPEAKING: silence re-prompt
    CallState.THINKING: {CallState.SPEAKING, CallState.INTERRUPTED, CallState.WAITING_FOR_USER,
                          CallState.LISTENING, CallState.IDLE},
    CallState.SPEAKING: {CallState.INTERRUPTED, CallState.WAITING_FOR_USER, CallState.LISTENING,
                         CallState.IDLE},
    CallState.INTERRUPTED: {CallState.LISTENING, CallState.THINKING, CallState.IDLE},
    CallState.WAITING_FOR_USER: {CallState.LISTENING, CallState.THINKING, CallState.IDLE},
}

# States in which the caller's voice should be treated as a barge-in rather than a
# fresh turn starting from silence.
_INTERRUPTIBLE = {CallState.SPEAKING, CallState.THINKING}


class CallStateMachine:
    """One state active at a time. `transition()` is the only sanctioned way to change
    it — callers never assign `.state` directly, so every change is logged and validated."""

    def __init__(self, session_id: str = ""):
        self.session_id = session_id
        self._state = CallState.IDLE

    @property
    def state(self) -> CallState:
        return self._state

    def transition(self, new: CallState, reason: str = "") -> CallState:
        old = self._state
        if new != old:
            allowed = _TRANSITIONS.get(old, set())
            if new not in allowed:
                log.warning("session %s: ILLEGAL transition %s -> %s (%s) — applying anyway",
                            self.session_id, old.value, new.value, reason)
            else:
                log.info("session %s: %s -> %s%s", self.session_id, old.value, new.value,
                         f" ({reason})" if reason else "")
        self._state = new
        return self._state

    def is_speaking(self) -> bool:
        return self._state is CallState.SPEAKING

    def is_interruptible(self) -> bool:
        """True while the agent is generating or voicing a reply — the only window
        during which incoming caller speech should trigger barge-in rather than just
        being treated as the start of a normal new turn."""
        return self._state in _INTERRUPTIBLE
