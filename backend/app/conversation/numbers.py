"""Number Recognition Engine.

Purpose
-------
Real callers speak numbers (consumer no, mobile, OTP, meter no) in fragments,
often with long thinking-pauses between groups: "one zero zero..." <pause>
"...two three..." <pause> "...four five six seven eight nine one."

Each pause longer than `vad_end_silence_ms` ends the VAD utterance, so those
fragments arrive at the Conversation Manager as SEPARATE STT calls / separate
turns. Nothing before this module merges them — CallMemory.scan_user_text
only ever looks at one utterance in isolation. This module buffers digit
fragments across turns until a complete, validated number is assembled, and
never lets a partial number reach a verification tool call.

Two independent jobs:
  1. spoken_to_digits()  — normalize a single utterance's digit words
                            (English/Hindi/Marathi, native + romanized) into
                            a plain digit string, e.g. "one seven zero" → "170".
  2. NumberBuffer         — stateful, per-slot accumulator that merges digit
                            fragments across multiple utterances/turns until
                            the expected length is reached, supports
                            single-digit correction, and rejects impossible
                            lengths before anything is handed to a tool.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ── spoken-digit vocabulary ──────────────────────────────────────────────────
# English (incl. "oh" for zero, common in phone numbers)
_EN = {
    "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}
# Hindi (Devanagari)
_HI_DEV = {
    "शून्य": "0", "एक": "1", "दो": "2", "तीन": "3", "चार": "4", "पांच": "5",
    "पाँच": "5", "छह": "6", "छे": "6", "सात": "7", "आठ": "8", "नौ": "9",
}
# Marathi (Devanagari) — mostly shared with Hindi but a few differ
_MR_DEV = {
    "शून्य": "0", "एक": "1", "दोन": "2", "तीन": "3", "चार": "4", "पाच": "5",
    "सहा": "6", "सात": "7", "आठ": "8", "नऊ": "9",
}
# Romanized Hindi/Marathi (STT frequently outputs Latin script for codemix)
_ROM = {
    "shunya": "0", "sunya": "0", "ek": "1", "do": "2", "don": "2", "teen": "3",
    "tin": "3", "char": "4", "chaar": "4", "paanch": "5", "panch": "5",
    "pach": "5", "che": "6", "chhe": "6", "saha": "6", "saat": "7", "sat": "7",
    "aath": "8", "aat": "8", "nau": "9", "nou": "9",
}

_WORD_TO_DIGIT: dict[str, str] = {**_EN, **_HI_DEV, **_MR_DEV, **_ROM}

# Tokenizer: Devanagari word runs, Latin word runs, or literal digit runs.
_TOKEN_RE = re.compile(r"[ऀ-ॿ]+|[A-Za-z]+|\d+")

# Expected lengths per number-type slot. None = variable/alphanumeric, skip
# length validation (e.g. complaint numbers like "SR2607A42517").
EXPECTED_LENGTHS: dict[str, int] = {
    "consumer_no": 12,
    "mobile": 10,
    "otp": 6,
    "meter_no": 9,
}


def normalize_digit_words(text: str) -> str:
    """Replace recognized digit-word tokens with digit characters IN PLACE,
    preserving spacing/structure and leaving non-digit words untouched.

    Unlike spoken_to_digits() (which drops everything but digits, for
    fragment-buffering), this is safe to run over a whole sentence: it only
    turns "consumer number is one seven zero..." into "consumer number is
    1 7 0...", so existing contiguous-digit-run regexes (CallMemory) can find
    a full number even when the caller spoke it as words in one breath.
    """
    def _sub(m: re.Match) -> str:
        tok = m.group(0)
        if tok.isdigit():
            return tok
        return _WORD_TO_DIGIT.get(tok.lower(), tok)
    return _TOKEN_RE.sub(_sub, text)


def spoken_to_digits(text: str) -> str:
    """Extract a digit string from spoken/mixed text.

    Converts recognized digit words (English/Hindi/Marathi, native script or
    romanized) and literal digit characters into one contiguous digit string,
    in order, ignoring everything else. Non-digit words simply break the
    sequence conceptually but are dropped from the output — callers should
    only feed this text already suspected to be a number fragment (see
    `looks_like_number_fragment`), not a whole free-form sentence, or
    unrelated numbers mentioned in conversation could leak in.
    """
    out: list[str] = []
    for tok in _TOKEN_RE.findall(text):
        if tok.isdigit():
            out.append(tok)
        else:
            d = _WORD_TO_DIGIT.get(tok.lower())
            if d is not None:
                out.append(d)
    return "".join(out)


# Words that indicate a correction rather than a fresh number ("sorry, last
# digit is 2", "galat bola, aakhri ank do hai", "चुकीचे सांगितले").
_CORRECTION_MARKERS = re.compile(
    r"\b(sorry|galat|wrong|last digit|last number|aakhri|आखरी|शेवटच[ाी]|चुकीचे|"
    r"चूक|correct|change (?:it|that))\b",
    re.IGNORECASE,
)


def is_correction(text: str) -> bool:
    return bool(_CORRECTION_MARKERS.search(text))


def looks_like_number_fragment(text: str, max_words: int = 8) -> bool:
    """Heuristic: is this utterance PLAUSIBLY just a piece of a spoken number,
    rather than a normal sentence that happens to contain a digit?

    Used to decide whether to hand the utterance to the NumberBuffer at all.
    Deliberately conservative — a short utterance where digit-words/digits
    dominate the token count, OR a bare run of digit characters.
    """
    text = text.strip()
    if not text:
        return False
    tokens = _TOKEN_RE.findall(text)
    if not tokens or len(tokens) > max_words:
        return False
    digit_like = sum(
        1 for t in tokens if t.isdigit() or t.lower() in _WORD_TO_DIGIT
    )
    return digit_like >= max(1, len(tokens) - 1)  # allow at most 1 filler word


@dataclass
class NumberBuffer:
    """Per-slot accumulator for one in-progress number collection.

    Lives on CallMemory (one instance, re-targeted per active field) so it
    persists across the multiple STT utterances a single spoken number spans.
    """
    field: str | None = None          # "consumer_no" | "mobile" | "otp" | "meter_no"
    digits: str = ""
    turns_active: int = 0

    @property
    def active(self) -> bool:
        return self.field is not None

    @property
    def expected_len(self) -> int | None:
        return EXPECTED_LENGTHS.get(self.field) if self.field else None

    def start(self, field: str) -> None:
        self.field = field
        self.digits = ""
        self.turns_active = 0

    def feed(self, text: str) -> tuple[str, bool]:
        """Add a fragment. Returns (accumulated_digits, is_complete).

        Over-long input (more digits than expected) is truncated to the
        expected length rather than silently accepted — a caller who keeps
        talking past the target length almost always means the number is
        already complete and what follows is unrelated speech.
        """
        self.turns_active += 1
        self.digits += spoken_to_digits(text)
        exp = self.expected_len
        if exp is not None and len(self.digits) > exp:
            self.digits = self.digits[:exp]
        complete = exp is not None and len(self.digits) == exp
        return self.digits, complete

    def correct_last(self, text: str) -> str:
        """Apply a single-digit correction to the tail of the buffer.

        Extracts the digit(s) mentioned in the correction utterance and
        replaces the same number of trailing digits, rather than discarding
        and re-collecting the whole number.
        """
        new_tail = spoken_to_digits(text)
        if not new_tail:
            return self.digits
        n = len(new_tail)
        if n >= len(self.digits):
            self.digits = new_tail[-len(self.digits):] if self.digits else new_tail
        else:
            self.digits = self.digits[:-n] + new_tail
        return self.digits

    def clear(self) -> None:
        self.field = None
        self.digits = ""
        self.turns_active = 0


def is_valid_length(field: str, digits: str) -> bool:
    """Reject impossible numbers before they ever reach a tool call."""
    exp = EXPECTED_LENGTHS.get(field)
    if exp is None:
        return bool(digits)
    return len(digits) == exp
