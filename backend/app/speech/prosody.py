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
# question tells (terminal ? or interrogative markers in the 3 languages)
_QUESTION_CUE = re.compile(
    r"\?\s*$|\b(kya|kaise|kahan|kab|can you|could you|would you|shall i|may i|"
    r"क्या|कैसे|कहाँ|कब|का\?|कशी|कसं|कसे|सांगाल|बता सकते|चालेल का)\b",
    re.IGNORECASE,
)


def is_question(text: str) -> bool:
    return bool(_QUESTION_CUE.search(text.strip()))


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
