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
