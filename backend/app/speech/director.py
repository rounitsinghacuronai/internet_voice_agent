"""Voice Director — assigns a speaking style BEFORE any formatting.

Sits above the Human Speech Engine. Instead of every response being read with
the same cadence, the Director reads the conversation context and picks one
StyleProfile for the whole turn — greeting, verification, outage, billing,
complaint-registered, emergency, closing — then layers caller-emotion on top.
The result is a consistent 'performance' rather than a flat voice.
"""
from __future__ import annotations

import re

from .plan import SpeechContext, StyleName, StyleProfile
from .profiles import apply_caller_emotion, base_profile

# ── caller-emotion sensing from the last utterance (heuristic, multilingual) ──
_ANGRY = re.compile(
    r"\b(worst|pathetic|useless|ridiculous|nonsense|rubbish|stupid|terrible|"
    r"बकवास|बेकार|फालतू|बेकार|घटिया|नालायक|वैताग|डोक्याला ताप)\b|!!+",
    re.IGNORECASE,
)
_ANGRY_REPEAT = re.compile(
    r"\b(kab tak|kabtak|roz roz|har baar|baar baar|कब तक|रोज़ रोज़|हर बार|बार बार|"
    r"किती वेळा|रोज रोज|परत परत)\b",
    re.IGNORECASE,
)
_FRUSTRATED = re.compile(
    r"\b(again|third time|second time|still not|fed up|thak gaya|thak gayi|थक गया|"
    r"थक गयी|फिर से|तिसऱ्यांदा|परत|थकलो|कंटाळलो|अजून)\b",
    re.IGNORECASE,
)
_WORRIED = re.compile(
    r"\b(scared|afraid|worried|dangerous|डर|घाबर|भीती|खतरा|धोका|चिंता)\b",
    re.IGNORECASE,
)


def detect_caller_emotion(text: str, existing: str | None = None) -> str | None:
    """Best-effort read of how the caller sounds, from their words. Conservative:
    only flags a clear signal. An explicit hint (e.g. elderly, set elsewhere) wins."""
    if existing in ("elderly", "angry", "frustrated", "worried", "calm"):
        return existing
    if not text:
        return existing
    if _ANGRY.search(text) or _ANGRY_REPEAT.search(text):
        return "angry"
    if _FRUSTRATED.search(text):
        return "frustrated"
    if _WORRIED.search(text):
        return "worried"
    return existing


class VoiceDirector:
    """Stateless decision function: SpeechContext → StyleProfile."""

    def direct(self, ctx: SpeechContext) -> StyleProfile:
        name = self._pick_style(ctx)
        profile = base_profile(name)
        emotion = detect_caller_emotion(ctx.user_text, ctx.caller_emotion)
        profile = apply_caller_emotion(profile, emotion)
        return profile

    @staticmethod
    def _pick_style(ctx: SpeechContext) -> StyleName:
        if ctx.is_emergency:
            return StyleName.EMERGENCY
        if ctx.is_greeting:
            return StyleName.GREETING
        if ctx.is_closing:
            return StyleName.CLOSING
        if ctx.just_registered_complaint:
            return StyleName.COMPLAINT_REGISTERED
        if ctx.asking_for_number:
            return StyleName.VERIFICATION
        if not ctx.verified and ctx.asking_for_number is None and ctx.topic is None \
                and ctx.turn_no <= 2:
            # early identity-gathering turns lean deliberate/clear
            return StyleName.VERIFICATION
        if ctx.topic == "outage":
            return StyleName.OUTAGE
        if ctx.topic == "billing":
            return StyleName.BILLING
        if ctx.topic in ("complaint_status", "new_connection"):
            return StyleName.DEFAULT
        return StyleName.DEFAULT
