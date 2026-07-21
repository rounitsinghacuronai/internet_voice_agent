"""Output loudness leveling — keeps the AGENT'S OWN voice at a steady volume.

The problem this solves
-----------------------
Sarvam Bulbul (like every neural TTS) varies loudness both BETWEEN sentences and
WITHIN a long one — the level swells at the start of a phrase, drops at a comma
pause, then rises again. Streamed to the caller untouched, the agent's volume
audibly wanders, which is one of the clearest "this is a bot" tells.

The AGC in ``agc.py`` fixes the INPUT (the caller's mic). This is its mirror on
the OUTPUT side, tuned for transparency:

  • CONTINUOUS, not per-sentence. It tracks a smoothed loudness envelope and
    gently pulls it toward the call's own running-average level, adapting THROUGH
    a long sentence — so the within-sentence swells and dips are evened out, not
    just the sentence-to-sentence steps.
  • SLOW time constants (attack ~120 ms, release ~350 ms) so it follows
    phrase-level changes but is far too slow to chase individual syllables —
    which is what would cause audible pumping/breathing (itself an AI tell).
  • HOLDS gain through silence/pauses (never boosts a pause), so a comma or a
    breath can't make the next word jump in volume.
  • Reference-free: the target is the call's own average voice level, so overall
    volume stays exactly where Sarvam naturally sits — only the wander is removed.
  • Per-sample gain ramping (no clicks) + a soft peak limiter (no clipping).

The same leveled PCM feeds the caller AND the AEC reference, so echo cancellation
stays in sync and benefits from the steadier level.
"""
from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger(__name__)

_EPS = 1e-9
_INT16_MAX = 32767.0


def _coeff(window_ms: float, tau_ms: float) -> float:
    """One-pole smoothing coefficient for a time constant, given the window."""
    return 1.0 - float(np.exp(-window_ms / max(1.0, tau_ms)))


class OutputLoudness:
    """Continuous, gentle loudness leveler for streamed TTS PCM16.

    Usage (per call; state persists across sentences for a steady level)::

        for pcm in tts.synthesize(...):
            pcm = leveler.process(pcm)     # bytes in, bytes out (same length)
    """

    def __init__(
        self,
        max_gain: float = 2.0,
        min_gain: float = 0.5,
        silence_rms: float = 0.005,
        limiter_ceiling: float = 0.98,
        sample_rate: int = 24000,
        attack_ms: float = 120.0,
        release_ms: float = 350.0,
        avg_ms: float = 2500.0,
        window_ms: float = 10.0,
        # accepted for backward-compat with the old per-sentence constructor:
        avg_alpha: float | None = None,
    ) -> None:
        self.max_gain = float(max_gain)
        self.min_gain = float(min_gain)
        self.silence_rms = float(silence_rms)
        self.limiter_ceiling = float(limiter_ceiling)
        self.sample_rate = int(sample_rate)
        self._win = max(64, int(self.sample_rate * window_ms / 1000.0))
        self._att = _coeff(window_ms, attack_ms)      # gain down (loud) — faster
        self._rel = _coeff(window_ms, release_ms)     # gain up (quiet) — slower
        self._env_c = _coeff(window_ms, 30.0)         # 30 ms RMS envelope detector
        self._avg_c = _coeff(window_ms, avg_ms)       # slow running-average target
        self._env: float | None = None                # smoothed loudness envelope
        self._avg: float | None = None                # running-average target level
        self._gain: float = 1.0                        # current smoothed gain
        self._voiced = False                           # seen real speech yet

    # ── lifecycle ─────────────────────────────────────────────────────────────
    def start_sentence(self) -> None:
        """No-op kept for API compatibility — leveling is continuous across the
        whole call (that's what keeps the level steady between sentences)."""

    def reset(self) -> None:
        self._env = None
        self._avg = None
        self._gain = 1.0
        self._voiced = False

    # ── processing ─────────────────────────────────────────────────────────────
    def process(self, pcm_bytes: bytes) -> bytes:
        """Level one PCM16 chunk. Returns bytes of identical length."""
        if not pcm_bytes or len(pcm_bytes) % 2:
            return pcm_bytes
        x = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / _INT16_MAX
        if x.size == 0:
            return pcm_bytes

        out = np.empty_like(x)
        win = self._win
        prev_gain = self._gain
        touched = False
        for start in range(0, x.size, win):
            w = x[start:start + win]
            rms = float(np.sqrt(np.mean(w.astype(np.float64) ** 2))) + _EPS
            if rms > self.silence_rms:
                # update the smoothed envelope + slow running-average target
                self._env = rms if self._env is None else \
                    self._env + self._env_c * (rms - self._env)
                if self._avg is None:
                    self._avg = self._env          # first voiced level = reference
                    self._voiced = True
                else:
                    self._avg += self._avg_c * (self._env - self._avg)
                desired = float(np.clip(self._avg / (self._env + _EPS),
                                        self.min_gain, self.max_gain))
                c = self._att if desired < self._gain else self._rel
                self._gain += c * (desired - self._gain)
            # else: silence/pause → HOLD gain and envelope (never boost a pause)

            if prev_gain == 1.0 and self._gain == 1.0:
                out[start:start + win] = w         # untouched
            else:
                touched = True
                ramp = np.linspace(prev_gain, self._gain, w.size, dtype=np.float32)
                out[start:start + win] = w * ramp
            prev_gain = self._gain

        if not touched:
            return pcm_bytes

        # Soft limiter (tanh knee) on any peak a boost pushed past the ceiling.
        ceil = self.limiter_ceiling
        over = np.abs(out) > ceil
        if np.any(over):
            out[over] = np.sign(out[over]) * (
                ceil + (1.0 - ceil) * np.tanh((np.abs(out[over]) - ceil) / (1.0 - ceil)))
        np.clip(out, -1.0, 1.0, out=out)
        return (out * _INT16_MAX).astype(np.int16).tobytes()

    # ── diagnostics ────────────────────────────────────────────────────────────
    @property
    def current_gain_db(self) -> float:
        return 20.0 * float(np.log10(self._gain + _EPS))
