"""Output loudness leveler — offline, deterministic tests.

Proves the properties that make the agent's voice sound human-steady rather
than drifting up and down between sentences:

  • loud & quiet sentences converge to a consistent output level
  • the FIRST sentence is passed through untouched (it sets the reference)
  • the gain is CONSTANT within a sentence (no intra-sentence pumping)
  • silence is never boosted
  • output never clips, and byte length is preserved (playhead/AEC framing)
  • correction is clamped to the configured window (gentle, not flattening)
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.audio.output_loudness import OutputLoudness

_SR = 24000


def _sentence(rms: float, ms: int = 2000, seed: int = 0) -> bytes:
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(int(_SR * ms / 1000)).astype(np.float32)
    x *= rms / (np.sqrt(np.mean(x ** 2)) + 1e-9)
    return (np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes()


def _rms(b: bytes) -> float:
    x = np.frombuffer(b, dtype=np.int16).astype(np.float64) / 32767
    return float(np.sqrt(np.mean(x ** 2))) if x.size else 0.0


def _stream(lv: OutputLoudness, raw: bytes, chunk: int = 9600) -> bytes:
    lv.start_sentence()
    return b"".join(lv.process(raw[i:i + chunk]) for i in range(0, len(raw), chunk))


def test_varying_sentences_converge_to_steady_level():
    lv = OutputLoudness()
    levels = [0.10, 0.22, 0.05, 0.18, 0.06, 0.20]
    outs = [_rms(_stream(lv, _sentence(l, seed=i))) for i, l in enumerate(levels)]
    # input spans a wide loudness range; output must be tightly clustered
    assert (max(levels) - min(levels)) > 0.15
    assert (max(outs) - min(outs)) < 0.03           # steady volume


def test_first_sentence_is_unchanged():
    lv = OutputLoudness()
    raw = _sentence(0.13, seed=1)
    out = _stream(lv, raw)
    assert out == raw                               # reference sentence untouched
    assert abs(lv.current_gain_db) < 1e-6           # gain exactly 1.0


def test_gain_constant_within_a_sentence():
    """No intra-sentence pumping: every chunk of one sentence gets the same gain."""
    lv = OutputLoudness()
    _stream(lv, _sentence(0.12, seed=0))            # establish reference
    lv.start_sentence()
    raw = _sentence(0.24, seed=2)                   # a loud sentence to correct
    chunk = 9600
    ratios = []
    for i in range(0, len(raw), chunk):
        cin = raw[i:i + chunk]
        cout = lv.process(cin)
        ri, ro = _rms(cin), _rms(cout)
        if ri > 0:
            ratios.append(ro / ri)
    assert max(ratios) - min(ratios) < 0.02         # one gain for the whole sentence
    assert ratios[0] < 0.99                          # and it actually cut the loud one


def test_silence_is_not_boosted():
    lv = OutputLoudness()
    _stream(lv, _sentence(0.12, seed=0))            # reference ~0.12
    lv.start_sentence()
    silence = (np.zeros(_SR, dtype=np.int16)).tobytes()
    out = lv.process(silence)
    assert _rms(out) < 1e-3                          # stays silent, no huge gain-up


def test_correction_is_clamped_gentle():
    lv = OutputLoudness(min_gain=0.5, max_gain=2.0)
    _stream(lv, _sentence(0.12, seed=0))            # reference
    # An extremely quiet sentence must be boosted by at most max_gain (not ∞)
    lv.start_sentence()
    lv.process(_sentence(0.001, seed=3)[:9600])
    assert lv.current_gain_db <= 20 * np.log10(2.0) + 1e-6


def test_output_never_clips_and_preserves_length():
    lv = OutputLoudness()
    _stream(lv, _sentence(0.10, seed=0))
    lv.start_sentence()
    raw = _sentence(0.02, seed=4)                    # quiet → gets boosted
    out = b"".join(lv.process(raw[i:i + 9600]) for i in range(0, len(raw), 9600))
    assert len(out) == len(raw)                      # framing preserved
    x = np.frombuffer(out, dtype=np.int16)
    assert x.max() < 32767 and x.min() > -32768      # no hard clipping


def test_odd_length_and_empty_pass_through():
    lv = OutputLoudness()
    assert lv.process(b"") == b""
    assert lv.process(b"\x01\x02\x03") == b"\x01\x02\x03"   # odd bytes untouched


def test_disabled_via_reset_behaviour_is_stable():
    """reset() forgets the running level so a fresh call starts clean."""
    lv = OutputLoudness()
    _stream(lv, _sentence(0.20, seed=0))
    lv.reset()
    raw = _sentence(0.20, seed=0)
    assert _stream(lv, raw) == raw                   # first sentence again unchanged
