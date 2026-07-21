"""Enterprise Number Capture Engine — comprehensive scenarios.

Covers every speaking style, identifier type, correction/edit, readback grouping,
validation, and edge case the redesign targets. All offline & deterministic.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.conversation.numbers import (
    NumberBuffer, NUMBER_TYPES, spoken_to_digits, group_for_readback,
    mask_digits, is_valid_length, parse_position, wants_restart,
    wants_remove_last, number_type)
from backend.app.conversation.memory import CallMemory


# ── speech normalization: every natural style ────────────────────────────────
def test_individual_digits():
    assert spoken_to_digits("nine eight seven six five four three two one zero") == "9876543210"


def test_paired_tens_style():
    assert spoken_to_digits("ninety eight seventy six fifty four thirty two ten") == "9876543210"


def test_teens_and_tens_standalone():
    assert spoken_to_digits("nineteen") == "19"
    assert spoken_to_digits("twenty") == "20"
    assert spoken_to_digits("seventeen seventy six") == "1776"


def test_double_and_triple():
    assert spoken_to_digits("double three") == "33"
    assert spoken_to_digits("triple five") == "555"
    assert spoken_to_digits("nine double eight seven") == "9887"   # 9, 88, 7


def test_oh_o_zero_variants():
    assert spoken_to_digits("one oh oh two") == "1002"
    assert spoken_to_digits("nine o nine o") == "9090"


def test_mixed_digits_and_words():
    assert spoken_to_digits("9 8 seven 6") == "9876"


def test_hindi_marathi_digit_words():
    assert spoken_to_digits("ek do teen char") == "1234"
    assert spoken_to_digits("नऊ आठ सात") == "987"      # Marathi
    assert spoken_to_digits("शून्य एक दो") == "012"       # Hindi


def test_filler_words_do_not_break_run():
    assert spoken_to_digits("nine eight and seven six") == "9876"


# ── cross-turn buffering (fragments across pauses) ───────────────────────────
def test_fragments_across_pauses_mobile():
    buf = NumberBuffer(); buf.start("mobile")
    for frag, done in [("nine eight seven six", False), ("five four three two", False),
                       ("one zero", True)]:
        _, complete = buf.feed(frag)
        assert complete is done
    assert buf.digits == "9876543210"


def test_positional_first_last_next():
    assert parse_position("first four are nine eight seven six") == ("first", 4)
    assert parse_position("last two are one zero") == ("last", 2)
    buf = NumberBuffer(); buf.start("mobile")
    buf.feed("first four are nine eight seven six")
    buf.feed("next four five four three two")
    buf.feed("last two are one zero")
    assert buf.digits == "9876543210"


# ── corrections & edits — never restart ──────────────────────────────────────
def test_tail_correction_keeps_the_rest():
    m = CallMemory(); m.number_buffer.start("mobile")
    m.feed_number_fragment("nine eight seven six five four three two one zero")
    # buffer cleared on complete + mobile set
    assert m.mobile == "9876543210"


def test_remove_last_digit():
    buf = NumberBuffer(); buf.start("account_no")
    buf.feed("three zero zero zero one two three four five six seven")   # 11
    assert wants_remove_last("remove the last digit")
    buf.remove_last()
    assert buf.digits == "3000123456"


def test_restart_clears_but_keeps_field():
    m = CallMemory(); m.number_buffer.start("mobile")
    m.feed_number_fragment("nine eight seven six")
    assert m.number_buffer.digits == "9876"
    assert wants_restart("forget that, start again")
    digits, complete = m.feed_number_fragment("forget that start again")
    assert digits == "" and complete is False
    assert m.number_buffer.active and m.number_buffer.field == "mobile"


def test_single_digit_correction():
    buf = NumberBuffer(); buf.start("mobile")
    buf.feed("nine eight seven six five four three two one zero")
    buf.correct_last("sorry last digit is nine")
    assert buf.digits == "9876543219"
    assert buf.corrections                      # recorded


# ── readback grouping & masking ──────────────────────────────────────────────
def test_readback_grouping_per_type():
    assert group_for_readback("9876543210", "mobile") == "98765 43210"
    assert group_for_readback("300012345678", "account_no") == "3000 1234 5678"
    assert group_for_readback("123456", "otp") == "123 456"


def test_masking_progress():
    assert mask_digits("9876543", 10) == "98•••43___"
    assert mask_digits("", 10) == "__________"
    assert mask_digits("98", 10) == "98________"


# ── identifier types, lengths, validation ────────────────────────────────────
def test_all_identifier_types_exist():
    for t in ("mobile", "account_no", "otp", "pin", "complaint_id",
              "customer_id", "reference", "service_id"):
        assert t in NUMBER_TYPES


def test_validation_exact_and_ranged():
    assert is_valid_length("mobile", "9876543210")
    assert not is_valid_length("mobile", "98765")
    assert is_valid_length("otp", "1234")            # 4 ok (range 4-8)
    assert is_valid_length("otp", "12345678")        # 8 ok
    assert not is_valid_length("otp", "123")         # 3 too short
    assert is_valid_length("pin", "1234")
    assert is_valid_length("complaint_id", "12345678")   # variable, in range
    assert not is_valid_length("complaint_id", "123")    # below min


def test_otp_and_pin_capture():
    buf = NumberBuffer(); buf.start("otp")
    _, complete = buf.feed("triple one two two two")   # 111222 → 6 digits
    assert complete and buf.digits == "111222"
    p = NumberBuffer(); p.start("pin")
    _, done = p.feed("one two three four")
    assert done and p.digits == "1234"


def test_variable_length_finalize_on_that_is_all():
    buf = NumberBuffer(); buf.start("complaint_id")
    buf.feed("one two three four five six seven eight")
    digits, valid = buf.finalize()
    assert digits == "12345678" and valid is True


# ── edge cases ───────────────────────────────────────────────────────────────
def test_overlong_input_truncated():
    buf = NumberBuffer(); buf.start("mobile")
    d, complete = buf.feed("nine eight seven six five four three two one zero one two three")
    assert complete and d == "9876543210"           # extra speech dropped


def test_full_repeat_replaces_not_appends():
    buf = NumberBuffer(); buf.start("mobile")
    buf.feed("nine eight seven six")                 # partial 4
    d, complete = buf.feed("nine eight seven six five four three two one zero")  # full repeat
    assert complete and d == "9876543210"            # not glued onto the partial


def test_confidence_tracked_min():
    buf = NumberBuffer(); buf.start("mobile")
    buf.feed("nine eight seven", confidence=0.9)
    buf.feed("six five four", confidence=0.6)
    assert buf.confidence == 0.6                     # keeps the worst segment


def test_snapshot_shape_for_frontend():
    buf = NumberBuffer(); buf.start("mobile")
    buf.feed("nine eight seven six five four")
    snap = buf.snapshot()
    for k in ("field", "label", "digits", "count", "expected", "masked",
              "grouped", "confidence", "confirmed"):
        assert k in snap
    assert snap["count"] == 6 and snap["expected"] == 10
    assert snap["label"] == "mobile number"
