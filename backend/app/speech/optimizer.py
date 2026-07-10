"""Response Optimizer — the first stage after Gemini.

Two jobs, in order of cost:

  1. clean()  — deterministic, zero-latency. Strips written-document artefacts
     that leak into speech (markdown, parentheticals), rewrites AI/IVR
     phrasings into natural spoken alternatives ("I apologize" → "I'm sorry",
     "Please note that" → "Just so you know"), and applies English spoken
     contractions. NEVER touches a number, name, amount, or fact.

  2. restructure() — optional, async, uses the LLM to re-shape a whole reply
     into natural spoken thought-groups. Higher human-feel, but adds latency
     and another failure mode, so it is OFF by default (speech_llm_restructure)
     and guarded: if the rewrite drops any digit/identifier present in the
     original, the original is kept.
"""
from __future__ import annotations

import logging
import re

from .lexicon import AI_PATTERNS, SPOKEN_FORMS
from .plan import StyleProfile, norm_lang

log = logging.getLogger(__name__)

# artefacts that are fine on a page but wrong in the ear
_MD = re.compile(r"[*_#`]+")
_PARENS = re.compile(r"\([^)]*\)")
_BULLET = re.compile(r"^\s*(?:[-•]|\d+[.)])\s*", re.M)
_LEAD_JUNK = re.compile(r"^[\s,;:—-]+")
_MULTISPACE = re.compile(r"\s+")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.;:!?।])")
_ID_RUN = re.compile(r"[A-Za-z]{0,3}\d{4,}[A-Za-z0-9]*|\d(?:[ \-]?\d){4,}")


def _digit_fingerprints(text: str) -> set[str]:
    """Long identifiers/numbers in a line, normalised (spaces/hyphens removed),
    for verifying a rewrite didn't silently drop or mangle a fact."""
    out = set()
    for m in _ID_RUN.finditer(text):
        norm = re.sub(r"[ \-]", "", m.group(0)).lower()
        if sum(c.isdigit() for c in norm) >= 4:
            out.add(norm)
    return out


class ResponseOptimizer:
    def __init__(self, llm=None):
        self.llm = llm   # LLMProvider | None (only needed for restructure())

    # ── 1. deterministic clean + de-AI ───────────────────────────────────────
    def clean(self, text: str, lang: str) -> tuple[str, list[str]]:
        lang = norm_lang(lang)
        notes: list[str] = []
        original = text

        text = _PARENS.sub(" ", text)
        text = _MD.sub("", text)
        text = _BULLET.sub("", text)

        # de-AI: rewrite documentation/IVR phrasings
        for pattern, repl in AI_PATTERNS:
            replacement = repl.get(lang, repl.get("en", ""))
            new = pattern.sub(replacement, text)
            if new != text:
                notes.append("de-AI")
                text = new

        # English spoken contractions (case-insensitive, capitalization preserved:
        # "It is" → "It's", "it is" → "it's")
        if lang == "en":
            for pattern, repl in SPOKEN_FORMS:
                new = pattern.sub(lambda m, r=repl: _match_case(m.group(0), r), text)
                if new != text:
                    notes.append("contraction")
                    text = new

        text = _tidy(text)
        if text != original and "de-AI" not in notes and "contraction" not in notes:
            notes.append("clean")
        return text, notes

    # ── 2. optional LLM restructure (async, guarded) ──────────────────────────
    async def restructure(self, text: str, lang: str, profile: StyleProfile) -> tuple[str, bool]:
        """Rewrite into natural spoken thought-groups without changing facts.
        Returns (text, used_llm). Falls back to the input on any doubt."""
        if not self.llm or not text.strip():
            return text, False
        lang_name = {"mr": "Marathi", "hi": "Hindi", "en": "English"}[norm_lang(lang)]
        sys = (
            "You rewrite one line of an Indian electricity customer-care agent's "
            "reply so it sounds like a calm, experienced human speaking on the phone, "
            "not text being read. STRICT RULES: keep the meaning and EVERY number, "
            "amount, name, date and complaint/consumer ID EXACTLY as given — never add, "
            "drop or change a digit. Keep the same language "
            f"({lang_name}); do not translate. Use short, natural spoken clauses. "
            "Remove any documentation tone. Return ONLY the rewritten line, nothing else. "
            f"Deliver it as: {profile.emotion.value}, {profile.label}."
        )
        try:
            out = await self.llm.complete(
                [{"role": "system", "content": sys}, {"role": "user", "content": text}],
                temperature=0.3,
            )
        except Exception as e:                       # provider hiccup → keep original
            log.warning("speech restructure failed, keeping original: %s", e)
            return text, False
        out = _tidy(_PARENS.sub(" ", _MD.sub("", out or "")))
        if not out:
            return text, False
        # fact guard: the rewrite must preserve every long identifier/number
        if not _digit_fingerprints(text).issubset(_digit_fingerprints(out)):
            log.warning("speech restructure dropped a number — keeping original")
            return text, False
        return out, True


def _match_case(matched: str, repl: str) -> str:
    """Give the replacement the same leading-letter case as what it replaced."""
    if matched[:1].isupper():
        return repl[:1].upper() + repl[1:]
    return repl


def _tidy(text: str) -> str:
    text = _LEAD_JUNK.sub("", text)
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = re.sub(r"([,;:])\1+", r"\1", text)        # collapse ",," etc.
    text = _MULTISPACE.sub(" ", text).strip()
    return text
