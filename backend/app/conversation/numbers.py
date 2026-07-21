"""Number Recognition Engine.

Purpose
-------
Real callers speak numbers (account no, mobile, OTP) in fragments,
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
from dataclasses import dataclass, field as _dc_field

# ── spoken-digit vocabulary ──────────────────────────────────────────────────
# English (incl. "oh"/"o" for zero, common in phone numbers)
_EN = {
    "zero": "0", "oh": "0", "o": "0", "nought": "0", "one": "1", "two": "2",
    "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
    "eight": "8", "nine": "9",
}
# Teens spoken as one word — a caller reading a number often says "nineteen"
# meaning "1 9". These map to a TWO-digit string.
_TEEN = {
    "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
    "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
    "eighteen": "18", "nineteen": "19",
}
# Tens: standalone → "N0"; when followed by a unit ("ninety eight") → "98".
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
# Repetition words: "double three" → 33, "triple five" → 555.
_MULT = {"double": 2, "triple": 3, "treble": 3}
# Words to ignore between digits so they don't break a run ("nine eight AND seven").
_FILLER = {"and", "is", "are", "the", "my", "number", "it", "its", "it's",
           "hai", "ahe", "aahe", "number", "no", "digit", "digits"}
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

# ── identifier type catalogue ────────────────────────────────────────────────
# Valid leading digits for an Indian mobile number (TRAI: 10-digit mobiles begin
# 6/7/8/9). Used for LIVE validation — a mobile that starts 0-5 is rejected the
# instant the first digit lands, so the caller is corrected immediately instead
# of after speaking all ten.
MOBILE_PREFIXES: frozenset[str] = frozenset("6789")


@dataclass(frozen=True)
class NumberType:
    """Everything the capture engine needs to know about one identifier kind."""
    name: str
    exact: int | None = None            # auto-complete at this exact length
    min_len: int | None = None          # for variable-length (complete on pause)
    max_len: int | None = None
    groups: tuple[int, ...] = ()        # read-back grouping, e.g. (5,5) or (4,4,4)
    label: str = ""                     # spoken label ("mobile number")
    prefixes: frozenset[str] = frozenset()  # allowed leading digits ('' = any)

    def is_complete(self, digits: str) -> bool:
        n = len(digits)
        if self.exact is not None:
            return n == self.exact
        if self.min_len is not None:
            return n >= self.min_len and (self.max_len is None or n <= self.max_len)
        return bool(digits)

    def prefix_ok(self, digits: str) -> bool:
        """Live prefix check: the first digit must be allowed for this type.
        Empty buffer or no configured prefixes → always OK (nothing to reject)."""
        if not digits or not self.prefixes:
            return True
        return digits[0] in self.prefixes

    def valid(self, digits: str) -> bool:
        n = len(digits)
        # A range (min_len) takes precedence for VALIDATION so a variable-length
        # OTP (4-8) accepts a 4-digit code even though it auto-completes at 6.
        if self.min_len is not None:
            hi = self.max_len or 10 ** 9
            len_ok = self.min_len <= n <= hi
        elif self.exact is not None:
            len_ok = n == self.exact
        else:
            len_ok = bool(digits)
        return len_ok and self.prefix_ok(digits)


NUMBER_TYPES: dict[str, NumberType] = {
    "mobile":       NumberType("mobile", exact=10, groups=(5, 5), label="mobile number",
                               prefixes=MOBILE_PREFIXES),
    "account_no":   NumberType("account_no", exact=12, groups=(4, 4, 4), label="account number"),
    "otp":          NumberType("otp", exact=6, min_len=4, max_len=8, groups=(3, 3), label="OTP"),
    "pin":          NumberType("pin", exact=4, groups=(4,), label="PIN"),
    "complaint_id": NumberType("complaint_id", min_len=6, max_len=14, label="complaint number"),
    "customer_id":  NumberType("customer_id", min_len=6, max_len=14, label="customer ID"),
    "reference":    NumberType("reference", min_len=4, max_len=16, label="reference number"),
    "service_id":   NumberType("service_id", min_len=6, max_len=14, label="service ID"),
}

# Back-compat: existing code/tests import EXPECTED_LENGTHS and expect fixed
# exact-length slots. Derived from the catalogue so there is one source of truth.
EXPECTED_LENGTHS: dict[str, int] = {
    name: t.exact for name, t in NUMBER_TYPES.items() if t.exact is not None
}


def number_type(field: str | None) -> NumberType | None:
    return NUMBER_TYPES.get(field) if field else None


def mask_digits(digits: str, expected: int | None = None) -> str:
    """Privacy-preserving live display: keep the first 2 and last 2 visible,
    mask the middle; pad remaining expected slots with underscores.
    '9876543' (exp 10) → '98•••43•••' style → '98•••43___'."""
    if not digits:
        return "_" * (expected or 0)
    n = len(digits)
    if n <= 4:
        shown = digits
    else:
        shown = digits[:2] + "•" * (n - 4) + digits[-2:]
    if expected and n < expected:
        shown += "_" * (expected - n)
    return shown


def group_for_readback(digits: str, field: str | None = None) -> str:
    """Format digits into natural spoken groups so the read-back sounds human
    ('98765 43210') instead of a robotic single run. Grouping comes from the
    identifier type; unknown/variable types fall back to groups of 3-4."""
    if not digits:
        return ""
    t = number_type(field)
    groups = t.groups if (t and t.groups) else ()
    if not groups:
        # sensible default: 4-4-… for long, 3-3 for short
        size = 4 if len(digits) > 6 else 3
        groups = tuple(size for _ in range((len(digits) + size - 1) // size))
    out, idx = [], 0
    for g in groups:
        if idx >= len(digits):
            break
        out.append(digits[idx:idx + g])
        idx += g
    if idx < len(digits):                  # trailing remainder (over-long)
        out.append(digits[idx:])
    return " ".join(out)


# ── editing / positional intents ─────────────────────────────────────────────
_RESTART_MARKERS = re.compile(
    r"\b(forget (that|it|this)|start (over|again|fresh)|restart|scratch that|"
    r"clear (it|that)|new number|another number|use another|दुबारा|फिर से|"
    r"नए सिरे|पुन्हा|नवीन नंबर|काढून टाका|रद्द)\b", re.IGNORECASE)

_REMOVE_LAST_MARKERS = re.compile(
    r"\b(remove|delete|drop|hata|hatao|काढा|हटाओ|मिटा)\b.*\b(last|end|आखरी|शेवट|अंतिम)\b"
    r"|\b(last|आखरी|शेवट)\b.*\b(remove|delete|galat|wrong)\b", re.IGNORECASE)

_POSITION_RE = re.compile(
    r"\b(first|last|next|फर्स्ट|पहल[ेा]|आखरी|शेवट|पुढ[ीच]|next)\s+"
    r"(\d+|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"एक|दो|तीन|चार|पांच|दोन|पाच)\b", re.IGNORECASE)

_POS_WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                 "seven": 7, "eight": 8, "nine": 9, "ten": 10, "एक": 1, "दो": 2,
                 "दोन": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाच": 5}


def wants_restart(text: str) -> bool:
    """Caller wants to abandon what's captured and start the number fresh."""
    return bool(_RESTART_MARKERS.search(text or ""))


