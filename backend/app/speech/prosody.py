"""Prosody Planner — assigns pauses by MEANING and sets sentence intonation.

The Human Speech Engine produced thought-groups with rough pauses. The planner
refines them: a beat that lets empathy land is an EMPATHY pause, the close of a
confirmed action is a CONFIRMATION pause, a closing question invites the caller
back with a LISTENING pause (rising intonation). Nothing is mechanical — the
pause type reflects why the pause is there, which is what the evaluator checks.
"""
from __future__ import annotations

import re

from .plan import Emotion, PauseType, Segment, SpeechContext, StyleProfile

# empathy / feeling cues (any language) → let the beat land
_EMPATHY_CUE = re.compile(
    r"(sorry|frustrat|understand|apolog|अरे|अरेरे|माफ|काळजी|परेशान|समझ|चिंता|त्रास)",
    re.IGNORECASE,
)
# completed-action confirmation cues → settle the fact
_CONFIRM_CUE = re.compile(
    r"(done|taken care|registered|झालं|हो गया|नोंदव|दर्ज|complete|all set|there we go)",
    re.IGNORECASE,
)
# question tells (terminal ? or interrogative markers in the 3 languages).
# NOTE on Devanagari + \b: Python's \w (and therefore \b) does not treat
# Devanagari vowel signs/anusvara/chandrabindu (matras — ा ी ं ँ े etc.) as
# word characters, so a trailing \b right after a Devanagari word silently
# fails whenever that word ends in one (verified: re.search(r"क्या\b", "आप
# क्या कहते हैं") was None — this dropped rising question intonation on the
# large majority of real Hindi/Marathi questions, which almost all end their
# interrogative word in a matra: क्या, कैसे, कहाँ, कशी, कसं, कसे...). Fixed
# the same way conversation/language.py's _MR_MARKERS/_HI_MARKERS avoid this:
# plain substring containment for Devanagari, \b-regex kept for Latin/
# romanized terms (where the boundary works correctly).
_QUESTION_CUE_TERMINAL = re.compile(r"\?\s*$")
_QUESTION_CUE_LATIN = re.compile(
    r"\b(kya|kaise|kahan|kab|can you|could you|would you|shall i|may i)\b",
    re.IGNORECASE)
_QUESTION_CUE_DEVANAGARI = ("क्या", "कैसे", "कहाँ", "कब", "का?", "कशी", "कसं",
                            "कसे", "सांगाल", "बता सकते", "चालेल का")


def is_question(text: str) -> bool:
    t = text.strip()
    if _QUESTION_CUE_TERMINAL.search(t) or _QUESTION_CUE_LATIN.search(t):
        return True
    return any(term in t for term in _QUESTION_CUE_DEVANAGARI)


class ProsodyPlanner:
    def plan(self, segments: list[Segment], lang: str, profile: StyleProfile,
             ctx: SpeechContext) -> list[Segment]:
        if not segments:
            return segments

        out: list[Segment] = []
        n = len(segments)
        for i, seg in enumerate(segments):
            last = i == n - 1
            pause = seg.pause
            text = seg.text

            # keep an explicit lead-in beat (THINKING) as-is
            if pause is PauseType.THINKING:
                out.append(seg)
                continue

            if last:
                pause = PauseType.LISTENING if is_question(text) else PauseType.COMPLETION
            elif _EMPATHY_CUE.search(text):
                pause = PauseType.EMPATHY
            elif _CONFIRM_CUE.search(text):
                pause = PauseType.CONFIRMATION
            elif pause is None:
                pause = PauseType.TRANSITION

            out.append(Segment(text, pause))

        # emotion shaping: an urgent line is tighter; a patient one is roomier
        if profile.emotion is Emotion.CALM_URGENT:
            out = [_retype(s, {PauseType.BREATH: PauseType.MICRO,
                               PauseType.TRANSITION: PauseType.MICRO}) for s in out]
        return out


def _retype(seg: Segment, mapping: dict[PauseType, PauseType]) -> Segment:
    return Segment(seg.text, mapping.get(seg.pause, seg.pause))
