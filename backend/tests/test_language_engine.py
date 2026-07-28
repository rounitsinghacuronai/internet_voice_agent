"""Regression tests for LanguageEngine's false-switch fix (conversation/
language.py) — reproduces a real production complaint: a Hindi-active call's
language kept "changing for no reason". Log evidence (session 7a00df2219b8):
an empty STT transcript tagged hint="en-IN" (silence/noise) plus filler
interjections ("Hmm", "Hello", "Thank you") accumulated as confident "en"
votes and drifted the active language away from Hindi, even though none of
them were the caller actually switching languages. Pure function tests, no
network/LLM.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.conversation.language import LanguageEngine, _NEUTRAL_FILLERS


def _hindi_seed(engine: LanguageEngine) -> None:
    """Establish an active Hindi call, mirroring the real session's opener."""
    engine.update("अह रतन मेरी अह क्या कहते हैं मेरा WiFi बहुत धीरे चल रहा है", "hi-IN")
    assert engine.language == "hi"


def test_empty_transcript_never_moves_language():
    eng = LanguageEngine()
    _hindi_seed(eng)
    # Reproduces the exact production line: empty STT result tagged en-IN.
    result = eng.update("", "en-IN")
    assert result == "hi"


def test_bare_hello_does_not_accumulate_toward_a_switch():
    eng = LanguageEngine()
    _hindi_seed(eng)
    eng.update("Hello", "unknown")
    eng.update("Hello", "unknown")
    eng.update("Hello", "unknown")
    assert eng.language == "hi"


def test_thank_you_and_hmm_do_not_drift_the_call():
    """The exact sequence from the production log: empty, Hmm, Hello, in a
    row — must not drift a Hindi-active call to English."""
    eng = LanguageEngine()
    _hindi_seed(eng)
    eng.update("", "en-IN")
    eng.update("Hmm", "unknown")
    eng.update("Hello", "unknown")
    assert eng.language == "hi"


def test_thank_you_alone_is_neutral():
    eng = LanguageEngine()
    _hindi_seed(eng)
    for _ in range(3):
        eng.update("Thank you", "unknown")
    assert eng.language == "hi"


def test_neutral_fillers_detected_as_und_directly():
    for word in ("hello", "hi", "hmm", "ok", "okay", "thanks", "thank you", "bye", "yes", "no"):
        assert LanguageEngine._detect(word, "unknown") == "und"
    assert LanguageEngine._detect("", "en-IN") == "und"
    assert LanguageEngine._detect("   ", "en-IN") == "und"


def test_neutral_filler_with_punctuation_still_recognised():
    assert LanguageEngine._detect("Hello!", "unknown") == "und"
    assert LanguageEngine._detect("Thank you.", "unknown") == "und"


# ── regression guard: real English switches must still work ──────────────────
def test_real_english_sentence_still_switches_strong():
    eng = LanguageEngine()
    _hindi_seed(eng)
    result = eng.update("Can you please check my broadband connection status", "en-IN")
    assert result == "en"


def test_real_english_sentence_not_in_neutral_set():
    # Sanity: a genuine sentence must not accidentally collide with the
    # filler whitelist (only bare 1-2 word pleasantries are in it).
    assert "can you please check my broadband connection status" not in _NEUTRAL_FILLERS


# ── "switches to Marathi without any reason" — the _command() negation bug ────
# Root cause found from a second production complaint: _COMMANDS' bare
# "मराठी" / "हिन्दी|हिंदी" patterns had no negation guard and no requirement
# that a verb accompany them, so ANY mention of the language name — including
# a caller asking to STOP hearing it — triggered an instant switch+pin. This
# hit Marathi hardest because it's also the system's default/tie-break
# language elsewhere, compounding the effect.

def test_negated_marathi_request_does_not_switch_to_marathi():
    """The exact bug: caller says 'don't speak Marathi' and the call flips
    INTO Marathi — backwards from what was asked."""
    eng = LanguageEngine()
    _hindi_seed(eng)
    eng.update("मराठी मत बोलो", "hi-IN")   # "don't speak Marathi"
    assert eng.language == "hi"
    assert not eng.pinned