def wants_remove_last(text: str) -> bool:
    return bool(_REMOVE_LAST_MARKERS.search(text or ""))


def parse_position(text: str) -> tuple[str, int] | None:
    """Detect 'first four …', 'last two …', 'next four …' → (which, count).
    which ∈ {first,last,next}. Used to place a fragment at a known position."""
    m = _POSITION_RE.search(text or "")
    if not m:
        return None
    which = m.group(1).lower()
    which = ("first" if which in ("first", "फर्स्ट", "पहले", "पहला")
             else "last" if which in ("last", "आखरी", "शेवट")
             else "next")
    raw = m.group(2).lower()
    count = int(raw) if raw.isdigit() else _POS_WORD_NUM.get(raw, 0)
    return (which, count) if count else None


def normalize_digit_words(text: str) -> str:
    """Replace recognized digit-word tokens with digit characters IN PLACE,
    preserving spacing/structure and leaving non-digit words untouched.

    Unlike spoken_to_digits() (which drops everything but digits, for
    fragment-buffering), this is safe to run over a whole sentence: it only
    turns "account number is three zero zero..." into "account number is
    3 0 0...", so existing contiguous-digit-run regexes (CallMemory) can find
    a full number even when the caller spoke it as words in one breath.
    """
    def _sub(m: re.Match) -> str:
        tok = m.group(0)
        if tok.isdigit():
            return tok
        return _WORD_TO_DIGIT.get(tok.lower(), tok)
    return _TOKEN_RE.sub(_sub, text)


