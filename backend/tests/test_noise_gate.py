"""Utterance-level noise gate — offline, no model needed.

The endpointer drops an utterance as background noise unless at least one frame
reached `speech_confirm_peak_prob`. A scripted fake VAD lets us feed exact
per-frame probabilities and assert the gate keeps real speech and rejects noise.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend.app.audio.endpointing import Endpointer, EventType
from backend.app.audio.vad import FRAME
from backend.app.config import get_settings


class ScriptedVAD:
    """Returns a preset probability per frame; behaves like the real VAD interface."""

    def __init__(self, probs, threshold=0.4):
        self._probs = probs
        self._i = 0
        self.threshold = threshold
        self.last_prob = 0.0

    @property
    def has_model(self) -> bool:
        return True

    def is_speech(self, frame, sample_rate=16000) -> bool:
        p = self._probs[min(self._i, len(self._probs) - 1)]
        self._i += 1
        self.last_prob = p
        return p >= self.threshold

    def reset(self) -> None:
        pass


def _frames(probs):
    """One FRAME of silence-shaped bytes per prob (content is ignored by ScriptedVAD)."""
    import numpy as np
    pcm = (np.zeros(FRAME * len(probs), dtype=np.int16)).tobytes()
    return pcm


def _run(probs):
    s = get_settings()
    s.__dict__["speech_confirm_peak_prob"] = 0.7
    s.__dict__["vad_end_silence_ms"] = 300      # flush quickly in-test
    s.__dict__["vad_min_speech_ms"] = 150
    ep = Endpointer(s, ScriptedVAD(probs))
    return ep.feed(_frames(probs))


def test_real_speech_passes_the_gate():
    # ~320 ms of confident speech (0.9), then silence to flush.
    probs = [0.9] * 10 + [0.0] * 15
    events = _run(probs)
    assert any(e.type is EventType.UTTERANCE for e in events)


def test_low_confidence_noise_is_dropped():
    # Frames cross the 0.4 detection threshold but never reach 0.7 — classic
    # fan / traffic murmur. Enough duration to flush, but must be rejected.
    probs = [0.5] * 12 + [0.0] * 15
    events = _run(probs)
    assert not any(e.type is EventType.UTTERANCE for e in events)


def test_energy_fallback_skips_gate():
    """Without a real model (has_model False), the probability gate is skipped so
    the energy-VAD fallback still forwards utterances."""
    s = get_settings()
    s.__dict__["speech_confirm_peak_prob"] = 0.7
    s.__dict__["vad_end_silence_ms"] = 300
    s.__dict__["vad_min_speech_ms"] = 150

    class NoModelVAD(ScriptedVAD):
        @property
        def has_model(self) -> bool:
            return False

    probs = [0.5] * 12 + [0.0] * 15
    ep = Endpointer(s, NoModelVAD(probs))
    events = ep.feed(_frames(probs))
    assert any(e.type is EventType.UTTERANCE for e in events)
