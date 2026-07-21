"""DTMF (keypad) capture controller — the keypad half of DUAL-INPUT number
capture. Speech capture lives in numbers.py (NumberBuffer.feed); this module
turns raw Exotel keypad events into the same buffer, with IVR-grade control keys.

Design
------
The controller is a thin, PURE state machine over a NumberBuffer (no I/O, no
telephony): the WS layer feeds it one key at a time and gets back a DTMFResult
describing what happened, so it can update the UI, speak an acknowledgement, or
run validation/confirmation. Timeouts are driven by the WS layer calling
`idle_ms` against `last_key_ts` — nothing here blocks.

Key map (bankers'-IVR conventions, all configurable):
    0-9   → append a digit
    *     → BACKSPACE the last digit (press on an empty buffer = CANCEL capture)
    **    → RESTART (two backspaces on an empty buffer clears everything)
    #     → SUBMIT / done (finalise variable-length, or confirm exact-length)

Everything a customer can do with their voice ("remove the last digit", "start
again", "that's all") therefore has a keypad equivalent, and the two can be
mixed freely inside a single capture (hybrid mode).
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from .numbers import NumberBuffer, number_type

# Control keys (defaults; overridable from Settings so a deployment can remap).
SUBMIT_KEY = "#"
BACKSPACE_KEY = "*"


@dataclass
class DTMFResult:
    """Outcome of one keypress, for the WS/UI layer."""
    action: str            # digit | backspace | cancel | restart | submit | ignored
    digits: str            # buffer contents after the key
    complete: bool = False  # reached the exact expected length
    submitted: bool = False  # caller pressed SUBMIT (#)
    valid: bool = False     # digits currently pass length + prefix validation
    message: str = ""       # short human note (logging / UI)


class DTMFController:
    """One per call. Re-targets whichever NumberBuffer the memory hands it."""

    def __init__(self, buffer: NumberBuffer,
                 submit_key: str = SUBMIT_KEY,
                 backspace_key: str = BACKSPACE_KEY):
        self.buf = buffer
        self.submit_key = submit_key
        self.backspace_key = backspace_key
        self.last_key_ts: float = 0.0
        self._empty_backspaces = 0     # consecutive '*' on an empty buffer

    # ── timing (WS layer polls this; we never sleep) ─────────────────────────
    def idle_ms(self, now: float | None = None) -> float:
        if not self.last_key_ts:
            return 0.0
        return ((now or time.time()) - self.last_key_ts) * 1000.0

    def _valid(self) -> bool:
        t = number_type(self.buf.field)
        return bool(t and t.valid(self.buf.digits)) if t else bool(self.buf.digits)

    # ── main entry: one keypress ─────────────────────────────────────────────
    def press(self, key: str) -> DTMFResult:
        key = (key or "").strip()
        self.last_key_ts = time.time()

        if key == self.backspace_key:
            if self.buf.digits:
                self._empty_backspaces = 0
                digits = self.buf.backspace()
                return DTMFResult("backspace", digits, valid=self._valid(),
                                  message="deleted last digit")
            # '*' on an empty buffer: first press = cancel, second = restart.
            self._empty_backspaces += 1
            if self._empty_backspaces >= 2:
                field = self.buf.field
                self.buf.clear()
                if field:
                    self.buf.start(field)
                return DTMFResult("restart", "", message="capture restarted")
            return DTMFResult("cancel", "", message="nothing to delete — cancel?")

        self._empty_backspaces = 0

        if key == self.submit_key:
            digits = self.buf.digits
            complete = self.buf.type.is_complete(digits) if self.buf.type else bool(digits)
            return DTMFResult("submit", digits, complete=complete, submitted=True,
                              valid=self._valid(), message="submit pressed")

        if key in "0123456789":
            digits, complete = self.buf.feed_dtmf(key)
            return DTMFResult("digit", digits, complete=complete, valid=self._valid(),
                              message=f"digit {key}")

        # Unknown key (letters, empty) — ignore, never corrupt the buffer.
        return DTMFResult("ignored", self.buf.digits, valid=self._valid(),
                          message=f"ignored key {key!r}")