def spoken_to_digits(text: str) -> str:
    """Extract a digit string from spoken/mixed text — the way a human hears a
    number read aloud.

    Handles, in any mix of English/Hindi/Marathi (native or romanized):
      • individual digits ....... "nine eight seven"       → "987"
      • literal digits .......... "9 8 7"                  → "987"
      • paired tens ............. "ninety eight seventy six"→ "9876"
      • standalone tens ......... "twenty"                 → "20"
      • teens ................... "nineteen"               → "19"
      • repeats ................. "double three", "triple five" → "33", "555"
      • zero variants ........... "oh", "o", "nought"      → "0"

    Non-number words are ignored (they break the run conceptually), so only feed
    utterances already suspected to be a number fragment (see
    `looks_like_number_fragment`), not free-form sentences.
    """
    out: list[str] = []
    tokens = [t for t in _TOKEN_RE.findall(text)]
    i = 0
    pending_mult = 0                       # set by "double"/"triple" for next unit
    while i < len(tokens):
        tok = tokens[i]
        low = tok.lower()
        if tok.isdigit():
            out.append(tok * pending_mult if pending_mult else tok)
            pending_mult = 0
            i += 1
            continue
        if low in _MULT:
            pending_mult = _MULT[low]
            i += 1
            continue
        if low in _TEEN:                    # "nineteen" → "19"
            out.append(_TEEN[low])
            pending_mult = 0
            i += 1
            continue
        if low in _TENS:                    # "ninety [eight]" → "98" or "90"
            tens = _TENS[low]
            nxt = tokens[i + 1].lower() if i + 1 < len(tokens) else ""
            unit = _EN.get(nxt) or _WORD_TO_DIGIT.get(nxt)
            if unit is not None and unit != "0" and nxt not in _TENS and nxt not in _TEEN:
                out.append(str(tens + int(unit)))
                i += 2
            else:
                out.append(str(tens))
                i += 1
            pending_mult = 0
            continue
        d = _WORD_TO_DIGIT.get(low)
        if d is not None:                   # single digit word (any language)
            out.append(d * pending_mult if pending_mult else d)
            pending_mult = 0
            i += 1
            continue
        # unknown word (filler / noise) — skip, keep the run going
        i += 1
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
    """Per-slot accumulator for one in-progress number collection — the
    conversation state of NUMBER CAPTURE MODE.

    Lives on CallMemory (one instance, re-targeted per active field) so it
    persists across the multiple STT utterances a single spoken number spans.
    Tracks not just the digits but confidence, confirmation, and a full
    correction history so the agent can behave like a human executive.
    """
    field: str | None = None          # identifier type (see NUMBER_TYPES)
    digits: str = ""
    turns_active: int = 0
    confidence: float = 1.0           # min STT confidence seen while capturing
    confirmed: bool = False           # caller has confirmed the read-back
    corrections: list[str] = _dc_field(default_factory=list)  # audit of edits
    last_updated: float = 0.0         # monotonic-ish timestamp of last change
    # DUAL INPUT: how the digits captured so far arrived. "speech" (spoken),
    # "dtmf" (keypad), or "hybrid" (both used in one capture). Drives the UI
    # badge and lets the agent acknowledge a mode switch naturally.
    input_mode: str = "speech"
    # CONFIDENCE-BASED RECOVERY: one entry per accepted fragment —
    # (start_index, end_index, confidence). Lets the agent re-ask ONLY the
    # uncertain span ("I'm unsure of the last three") instead of the whole
    # number. DTMF fragments are recorded at confidence 1.0 (a keypress is
    # unambiguous), so they are never re-queried.
    segments: list[tuple[int, int, float]] = _dc_field(default_factory=list)

    @property
    def active(self) -> bool:
        return self.field is not None

    @property
    def type(self) -> "NumberType | None":
        return number_type(self.field)

    @property
    def expected_len(self) -> int | None:
        return EXPECTED_LENGTHS.get(self.field) if self.field else None

    def _touch(self) -> None:
        import time as _t
        self.last_updated = _t.time()

    def start(self, field: str) -> None:
        self.field = field
        self.digits = ""
        self.turns_active = 0
        self.confidence = 1.0
        self.confirmed = False
        self.corrections = []
        self.input_mode = "speech"
        self.segments = []
        self._touch()

    def _note_mode(self, mode: str) -> None:
        """Track speech vs keypad. First real input sets the mode; any later
        input in the OTHER mode promotes the whole capture to 'hybrid'."""
        if not self.digits:
            self.input_mode = mode
        elif mode != self.input_mode and self.input_mode != "hybrid":
            self.input_mode = "hybrid"

    def _record_segment(self, start: int, end: int, confidence: float) -> None:
        if end > start:
            self.segments.append((start, end, float(confidence)))

    def prefix_valid(self) -> bool:
        t = self.type
        return t.prefix_ok(self.digits) if t else True

    def uncertain_tail(self, threshold: float = 0.55) -> tuple[int, int] | None:
        """Return (start, end) of the trailing run of low-confidence digits, so
        the agent can re-ask ONLY that span. None when everything is confident."""
        end = len(self.digits)
        start = end
        for s, e, conf in reversed(self.segments):
            if conf < threshold:
                start = min(start, s)
            else:
                if start < end:
                    break
        return (start, end) if start < end else None

    def feed(self, text: str, confidence: float = 1.0) -> tuple[str, bool]:
        """Add a fragment. Returns (accumulated_digits, is_complete).

        Over-long input (more digits than expected) is truncated to the
        expected length rather than silently accepted — a caller who keeps
        talking past the target length almost always means the number is
        already complete and what follows is unrelated speech.
        """
        self.turns_active += 1
        self.confidence = min(self.confidence, float(confidence))
        self._note_mode("speech")
        prev = self.digits
        # Positional placement: "first four are ...", "last two are ...". Strip
        # the position phrase first so its own number word ("four", "two") is not
        # mistaken for a captured digit.
        pos = parse_position(text)
        digit_text = _POSITION_RE.sub(" ", text) if pos else text
        incoming = spoken_to_digits(digit_text)
        exp = self.expected_len
        if pos and incoming:
            self._place(pos[0], pos[1], incoming)
        # A fragment that is ALREADY the full expected length is almost always
        # the caller REPEATING the whole number (after a re-prompt), not a
        # continuation — replacing avoids gluing it onto a stale partial and
        # assembling a corrupted number (seen in production: 11 stale digits +
        # a full 12-digit repeat -> wrong account number).
        elif exp is not None and len(incoming) >= exp:
            self.digits = incoming[:exp]
        else:
            self.digits += incoming
        if exp is not None and len(self.digits) > exp:
            self.digits = self.digits[:exp]
        # Confidence bookkeeping for targeted recovery. A clean tail-append maps
        # exactly to (len(prev), len(now)); a replace/reposition can't be tracked
        # per-digit, so collapse to a single whole-buffer segment at this conf.
        if self.digits.startswith(prev) and len(self.digits) > len(prev):
            self._record_segment(len(prev), len(self.digits), confidence)
        elif self.digits != prev:
            self.segments = [(0, len(self.digits), confidence)] if self.digits else []
        self._touch()
        complete = exp is not None and len(self.digits) == exp
        return self.digits, complete

    def feed_dtmf(self, key: str) -> tuple[str, bool]:
        """Append ONE keypad digit (0-9). Control keys (#, *) are handled by the
        DTMF controller, never here. A keypress is unambiguous, so it is recorded
        at full confidence and never surfaces in uncertain_tail()."""
        if not key or key not in "0123456789":
            return self.digits, False
        self.turns_active += 1
        self._note_mode("dtmf")
        exp = self.expected_len
        if exp is None or len(self.digits) < exp:
            start = len(self.digits)
            self.digits += key
            self._record_segment(start, len(self.digits), 1.0)
        self._touch()
        complete = exp is not None and len(self.digits) == exp
        return self.digits, complete

    def backspace(self) -> str:
        """Delete the last digit (keypad '*' or 'backspace'). Trims the trailing
        confidence segment so recovery stays accurate."""
        if self.digits:
            self.digits = self.digits[:-1]
            self.corrections.append("backspace")
            n = len(self.digits)
            self.segments = [(s, min(e, n), c) for (s, e, c) in self.segments if s < n]
            self._touch()
        return self.digits

    def _place(self, which: str, count: int, incoming: str) -> None:
        """Apply a positional fragment ('first four', 'last two', 'next four').

        'last N' is context-sensitive: while still BUILDING the number in order
        (buffer not yet full) it APPENDS the final N digits; once the buffer is
        already full it REPLACES the trailing N (a correction)."""
        frag = incoming if which == "next" else incoming[:count]
        exp = self.expected_len
        if which == "first":
            self.digits = frag + self.digits[len(frag):]
        elif which == "last":
            if exp and len(self.digits) + len(frag) <= exp:
                self.digits += frag                      # building in order
            else:
                keep = self.digits[:-count] if len(self.digits) >= count else ""
                self.digits = keep + frag                # correcting the tail
        else:                                            # "next" — append
            self.digits += frag
        self.corrections.append(f"{which} {count}: {frag}")

    def remove_last(self, n: int = 1) -> str:
        self.digits = self.digits[:-n] if n < len(self.digits) else ""
        self.corrections.append(f"removed last {n}")
        self._touch()
        return self.digits

    def finalize(self) -> tuple[str, bool]:
        """Called on a pause / 'that's all' for VARIABLE-length identifiers.
        Returns (digits, valid) — valid if the current length is acceptable."""
        t = self.type
        return self.digits, bool(t and t.valid(self.digits))

    def snapshot(self) -> dict:
        """State for the frontend live-capture display (never blocks)."""
        t = self.type
        exp = self.expected_len
        return {
            "field": self.field,
            "label": (t.label if t else self.field) or "",
            "digits": self.digits,
            "count": len(self.digits),
            "expected": exp,
            "masked": mask_digits(self.digits, exp),
            "grouped": group_for_readback(self.digits, self.field),
            "confidence": round(self.confidence, 2),
            "confirmed": self.confirmed,
            "input_mode": self.input_mode,
            "prefix_ok": self.prefix_valid(),
            "valid": bool(t and t.valid(self.digits)) if t else bool(self.digits),
        }

    def correct_last(self, text: str) -> str:
        """Apply a single-digit correction to the tail of the buffer.

        Extracts the digit(s) mentioned in the correction utterance and
        replaces the same number of trailing digits, rather than discarding
        and re-collecting the whole number.
        """
        before = self.digits
        new_tail = spoken_to_digits(text)
        if not new_tail:
            return self.digits
        n = len(new_tail)
        if n >= len(self.digits):
            self.digits = new_tail[-len(self.digits):] if self.digits else new_tail
        else:
            self.digits = self.digits[:-n] + new_tail
        self.corrections.append(f"{before} → {self.digits}")
        self._touch()
        return self.digits

    def clear(self) -> None:
        self.field = None
        self.digits = ""
        self.turns_active = 0
        self.segments = []
        self.input_mode = "speech"


def is_valid_length(field: str, digits: str) -> bool:
    """Reject impossible numbers before they ever reach a tool call."""
    t = NUMBER_TYPES.get(field)
    if t is not None:
        return t.valid(digits)
    return bool(digits)
