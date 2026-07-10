"""Speech Naturalness Evaluation.

Automatic, deterministic metrics that measure whether output sounds spoken or
read. Runs over a list of final spoken lines (or SpokenPlans) and scores:

  • repetition          — repeated whole lines / back-to-back openers
  • sentence length     — average words per sentence (short = spoken)
  • rhythm variety      — variation in sentence length (alive, not monotone)
  • pause density       — pause marks per sentence (breathing)
  • pause variety       — distinct pause types used (intentional, not mechanical)
  • acknowledgement diversity — unique openers across lines
  • AI-pattern residue  — documentation/IVR phrasings still present (should be 0)

`compare()` produces a before/after view so the impact of the engine is visible.
"""
from __future__ import annotations

import math
import re
import statistics
from dataclasses import asdict, dataclass

from .lexicon import AI_PATTERNS
from .plan import PauseType, SpokenPlan

# extra documentation tells to detect (superset of the rewrite list)
_AI_DETECT = [p for p, _ in AI_PATTERNS] + [
    re.compile(r"\bplease be advised\b", re.I),
    re.compile(r"\bas per (?:your|the) request\b", re.I),
    re.compile(r"\bthe estimated .* is\b", re.I),
    re.compile(r"\byour complaint has been\b", re.I),
]

_SENT_SPLIT = re.compile(r"(?<=[.!?।…])\s+|(?<=[.!?।])(?=\S)")
_WORD = re.compile(r"[^\s,.;:!?।—…]+")
_PAUSE_MARK = re.compile(r"[,…]|[.?!।](?=\s|$)")


def _sentences(text: str) -> list[str]:
    return [s for s in _SENT_SPLIT.split(text.strip()) if s.strip()]


def _first_word(text: str) -> str:
    m = _WORD.search(text)
    return m.group(0).lower().strip("…") if m else ""


def _fingerprint(text: str) -> str:
    return "".join(ch.lower() for ch in text if ch.isalnum())


@dataclass
class SpeechMetrics:
    lines: int
    avg_words_per_sentence: float
    rhythm_cv: float                # coeff. of variation of sentence length
    pause_density: float            # pause marks per sentence
    pause_type_variety: float       # distinct pause types / possible (plans only)
    ack_diversity: float            # unique openers / lines
    repetition_rate: float          # repeated lines + consecutive-opener repeats
    ai_pattern_hits: int
    naturalness_score: float        # 0-100 composite

    def as_dict(self) -> dict:
        return {k: (round(v, 3) if isinstance(v, float) else v)
                for k, v in asdict(self).items()}


class SpeechNaturalnessEvaluator:
    def evaluate(self, lines: list[str],
                 plans: list[SpokenPlan] | None = None) -> SpeechMetrics:
        lines = [l for l in lines if l and l.strip()]
        if not lines:
            return SpeechMetrics(0, 0, 0, 0, 0, 0, 0, 0, 0)

        sentences: list[str] = []
        for l in lines:
            sentences.extend(_sentences(l))
        word_counts = [len(_WORD.findall(s)) for s in sentences] or [0]

        avg_wps = statistics.fmean(word_counts)
        rhythm_cv = (statistics.pstdev(word_counts) / avg_wps) if avg_wps else 0.0

        pause_marks = sum(len(_PAUSE_MARK.findall(l)) for l in lines)
        pause_density = pause_marks / max(1, len(sentences))

        openers = [_first_word(l) for l in lines if _first_word(l)]
        ack_diversity = (len(set(openers)) / len(openers)) if openers else 1.0

        # repetition: repeated whole lines + consecutive identical openers
        fps = [_fingerprint(l) for l in lines]
        repeated_lines = len(fps) - len(set(fps))
        consec_openers = sum(1 for a, b in zip(openers, openers[1:]) if a == b)
        repetition_rate = (repeated_lines + consec_openers) / max(1, len(lines))

        ai_hits = sum(1 for l in lines for p in _AI_DETECT if p.search(l))

        pause_variety = self._pause_type_variety(plans) if plans else 0.0

        score = self._score(avg_wps, rhythm_cv, pause_density, ack_diversity,
                             repetition_rate, ai_hits)
        return SpeechMetrics(
            lines=len(lines),
            avg_words_per_sentence=avg_wps,
            rhythm_cv=rhythm_cv,
            pause_density=pause_density,
            pause_type_variety=pause_variety,
            ack_diversity=ack_diversity,
            repetition_rate=repetition_rate,
            ai_pattern_hits=ai_hits,
            naturalness_score=score,
        )

    @staticmethod
    def _pause_type_variety(plans: list[SpokenPlan]) -> float:
        used = {seg.pause for p in plans for seg in p.segments if seg.pause}
        return len(used) / len(PauseType)

    @staticmethod
    def _score(avg_wps, rhythm_cv, pause_density, ack_diversity,
               repetition_rate, ai_hits) -> float:
        score = 100.0
        # documentation residue is the biggest 'reads like text' tell
        score -= min(40, ai_hits * 12)
        # long sentences read like documents; ideal spoken ~6-14 words
        if avg_wps > 16:
            score -= min(20, (avg_wps - 16) * 2.5)
        elif avg_wps > 22:
            score -= 25
        # monotone rhythm (all sentences same length) sounds robotic
        if rhythm_cv < 0.15:
            score -= 12
        # no pauses at all = one uninterrupted breath
        if pause_density < 0.4:
            score -= 12
        elif pause_density > 4.0:
            score -= 6                      # over-choppy
        # repeated openers/lines
        score -= repetition_rate * 30
        # reward acknowledgement variety
        score -= (1 - ack_diversity) * 10
        return max(0.0, min(100.0, round(score, 1)))


def compare(before: list[str], after: list[str],
            after_plans: list[SpokenPlan] | None = None) -> dict:
    """Before/after naturalness comparison."""
    ev = SpeechNaturalnessEvaluator()
    b = ev.evaluate(before).as_dict()
    a = ev.evaluate(after, plans=after_plans).as_dict()
    delta = {k: round(a[k] - b[k], 3) for k in b
             if isinstance(a[k], (int, float)) and not isinstance(a[k], bool)}
    return {"before": b, "after": a, "delta": delta}
