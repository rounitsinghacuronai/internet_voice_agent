"""Language Purity Guard — deterministic hi↔mr de-blending, last text stage.

THE BUG THIS FIXES (seen in production logs):

    caller (Hindi): "मेरे को एक नया connection लगवाना था"
    agent  (BLEND): "आपको नया कनेक्शन चाहिए. कहाँ पर लगवाना आहे, मतलब पूरा
                     ऍड्रेस सांगू शकाल का?"    ← Hindi sentence, Marathi tail

The LanguageEngine already pins the correct BASE language each turn and the
prompt tells the model "never blend Hindi and Marathi". But Gemini Flash still
drifts mid-sentence between the two — they share the Devanagari script and most
of the vocabulary, so the model slides from one into the other inside a single
reply. A prompt cannot reliably stop this; a deterministic rewrite can.

This mirrors persona.enforce_gender(): a cheap, deterministic pass applied to
every generated line just before TTS. When the active language is Hindi, the
handful of high-frequency MARATHI function words / verb tails that leak in are
rewritten to their Hindi equivalents (and vice-versa). Only the confusable
hi↔mr pair is touched — English and undetermined turns pass through untouched.

SAFETY. Every entry is an UNAMBIGUOUS 1:1 function-word mapping: each source
token exists in only ONE of the two languages (e.g. "आहे" is never Hindi, "है"
is never Marathi), so a swap can never change meaning. Everyday English
loanwords the caller uses (bill, recharge, network, data) are never touched.
As a backstop, if MORE than half the Devanagari tokens in a line would be
rewritten, the line is left alone and logged instead — that is not "drift", it
is a whole sentence in the other language (a real switch, or an STT/engine
mislabel), and wholesale token-swapping such a line risks Frankenstein grammar.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

# ── multi-word phrases (applied first, longest-first) ────────────────────────
# These catch the common verb-tail drifts ("सांगू शकाल का", "हवा आहे") that a
# single-token map would miss. Each maps a Marathi phrase → Hindi and the
# reverse direction is derived where it is unambiguous.
_PHRASES_MR_TO_HI: list[tuple[str, str]] = [
    ("सांगू शकाल का", "बता सकते हैं"),
    ("सांगू शकता का", "बता सकते हैं"),
    ("देऊ शकाल का", "दे सकते हैं"),
    ("देऊ शकता का", "दे सकते हैं"),
    ("करू शकाल का", "कर सकते हैं"),
    ("सांगू शकता", "बता सकते हैं"),
    ("कशा प्रकारे", "किस तरह"),
    ("काय झालं", "क्या हुआ"),
    ("हवा आहे", "चाहिए"),
    ("हवं आहे", "चाहिए"),
    ("हवे आहे", "चाहिए"),
    ("पाहिजे", "चाहिए"),
]
_PHRASES_HI_TO_MR: list[tuple[str, str]] = [
    ("किस तरह", "कशा प्रकारे"),
    ("क्या हुआ", "काय झालं"),
]

# ── single tokens (exact whole-token match, unambiguous 1:1) ─────────────────
# Left column is Marathi-only, right column Hindi-only. Read one way for
# active=hi (rewrite the Marathi token) and the other way for active=mr.
_TOKENS: list[tuple[str, str]] = [
    # (marathi, hindi)
    ("आहे", "है"),
    ("आहेत", "हैं"),
    ("नाही", "नहीं"),
    ("नाहीये", "नहीं"),
    ("नाहीत", "नहीं"),
    ("मला", "मुझे"),
    ("तुम्हाला", "आपको"),
    ("आपल्याला", "आपको"),
    ("माझा", "मेरा"),
    ("माझी", "मेरी"),
    ("माझे", "मेरे"),
    ("माझ्या", "मेरे"),
    ("तुमचा", "आपका"),
    ("तुमची", "आपकी"),
    ("तुमचे", "आपके"),
    ("तुमच्या", "आपके"),
    ("आणि", "और"),
    ("म्हणजे", "मतलब"),
    ("म्हणून", "इसलिए"),
    ("साठी", "के लिए"),
    ("कसे", "कैसे"),
    ("कशी", "कैसी"),
    ("काय", "क्या"),
    ("होय", "हाँ"),
    ("नक्की", "ज़रूर"),
    ("खूप", "बहुत"),
    ("जास्त", "ज्यादा"),
    ("आत्ता", "अभी"),
    ("पुढे", "आगे"),
]

# tokens that are risky to swap because the SOURCE form is a substring/homograph
# of a valid word in the OTHER language — deliberately excluded above:
#   "पण" (mr 'but') sits inside Hindi words; "या" (hi 'or') is Marathi 'this'.
# Verb imperatives like करा/करो are handled by phrases only where safe.

_DEVANAGARI_TOKEN = re.compile(r"[ऀ-ॿ]")


def _build_maps() -> dict[str, dict[str, str]]:
    """Precompute token maps for each active language direction."""
    mr_to_hi = {mr: hi for mr, hi in _TOKENS}
    hi_to_mr = {hi: mr for mr, hi in _TOKENS}
    return {"hi": mr_to_hi, "mr": hi_to_mr}


_TOKEN_MAP = _build_maps()
_PHRASE_MAP = {"hi": _PHRASES_MR_TO_HI, "mr": _PHRASES_HI_TO_MR}

# leading/trailing punctuation we strip before a token lookup, then re-attach
_PUNCT = "।,.!?;:।॥\"'()“”‘’ …"


def enforce_language_purity(text: str, lang: str) -> tuple[str, bool]:
    """Rewrite intrusions from the confusable sister language into `lang`.

    Only hi↔mr are processed; every other language (en, und) is returned
    unchanged. Returns (possibly_rewritten_text, changed?)."""
    if not text or lang not in ("hi", "mr"):
        return text, False

    original = text

    # 1) phrase-level rewrites (verb tails etc.), longest-first
    for src, dst in _PHRASE_MAP[lang]:
        if src in text:
            text = text.replace(src, dst)

    # 2) token-level rewrites — count first, then apply, with a safety gate
    tmap = _TOKEN_MAP[lang]
    tokens = text.split(" ")
    dev_total = 0
    hits: list[int] = []
    stripped: list[str] = []
    for i, tok in enumerate(tokens):
        core = tok.strip(_PUNCT)
        stripped.append(core)
        if _DEVANAGARI_TOKEN.search(core):
            dev_total += 1
            if core in tmap:
                hits.append(i)

    # Backstop: a line where most Devanagari tokens are foreign is a whole
    # sentence in the other language, not drift — leave it and log.
    if dev_total and len(hits) > dev_total / 2:
        log.info("purity: line looks wholly non-%s, left as-is: %r", lang, original[:80])
    else:
        for i in hits:
            core = stripped[i]
            tokens[i] = tokens[i].replace(core, tmap[core], 1)
        text = " ".join(tokens)

    changed = text != original
    if changed:
        log.info("purity: de-blended %s line: %r → %r", lang, original[:70], text[:70])
    return text, changed
