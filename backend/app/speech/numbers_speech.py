"""Number Pronunciation Planning (the OUTPUT side).

The Number Recognition Engine (conversation/numbers.py) handles numbers coming
IN from the caller. This handles numbers going OUT to the caller: consumer
numbers, mobile numbers, OTPs, meter numbers and complaint IDs must be spoken
in clear, digit-by-digit groups with a small pause between groups — never let
Sarvam rush "170012345678" into one giant cardinal.

Amounts, times and short numbers are deliberately LEFT ALONE: a rupee amount
should be spoken naturally as words (the LLM already does this), and "10:30" or
"15th" read fine as-is. We only regroup long identifier-style digit runs.
"""
from __future__ import annotations

import re

# Currency context that marks a digit run as an AMOUNT (spoken as words, not
# digit-by-digit). Checked immediately around the run.
_CURRENCY = re.compile(
    r"(₹|rs\.?|inr|rupees?|रुपये|रुपए|रुपयां|रुपयांचा|रु\.?)",
    re.IGNORECASE,
)

# A "spoken number" candidate: 5+ digits, possibly already split by the model
# into space/hyphen groups ("1700 1234 5678" or "1 7 0 0 ..."). Bounded so it
# doesn't glue onto surrounding word characters.
_DIGIT_RUN = re.compile(r"(?<![\w])(\d(?:[ \-]?\d){4,})(?![\w])")


def _group_digits(digits: str) -> str:
    """Group a pure-digit string into natural spoken clusters, each digit voiced
    individually and a comma (Sarvam micro-pause) between clusters."""
    n = len(digits)
    if n == 12:                      # consumer number → 4-4-4
        groups = [digits[0:4], digits[4:8], digits[8:12]]
    elif n == 10:                    # mobile → 5-5
        groups = [digits[0:5], digits[5:10]]
    elif n == 9:                     # meter → 3-3-3
        groups = [digits[0:3], digits[3:6], digits[6:9]]
    elif n == 6:                     # OTP → 3-3
        groups = [digits[0:3], digits[3:6]]
    elif n == 11:                    # 11-digit → 4-4-3
        groups = [digits[0:4], digits[4:8], digits[8:11]]
    else:                            # fall back to threes
        groups = [digits[i:i + 3] for i in range(0, n, 3)]
    return ", ".join(" ".join(g) for g in groups)


def _looks_like_amount(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 8):start]
    after = text[end:end + 12]
    return bool(_CURRENCY.search(before) or _CURRENCY.search(after))


def format_numbers_for_speech(text: str, lang: str = "mr") -> tuple[str, bool]:
    """Regroup long identifier numbers for clear, unhurried speech.

    Returns (new_text, changed). Idempotent: text already grouped as
    '1 7 0 0, ...' collapses and regroups to the same canonical form, so
    running it twice never doubles the spacing.

    Alphanumeric complaint/SR codes (e.g. 'SR260782D4E6') are deliberately left
    alone: the LLM already voices them digit-by-digit in the caller's language
    (see prompts/modules/02_style.md), and mechanically spacing ASCII codes
    reads worse than the model's phonetic rendering.
    """
    changed = False

    # long pure-digit runs (consumer/mobile/OTP/meter)
    def _digit_sub(m: re.Match) -> str:
        nonlocal changed
        raw = m.group(1)
        digits = re.sub(r"[ \-]", "", raw)
        if not digits.isdigit() or len(digits) < 5:
            return raw
        if _looks_like_amount(text, m.start(1), m.end(1)):
            return raw                       # spoken as words elsewhere
        changed = True
        return _group_digits(digits)

    text = _DIGIT_RUN.sub(_digit_sub, text)
    return text, changed
