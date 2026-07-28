"""Regression tests for a systemic bug found while investigating "the voice
agent switches to Marathi without reason": Python's \\b (word boundary) does
not treat Devanagari vowel signs/anusvara/chandrabindu (matras — ा ी ो ं ँ े
etc.) as word characters, so a \\b placed right after a Devanagari word
silently fails to match whenever that word happens to end in one of them —
which is most Hindi/Marathi words. This wasn't only in the language-switch
logic (conversation/language.py, covered by test_language_engine.py); the
same regex pattern was copy-pasted into three other files, each silently
dropping the majority of its real-world Devanagari matches:

  - conversation/numbers.py: restart/remove-last/position phrases
  - speech/director.py: caller-emotion detection (angry/frustrated/worried/calm)
  - speech/prosody.py: question detection (rising intonation)

Each is fixed the same way conversation/language.py's own _MR_MARKERS/
_HI_MARKERS already did it correctly: Devanagari terms matched by plain
substring containment, Latin/romanized terms kept on \\b-regex (which works
correctly for them). Pure function tests, no network/LLM.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.conversation.numbers import (
    wants_restart, wants_remove_last, parse_position,
)
from backend.app.speech.director import detect_caller_emotion
from backend.app.speech.prosody import is_question


# ── numbers.py ─────────────────────────────────────────────────────────────

def test_wants_restart_devanagari_phrases_ending_in_matras():
    for text in ("दुबारा से बताता हूं", "फिर से शुरू करते हैं", "नए सिरे से बताता हूं",
                 "पुन्हा सांगतो", "काढून टाका आणि पुन्हा सांगतो"):
        assert wants_restart(text), text


def test_wants_restart_devanagari_phrases_ending_in_bare_consonant_still_work():
    assert wants_restart("नवीन नंबर देतो")
    assert wants_restart("रद्द करा")


def test_wants_remove_last_either_word_order_devanagari():
    assert wants_remove_last("आखरी नंबर हटाओ")
    assert wants_remove_last("शेवटचा नंबर काढा")
    assert wants_remove_last("अंतिम नंबर मिटा दो")
    assert wants_remove_last("हटाओ आखरी वाला")


def test_parse_position_do_two_devanagari_count_word():
    """'दो' (two) ends in the ो vowel sign — the exact word that silently
    failed to match its own trailing \\b."""
    assert parse_position("पहले दो अंक बताओ") == ("first", 2)
    assert parse_position("फर्स्ट दो सांगा") == ("first", 2)


def test_parse_position_devanagari_position_words():
    assert parse_position("पहले चार अंक बताओ") == ("first", 4)
    assert parse_position("शेवट दोन अंक सांगा") == ("last", 2)
    assert parse_position("आखरी तीन बताओ") == ("last", 3)


def test_parse_position_latin_still_works():
    assert parse_position("first two digits") == ("first", 2)
    assert parse_position("last four numbers") == ("last", 4)


# ── director.py: caller emotion ─────────────────────────────────────────────

def test_angry_repeat_devanagari():
    assert detect_caller_emotion("रोज़ रोज़ यही problem") == "angry"
    assert detect_caller_emotion("हर बार यही होता है") == "angry"


def test_frustrated_devanagari():
    assert detect_caller_emotion("फिर से वही दिक्कत") == "frustrated"


def test_worried_devanagari():
    assert detect_caller_emotion("मुझे डर लग रहा है") == "worried"


def test_calm_devanagari():
    assert detect_caller_emotion("शुक्रिया भाई") == "calm"
    assert detect_caller_emotion("बहुत अच्छा, धन्यवाद") == "calm"


def test_angry_latin_still_works():
    assert detect_caller_emotion("this service is pathetic") == "angry"


# ── prosody.py: question detection ──────────────────────────────────────────

def test_question_devanagari_interrogatives():
    for text in ("आप क्या कहते हैं", "यह कैसे होगा", "आप कहाँ हैं",
                 "तुम्ही कशी आहात", "बरं आहे कसं", "हे कसे झाले",
                 "मला सांगाल का", "तुम्ही बता सकते", "हे चालेल का"):
        assert is_question(text), text


def test_question_terminal_mark_still_works():
    assert is_question("क्या यह सही है?")


def test_non_question_devanagari_statement():
    assert not is_question("ठीक है धन्यवाद")


def test_question_latin_still_works():
    assert is_question("could you check my bill")
    assert not is_question("bill 1942 rupees hai")
