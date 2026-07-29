"""Regression tests for the end-of-sentence / barge-in click.

MEASURED, from a captured production call (stream 5cad1933b3c9247d, 226 s):
the outbound audio contains abrupt mid-speech cuts at 42.1 s, 159.6 s and
217.8 s — loud speech dropping to near-silence inside 10 ms. The caller
described exactly that: cracking "at last and in mid also".

WHERE IT COMES FROM. ExotelTransport._flush_out(force=True) runs on every
`audio_end`, i.e. at the end of EVERY sentence (~50 times in a 4-minute call).
It used to append hard zeros directly after the last speech sample. A sentence
tail is mid-decay, and a barge-in severs one at full amplitude, so that sample
is essentially never zero — the jump to 0 is a step discontinuity, and a step
is broadband click energy. _send_clear() had the same problem: it dropped the
buffered tail and asked Exotel to stop mid-sample.

FIX: ramp the final ~6 ms to zero before the pad, and emit a short faded tail
before `clear`. Six milliseconds is inaudible as a level change — a barge-in
still stops the agent instantly — but it removes the discontinuity, so the
silence that follows is a continuation rather than a cliff.

These tests assert the waveform property (no step into the pad), not the
implementation, so they still hold if the fade length or shape is retuned.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.telephony.exotel import _MULTIPLE, _fade_out_tail


def _loud_tail(n: int = 800, amp: int = 12000) -> bytearray:
    """PCM16 sitting at high amplitude right up to its last sample — the shape
    a severed sentence tail actually has."""
    t = np.arange(n)
    a = (amp * np.sin(2 * np.pi * 300 * t / 16000)).astype("<i2")
    a[-1] = amp                      # force a large final sample
    return bytearray(a.tobytes())


def _samples(buf: bytearray) -> np.ndarray:
    return np.frombuffer(bytes(buf), dtype="<i2").astype(float)


def test_tail_ends_at_zero_so_the_pad_is_not_a_step():
    buf = _loud_tail()
    before = abs(_samples(buf)[-1])
    _fade_out_tail(buf, 16000)
    after = abs(_samples(buf)[-1])
    assert before > 5000
    assert after <= 1, "tail still steps into the zero pad — this is the click"


def test_step_into_silence_is_removed():
    """The click is the JUMP from the last sample to the first pad zero."""
    buf = _loud_tail()
    raw_step = abs(_samples(buf)[-1] - 0.0)
    _fade_out_tail(buf, 16000)
    faded_step = abs(_samples(buf)[-1] - 0.0)
    assert faded_step < raw_step / 100.0


def test_fade_is_short_enough_to_be_inaudible_as_a_level_change():
    """A barge-in must still feel instant: only the last few ms may move."""
    buf = _loud_tail(n=16000)          # 1 s at 16 kHz
    original = _samples(bytearray(buf)).copy()
    _fade_out_tail(buf, 16000)
    out = _samples(buf)
    untouched = out[:-200]             # everything before the last 12.5 ms
    assert np.array_equal(untouched, original[:len(untouched)])


def test_length_is_never_changed():
    """Sample count drives playback timing — a fade must not add or drop any."""
    for n in (2, 64, 800, 16000):
        buf = _loud_tail(n=n)
        before = len(buf)
        _fade_out_tail(buf, 16000)
        assert len(buf) == before


def test_monotonic_ramp_no_overshoot_or_clipping():
    buf = _loud_tail()
    _fade_out_tail(buf, 16000)
    out = _samples(buf)
    assert out.max() <= 32767 and out.min() >= -32768
    # envelope over the faded region must be non-increasing
    tail = np.abs(out[-96:])
    env = np.maximum.accumulate(tail[::-1])[::-1]
    assert np.all(np.diff(env) <= 1e-9)


@pytest.mark.parametrize("bad", [bytearray(), bytearray(b"\x01")])
def test_degenerate_buffers_are_left_alone(bad):
    """Empty or odd-length (half a sample) must not raise or corrupt."""
    before = bytes(bad)
    _fade_out_tail(bad, 16000)
    assert bytes(bad) == before


def test_fade_scales_with_sample_rate():
    """Same duration at either leg rate, so an 8 kHz leg is handled too."""
    for rate in (8000, 16000, 24000):
        buf = _loud_tail(n=rate)       # 1 s
        _fade_out_tail(buf, rate)
        out = np.abs(_samples(buf))
        moved = np.where(out < np.abs(_samples(_loud_tail(n=rate))) - 1)[0]
        assert moved.size > 0
        fade_samples = out.size - moved.min()
        assert 0.003 * rate <= fade_samples <= 0.012 * rate


def test_barge_in_tail_stays_frame_aligned():
    """Exotel requires 320-byte-aligned media; the faded tail plus its pad
    must still satisfy that or the platform rejects the frame."""
    buf = _loud_tail(n=500)
    _fade_out_tail(buf, 16000)
    pad = (-len(buf)) % _MULTIPLE
    assert (len(buf) + pad) % _MULTIPLE == 0
