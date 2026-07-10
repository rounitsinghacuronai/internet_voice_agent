"""Human Speech Generation Engine — makes speech sound like a person, not text.

Given one cleaned line plus the turn's StyleProfile, it:

  • adds a brief active-listening lead-in on the first line of a turn
    ("Alright…", "बरं…") — rotated so it never repeats back-to-back,
  • uses a genuine thinking filler instead ("Let me just check…") ONLY when a
    real lookup/tool call happened this turn (never faked),
  • breaks a long line into comfortable thought-groups so the voice can breathe
    rather than delivering one uninterrupted paragraph,
  • leaves reviewed/fixed lines (greeting, safety) structurally untouched.

Output is a list of Segments (text + trailing pause type) that the Prosody
Planner refines and the Sarvam Formatter renders.
"""
from __future__ import annotations

import re

from .lexicon import ACKS, HESITATIONS, INTERJECTIONS, lang_table
from .plan import PauseType, Segment, SpeechContext, StyleProfile
from .variation import VariationTracker

_CLAUSE_SPLIT = re.compile(r"(?<=[,;—।])\s+")
_WORD = re.compile(r"[^\s,.;:!?।—]+")


def _first_word(text: str) -> str:
    m = _WORD.search(text)
    return m.group(0).lower().strip("…") if m else ""


def _already_opened(text: str, lang: str) -> bool:
    """Does the line already start with an acknowledgement / interjection, so we
    must NOT prepend another one (avoids 'Alright. Okay, ...' double-acks)?"""
    fw = _first_word(text)
    if not fw:
        return False
    openers = {w.split()[0].lower() for w in
               lang_table(ACKS, lang) + lang_table(INTERJECTIONS, lang)
               + lang_table(HESITATIONS, lang)}
    # common LLM openers / hesitations across all three languages
    openers |= {"sorry", "माफ", "माफ़", "अरे", "अरेरे", "ok", "okay", "alright",
                "sure", "right", "जी", "बरं", "अच्छा", "हं", "समजलं", "समझ",
                "let", "one", "just", "एक", "जरा", "ज़रा", "अभी", "आत्ता"}
    return fw in openers or text.strip().startswith(("…", "..."))


def _is_bare_ack(text: str, lang: str) -> bool:
    """The line is itself just a short acknowledgement (don't lead into it)."""
    stripped = re.sub(r"[.!?…]+$", "", text.strip())
    return _already_opened(text, lang) and len(stripped) <= 14


def _split_thoughts(text: str, max_chars: int) -> list[str]:
    """Break a long line into breathing-sized thought-groups at natural clause
    boundaries. Short lines pass through as one group."""
    text = text.strip()
    if len(text) <= max_chars:
        return [text]
    clauses = _CLAUSE_SPLIT.split(text)
    groups: list[str] = []
    cur = ""
    for cl in clauses:
        cand = f"{cur} {cl}".strip() if cur else cl
        if cur and len(cand) > max_chars:
            groups.append(cur)
            cur = cl
        else:
            cur = cand
    if cur:
        groups.append(cur)
    return groups or [text]


class HumanSpeechEngine:
    def shape(
        self,
        text: str,
        lang: str,
        profile: StyleProfile,
        ctx: SpeechContext,
        variation: VariationTracker,
    ) -> list[Segment]:
        text = text.strip()
        if not text:
            return []

        # Reviewed/fixed lines: no restructuring, no lead — deliver as written.
        if profile.preserve_wording:
            return [Segment(text, PauseType.COMPLETION)]

        segments: list[Segment] = []

        # ── active listening / hesitation lead-in (first line of a turn only) ──
        if ctx.is_first_utterance and not _is_bare_ack(text, lang) \
                and not _already_opened(text, lang):
            lead = self._lead_in(lang, profile, ctx, variation)
            if lead:
                segments.append(Segment(lead, PauseType.THINKING))

        # ── thought-grouping for breathing ──
        groups = _split_thoughts(text, profile.max_thought_chars)
        for i, g in enumerate(groups):
            last = i == len(groups) - 1
            segments.append(Segment(g, PauseType.COMPLETION if last else PauseType.BREATH))

        return segments

    @staticmethod
    def _lead_in(lang: str, profile: StyleProfile, ctx: SpeechContext,
                 variation: VariationTracker) -> str:
        # A real lookup happened → a genuine thinking filler (never faked).
        if ctx.processing and profile.hesitation_ok:
            return variation.pick(f"hes:{lang}", lang_table(HESITATIONS, lang))
        # Otherwise a brief acknowledgement, if the style allows one.
        if profile.lead_in:
            return variation.pick(f"ack:{lang}", lang_table(ACKS, lang))
        return ""
