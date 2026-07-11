"""Human Speech Generation Engine — makes speech sound like a person, not text.

Given one cleaned line plus the turn's StyleProfile, it:

  • breaks a long line into comfortable thought-groups so the voice can breathe
    rather than delivering one uninterrupted paragraph,
  • leaves reviewed/fixed lines (greeting, safety) structurally untouched.

It deliberately does NOT inject canned acknowledgements or thinking fillers
("Alright…", "Let me just check…") in front of the LLM's words any more.
That mechanical prepend was the single biggest "this is a bot" tell in live
calls: every turn opened with a rotated stock phrase, stacked on top of
whatever natural opener the model itself produced, giving every caller the
same audible template. Openers, hesitations, and acknowledgements are now the
LLM's job alone (see prompts/modules/02_style.md) — the model varies them with
real conversational judgement, including *not* using one most of the time.

Output is a list of Segments (text + trailing pause type) that the Prosody
Planner refines and the Sarvam Formatter renders.
"""
from __future__ import annotations

import re

from .plan import PauseType, Segment, SpeechContext, StyleProfile
from .variation import VariationTracker

_CLAUSE_SPLIT = re.compile(r"(?<=[,;—।])\s+")


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

        # Reviewed/fixed lines: no restructuring — deliver as written.
        if profile.preserve_wording:
            return [Segment(text, PauseType.COMPLETION)]

        # ── thought-grouping for breathing ──
        segments: list[Segment] = []
        groups = _split_thoughts(text, profile.max_thought_chars)
        for i, g in enumerate(groups):
            last = i == len(groups) - 1
            segments.append(Segment(g, PauseType.COMPLETION if last else PauseType.BREATH))

        return segments
