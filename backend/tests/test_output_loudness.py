"""Output loudness leveler — continuous within-sentence smoothing.

Proves the properties that make the agent's voice sound human-steady rather than
swelling and dipping through a long sentence: it reduces intra-utterance level
swings, holds gain through pauses (no pumping into silence), never clips, and
only nudges (gentle ±6 dB clamp).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.audio.output_loudness import OutputLoudness

_SR = 24000


def _tone(rms: float, ms: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(int(_SR * ms / 1000)).astype(np.float32)
    x *= rms / (np.sqrt(np.mean(x ** 2)) + 1e-9)
    return np.clip(x, -1, 1)


def _to_bytes(x: np.ndarray) -> bytes:
    return (x * 32767).astype(np.int16).tobytes()


def _rms(b: bytes) -> float:
    x = np.frombuffer(b, dtype=np.int16).astype(np.float64) / 32767
    return float(np.sqrt(np.mean(x ** 2))) if x.size else 0.0


def _stream(lv: OutputLoudness, x: np.ndarray, chunk_ms: int = 200) -> bytes:
    step = int(_SR * chunk_ms / 1000)
    raw = _to_bytes(x)
    step_bytes = step * 2
    return b"".join(lv.process(raw[i:i + step_bytes]) for i in range(0, len(raw), step_bytes))


def test_reduces_intra_utterance_swings():
    """A sentence that is loud, then quiet, then loud again should come out much
    flatter than it went in."""
    lv = OutputLoudness()
    # establish the running level first (~1.5s at a nominal level)
    _stream(lv, _tone(0.12, 1500, 0))
    loud1 = _tone(0.22, 900, 1)
    quiet = _tone(0.05, 900, 2)
    loud2 = _tone(0.22, 900, 3)
    out = _stream(lv, np.concatenate([loud1, quiet, loud2]))
    x = np.frombuffer(out, dtype=np.int16).astype(np.float64) / 32767
    n = len(x) // 3
    r_loud = np.sqrt(np.mean(x[:n] ** 2))
    r_quiet = np.sqrt(np.mean(x[n:2 * n] ** 2))
    in_ratio = 0.22 / 0.05                      # 4.4x swing in
    out_ratio = r_loud / (r_quiet + 1e-9)       # swing out
    assert out_ratio < in_ratio * 0.6           # swing materially reduced


def test_silence_is_held_not_boosted():
    lv = OutputLoudness()
    _stream(lv, _tone(0.12, 1000, 0))
    silence = np.zeros(_SR, dtype=np.float32)   # 1s of digital silence
    out = _stream(lv, silence)
    assert _rms(out) < 1e-3                      # stays silent — never gained up


def test_gain_clamped_gentle():
    lv = OutputLoudness(min_gain=0.5, max_gain=2.0)
    _stream(lv, _tone(0.12, 1500, 0))
    _stream(lv, _tone(0.001, 800, 3))           # extremely quiet passage
    assert lv.current_gain_db <= 20 * np.log10(2.0) + 1e-6   # never above +6 dB


def test_output_never_clips_and_preserves_length():
    lv = OutputLoudness()
    _stream(lv, _tone(0.10, 1000, 0))
    raw = _to_bytes(_tone(0.03, 1000, 4))       # quiet → boosted
    out = b"".join(lv.process(raw[i:i + 9600]) for i in range(0, len(raw), 9600))
    assert len(out) == len(raw)
    x = np.frombuffer(out, dtype=np.int16)
    assert x.max() < 32767 and x.min() > -32768


def test_odd_length_and_empty_pass_through():
    lv = OutputLoudness()
    assert lv.process(b"") == b""
    assert lv.process(b"\x01\x02\x03") == b"\x01\x02\x03"


def test_reset_clears_state():
    lv = OutputLoudness()
    _stream(lv, _tone(0.2, 800, 0))
    lv.reset()
    assert lv._avg is None and lv._gain == 1.0


def test_steady_output_across_varying_chunks():
    """Feed a long stream whose level wanders; the tail should sit near the
    running average, not track the wander."""
    lv = OutputLoudness()
    levels = [0.10, 0.20, 0.06, 0.18, 0.08, 0.16]
    outs = []
    for i, l in enumerate(levels):
        outs.append(_rms(_stream(lv, _tone(l, 900, i))))
    # the leveler is gentle (no pumping), so it REDUCES the spread rather than
    # flattening it perfectly — output spread should be well under the input's.
    in_spread = max(levels) - min(levels)          # 0.14
    out_spread = max(outs[1:]) - min(outs[1:])     # skip the reference-setting first
    assert out_spread < in_spread * 0.7
