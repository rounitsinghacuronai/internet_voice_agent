"""Voice Director — assigns a speaking style BEFORE any formatting.

Sits above the Human Speech Engine. Instead of every response being read with
the same cadence, the Director reads the conversation context and picks one
StyleProfile for the whole turn — greeting, verification, service-down, billing,
ticket-registered, priority, closing — then layers caller-emotion on top.
The result is a consistent 'performance' rather than a flat voice.
"""
from __future__ import annotations

import re

from .plan import SpeechContext, StyleName, StyleProfile
from .profiles import apply_caller_emotion, base_profile

# ── caller-emotion sensing from the last utterance (heuristic, multilingual) ──
# NOTE on Devanagari + \b: Python's \w (and therefore \b) does not treat
# Devanagari vowel signs/anusvara (matras — ा ी ो ं etc.) as word characters,
# so a trailing \b right after a Devanagari word silently fails to match
# whenever that word ends in one (verified: re.search(r"रोज़ रोज़\b", "रोज़
# रोज़ यही problem") was None — "angry repeat" never fired). Every Devanagari
# term below is checked by plain substring containment instead — the same
# convention conversation/language.py's _MR_MARKERS/_HI_MARKERS already use
# for this exact reason — while Latin/romanized terms keep \b, which works
# correctly for them.
_ANGRY_LATIN = re.compile(
    r"\b(worst|pathetic|useless|ridiculous|nonsense|rubbish|stupid|terrible)\b|!!+",
    re.IGNORECASE)
_ANGRY_DEVANAGARI = ("बकवास", "बेकार", "फालतू", "घटिया", "नालायक", "वैताग", "डोक्याला ताप")

_ANGRY_REPEAT_LATIN = re.compile(
    r"\b(kab tak|kabtak|roz roz|har baar|baar baar)\b", re.IGNORECASE)
_ANGRY_REPEAT_DEVANAGARI = ("कब तक", "रोज़ रोज़", "हर बार", "बार बार", "किती वेळा",
                            "रोज रोज", "परत परत")

_FRUSTRATED_LATIN = re.compile(
    r"\b(again|third time|second time|still not|fed up|thak gaya|thak gayi)\b",
    re.IGNORECASE)
_FRUSTRATED_DEVANAGARI = ("थक गया", "थक गयी", "फिर से", "तिसऱ्यांदा", "परत",
                          "थकलो", "कंटाळलो", "अजून")

_WORRIED_LATIN = re.compile(r"\b(scared|afraid|worried|dangerous)\b", re.IGNORECASE)
_WORRIED_DEVANAGARI = ("डर", "घाबर", "भीती", "खतरा", "धोका", "चिंता")

# gratitude / relief — the caller has visibly cooled off or is happy. Lets the
# manager CLEAR a sticky negative mood instead of treating one angry sentence
# five turns ago as a permanently angry caller.
_CALM_LATIN = re.compile(r"\b(thank(?:s| you)?|great|perfect|wonderful)\b", re.IGNORECASE)
_CALM_DEVANAGARI = ("धन्यवाद", "आभारी", "आभार", "शुक्रिया", "थैंक", "बरं झालं",
                    "छान", "मस्त", "बढ़िया", "बढिया", "बहुत अच्छा", "खूप छान")


def _hits(text: str, latin: re.Pattern, devanagari: tuple[str, ...]) -> bool:
    return bool(latin.search(text)) or any(term in text for term in devanagari)


def detect_caller_emotion(text: str, existing: str | None = None) -> str | None:
    """Best-effort read of how the caller sounds, from their words. Conservative:
    only flags a clear signal. An explicit hint (e.g. elderly, set elsewhere) wins."""
    if existing in ("elderly", "angry", "frustrated", "worried", "calm"):
        return existing
    if not text:
        return existing
    if (_hits(text, _ANGRY_LATIN, _ANGRY_DEVANAGARI)
            or _hits(text, _ANGRY_REPEAT_LATIN, _ANGRY_REPEAT_DEVANAGARI)):
        return "angry"
    if _hits(text, _FRUSTRATED_LATIN, _FRUSTRATED_DEVANAGARI):
        return "frustrated"
    if _hits(text, _WORRIED_LATIN, _WORRIED_DEVANAGARI):
        return "worried"
    if _hits(text, _CALM_LATIN, _CALM_DEVANAGARI):
        return "calm"
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
        if ctx.topic in ("network", "internet"):
            return StyleName.SERVICE_DOWN
        if ctx.topic == "billing":
            return StyleName.BILLING
        if ctx.topic in ("sim", "complaint_status", "new_connection"):
            return StyleName.DEFAULT
        return StyleName.DEFAULT