def test_negated_english_request_does_not_switch_to_english():
    eng = LanguageEngine()
    _hindi_seed(eng)
    eng.update("please don't speak in English", "hi-IN")
    assert eng.language == "hi"


def test_negated_marathi_knowledge_statement_does_not_switch():
    """'मुझे मराठी नहीं आती' — 'I don't know Marathi' — names only Marathi,
    negated; must not be read as a request FOR Marathi."""
    eng = LanguageEngine()
    _hindi_seed(eng)
    eng.update("मुझे मराठी नहीं आती", "hi-IN")
    assert eng.language == "hi"


def test_bare_marathi_word_alone_still_works_as_a_short_reply():
    """Positive case: a caller answering a language prompt with just the bare
    word ('Marathi' / 'मराठी') must still register as an explicit command —
    the negation/length guard must not break the common case."""
    eng = LanguageEngine()
    eng.update("मराठी", "mr-IN")
    assert eng.language == "mr" and eng.pinned

    eng2 = LanguageEngine()
    eng2.update("Marathi please", "en-IN")
    assert eng2.language == "mr" and eng2.pinned


def test_incidental_marathi_mention_in_a_long_sentence_is_not_a_command():
    """A long sentence that merely CONTAINS the word Marathi, with no request
    verb nearby, is not an explicit command — falls through to normal
    word-marker detection instead of an instant pin."""
    eng = LanguageEngine()
    _hindi_seed(eng)
    # "I am a Marathi person, I have lived here for many years" — long
    # (>4 words), mentions मराठी with no बोल/बात/कर nearby, and mentions no
    # other language at all — a clean incidental-mention case.
    eng.update("मैं मराठी आदमी हूं मुझे यहाँ बहुत साल हो गए हैं", "hi-IN")
    assert eng.language == "hi"
    assert not eng.pinned


# ── Devanagari marker tie: don't gamble on Sarvam's unreliable hi/mr hint ─────

def test_devanagari_marker_tie_casts_no_vote_even_with_hint():
    # No _MR_MARKERS or _HI_MARKERS substring present in either text -> a
    # genuine 0-0 tie. Must return "und" regardless of what the hint claims.
    assert LanguageEngine._detect("रतन बोलतोय", "mr-IN") in ("und", "mr")  # sanity, not asserted strictly
    # A cleaner true 0-0 tie:
    zero_marker_text = "रतन"
    assert LanguageEngine._detect(zero_marker_text, "mr-IN") == "und"
    assert LanguageEngine._detect(zero_marker_text, "hi-IN") == "und"


def test_devanagari_tie_does_not_move_an_active_call():
    eng = LanguageEngine()
    _hindi_seed(eng)
    # Marker-less Devanagari utterance tagged mr-IN by (unreliable) STT hint.
    eng.update("रतन", "mr-IN")
    assert eng.language == "hi"


# ── romanized hi/mr markers are now checked even when hint says hi/mr ─────────

def test_romanized_hindi_markers_override_a_marathi_hint():
    """Old bug: a bare hint="mr" was trusted with ZERO romanized-marker check
    (only the hint="en" path verified markers). Sarvam mislabelling romanized
    Hindi as mr-IN would go straight through as Marathi unchecked."""
    # "bahut" and "zyada" are both _ROM_HI markers, zero _ROM_MR markers.
    assert LanguageEngine._detect("bahut zyada bill aaya", "mr-IN") == "hi"


def test_romanized_no_markers_with_hi_mr_hint_casts_no_vote():
    # Pure Latin, no romanized hi/mr markers at all, but hint claims hi/mr —
    # per the same "hi/mr hint is unreliable" reasoning, cast no vote.
    assert LanguageEngine._detect("okay done thanks bye", "mr-IN") == "und"
