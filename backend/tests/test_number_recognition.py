"""Number Recognition Engine — cross-utterance digit buffering, correction,
and validation. These are pure/offline; no API calls involved."""
from __future__ import annotations

from backend.app.conversation.numbers import (
    NumberBuffer,
    is_correction,
    is_valid_length,
    looks_like_number_fragment,
    normalize_digit_words,
    spoken_to_digits,
)


def test_spoken_to_digits_english_words():
    assert spoken_to_digits("one seven zero zero") == "1700"


def test_spoken_to_digits_mixed_digits_and_words():
    assert spoken_to_digits("one 7 zero 0") == "1700"


def test_spoken_to_digits_romanized_hindi():
    assert spoken_to_digits("ek do teen") == "123"


def test_normalize_digit_words_preserves_sentence_structure():
    out = normalize_digit_words("my consumer number is one seven zero")
    assert out == "my consumer number is 1 7 0"


def test_number_buffer_merges_fragments_across_pauses():
    """Exact scenario from the spec: number spoken in five separate,
    pause-separated fragments must merge into one complete 12-digit value."""
    buf = NumberBuffer()
    buf.start("consumer_no")
    fragments = ["one zero zero", "two three", "four five six", "seven eight", "nine one"]
    digits = complete = None
    for frag in fragments:
        digits, complete = buf.feed(frag)
        if not complete:
            assert len(digits) < 12
    assert complete is True
    assert digits == "100234567891"


def test_number_buffer_never_completes_early():
    buf = NumberBuffer()
    buf.start("mobile")  # expects 10 digits
    _, complete = buf.feed("nine eight two two")
    assert complete is False
    _, complete = buf.feed("one one one")
    assert complete is False


def test_number_buffer_truncates_overlong_input():
    """A caller who keeps talking past the expected length shouldn't corrupt
    the buffer — treat it as already complete at the expected length."""
    buf = NumberBuffer()
    buf.start("otp")  # expects 6 digits
    digits, complete = buf.feed("one two three four five six seven eight nine")
    assert complete is True
    assert digits == "123456"


def test_correction_updates_only_the_wrong_digit():
    buf = NumberBuffer()
    buf.start("mobile")
    buf.feed("nine eight two two three four five six seven")  # 9 digits, one short
    before = buf.digits
    buf.correct_last("eight")
    assert buf.digits == before[:-1] + "8"
    assert len(buf.digits) == len(before)


def test_is_correction_detects_common_phrasing():
    assert is_correction("sorry, last digit is 2")
    assert is_correction("galat bola, aakhri ank do hai")
    assert not is_correction("my consumer number is 170023456789")


def test_looks_like_number_fragment_accepts_short_digit_utterances():
    assert looks_like_number_fragment("one zero zero")
    assert looks_like_number_fragment("170023456789")


def test_looks_like_number_fragment_rejects_normal_sentences():
    assert not looks_like_number_fragment("my light has been gone since morning")
    assert not looks_like_number_fragment("can you talk in english please")


def test_is_valid_length_rejects_impossible_numbers():
    assert is_valid_length("consumer_no", "170023456789")       # 12 digits, valid
    assert not is_valid_length("consumer_no", "17002345")       # too short
    assert not is_valid_length("mobile", "12345")                # too short
    assert is_valid_length("consumer_no", "170023456789")
