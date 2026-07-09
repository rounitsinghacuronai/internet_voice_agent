"""Automatic Gain Control — normalises microphone input level.

Why this matters for the MSEDCL agent:
  • Callers hold phones at wildly different distances from their mouths.
  • Some environments are acoustically noisy and the user compensates by
    speaking quietly (phone pressed against ear) or loudly (far away).
  • Without AGC the VAD threshold either clips loud frames or misses soft
    speech — both produce false barge-ins or missed utterances.

Design
------
  • Per-utterance (block) mode: the primary path.  Applied once per full
    utterance before noise-gating and STT, so the whole spectral chain
    sees normalised amplitudes.
  • Per-frame mode: a lighter variant used inside the endpointing loop so
    the VAD energy gate sees a consistent level.
  • Separate attack (fast) and release (slow) time constants prevent the
    classic "pumping" artefact: gain drops quickly when a loud burst
    arrives, then rises slowly as the signal quietens.
  • Hard clamp to [-1, 1] to avoid downstream overflows.
"""
from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger(__name__)

_EPS = 1e-9


class AGC:
    """Automatic Gain Control with smoothed attack/release.

    Args:
        target_rms   Target RMS level after gain (linear, 0-1).  Default 0.08
                     ≈ –22 dBFS — loud enough for VAD/STT, well below clip.
        attack_ms    How fast (ms) gain DECREASES when signal is too loud.
                     Fast (10 ms) avoids clipping loud transients.
        release_ms   How fast (ms) gain INCREASES when signal is quiet.
                     Slow (300 ms) avoids pumping artefacts.
        max_gain     Upper gain limit (linear, default 12× ≈ +22 dB).
        min_gain     Lower gain limit (linear, default 0.05 ≈ –26 dB).
        sample_rate  Input sample rate in Hz.
    """

    def __init__(
        self,
        target_rms: float = 0.08,
        attack_ms: float = 10.0,
        release_ms: float = 300.0,
        max_gain: float = 12.0,
        min_gain: float = 0.05,
        sample_rate: int = 16000,
    ) -> None:
        self.target_rms = target_rms
        self.max_gain = max_gain
        self.min_gain = min_gain
        self._gain: float = 1.0

        # Time-constant → per-sample smoothing coefficient
        # τ = -dt / ln(1 - α)  ⟹  α = 1 - exp(-1/τ_samples)
        self._attack = 1.0 - np.exp(-1.0 / (attack_ms * sample_rate / 1000.0))
        self._release = 1.0 - np.exp(-1.0 / (release_ms * sample_rate / 1000.0))

    # ── block (utterance-level) processing ───────────────────────────────────

    def process(self, audio: np.ndarray) -> np.ndarray:
        """Normalise a complete utterance to target_rms.

        Preferred path: called once per utterance before STT so the noise
        gate and denoiser see consistent amplitude.
        """
        rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2))) + _EPS
        desired = np.clip(self.target_rms / rms, self.min_gain, self.max_gain)
        # Smooth toward the new gain
        coeff = self._attack if desired < self._gain else self._release
        self._gain += coeff * (desired - self._gain)
        out = np.clip(audio * self._gain, -1.0, 1.0)
        return out.astype(np.float32)

    # ── frame (VAD-loop) processing ──────────────────────────────────────────

    def process_frame(self, frame: np.ndarray) -> np.ndarray:
        """Lightweight per-32 ms frame normalisation for VAD consistency.

        Uses the same smoothed gain state as process(), so both paths share
        one gain estimate.
        """
        rms = float(np.sqrt(np.mean(frame.astype(np.float64) ** 2))) + _EPS
        desired = np.clip(self.target_rms / rms, self.min_gain, self.max_gain)
        coeff = self._attack if desired < self._gain else self._release
        self._gain += coeff * (desired - self._gain)
        return np.clip(frame * self._gain, -1.0, 1.0).astype(np.float32)

    # ── diagnostics ───────────────────────────────────────────────────────────

    @property
    def current_gain_db(self) -> float:
        return 20.0 * np.log10(self._gain + _EPS)

    def reset(self) -> None:
        self._gain = 1.0
