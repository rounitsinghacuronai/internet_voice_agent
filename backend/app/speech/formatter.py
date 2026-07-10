"""Sarvam TTS Formatter — the LAST text stage before synthesis.

Sarvam Bulbul reads prosody almost entirely from punctuation, so this stage is
tuned specifically to it (not generically):

  • comma  → a brief pause, intonation held (used for MICRO/BREATH and between
             digit groups so long numbers don't rush),
  • ellipsis "…" → a longer, suspended 'thinking' pause (lead-ins, empathy),
  • period → a full stop with falling intonation and a fuller pause
             (confirmations, transitions, completion),
  • question mark → rising intonation that invites the caller back (LISTENING).

It also emits ONE pace value for the whole utterance: the style's pace, dropped
to the style's number_pace when the line contains a spoken digit group, so
consumer/complaint/OTP numbers are always delivered slowly and clearly.
"""
from __future__ import annotations

import re

from .plan import PauseType, Segment, StyleProfile

# how each meaning-typed pause is rendered for Sarvam
_TOKEN: dict[PauseType, str] = {
    PauseType.MICRO: ", ",
    PauseType.BREATH: ", ",
    PauseType.TRANSITION: ". ",
    PauseType.CONFIRMATION: ". ",
    PauseType.THINKING: "… ",
    PauseType.EMPATHY: "… ",
    PauseType.COMPLETION: ". ",
    PauseType.LISTENING: "? ",
}

_TRAIL_PUNCT = re.compile(r"[\s.,;:!?…—]+$")
_LEAD_PUNCT = re.compile(r"^[\s.,;:!?…—]+")
# a spoken digit group looks like "1 7 0 0" or "1 7 0 0, 1 2 3 4"
_SPOKEN_DIGITS = re.compile(r"\d(?:[ ,]\d){3,}")


def _clean_seg(text: str) -> str:
    return _LEAD_PUNCT.sub("", _TRAIL_PUNCT.sub("", text)).strip()


class SarvamFormatter:
    def render(
        self,
        segments: list[Segment],
        lang: str,
        profile: StyleProfile,
        base_pace: float,
        pace_min: float,
        pace_max: float,
    ) -> tuple[str, float]:
        pieces: list[str] = []
        n = len(segments)
        for i, seg in enumerate(segments):
            body = _clean_seg(seg.text)
            if not body:
                continue
            last = i == n - 1
            pause = seg.pause or (PauseType.COMPLETION if last else PauseType.TRANSITION)

            # honour a scale: roomier styles suspend a beat, tighter ones tighten
            if profile.pause_scale >= 1.2 and pause is PauseType.BREATH:
                token = "… "
            elif profile.pause_scale <= 0.95 and pause in (PauseType.THINKING, PauseType.EMPATHY):
                token = ", "
            else:
                token = _TOKEN.get(pause, ". ")

            pieces.append(body + token)

        text = "".join(pieces).strip()
        text = re.sub(r"\s+", " ", text)
        text = re.sub(r"\s+([,.;:!?…])", r"\1", text)
        # collapse accidental doubles from stitching (". …" etc.)
        text = re.sub(r"([.?!])\s*…", r"\1", text)
        text = re.sub(r"…\s*([.?!])", r"…", text)
        text = _ensure_terminal(text, segments)

        pace = _clamp(base_pace * profile.pace, pace_min, pace_max)
        if _SPOKEN_DIGITS.search(text):
            pace = min(pace, _clamp(base_pace * profile.number_pace, pace_min, pace_max))
        return text, round(pace, 3)


def _ensure_terminal(text: str, segments: list[Segment]) -> str:
    if not text:
        return text
    if text[-1] in ".?!।…":
        return text
    last_is_q = segments and segments[-1].pause is PauseType.LISTENING
    return text + ("?" if last_is_q else ".")


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))
