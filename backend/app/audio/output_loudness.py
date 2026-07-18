"""Output loudness leveling — keeps the AGENT'S OWN voice at a steady volume.

The problem this solves
-----------------------
Sarvam Bulbul (like every neural TTS) returns each sentence at a slightly
different loudness. Streamed to the caller untouched, the agent's volume
audibly drifts up and down from sentence to sentence. A human on a phone call
holds a near-constant level, so that drift is one of the clearest "this is a
bot" tells — exactly what callers notice.

The AGC in ``agc.py`` fixes the INPUT (the caller's mic). This is its mirror on
the OUTPUT side, but tuned very differently:

  • Reference-free. There is no absolute target dBFS to guess/mis-tune. Each
    sentence is pulled toward the CALL'S OWN running-average level (an EMA of
    recent sentence loudness). The overall volume therefore stays exactly where
    Sarvam naturally sits — only the outliers are reined in.
  • ONE constant gain per sentence. The gain is decided from the sentence's
    first audio and then held for the whole sentence. That means zero
    intra-sentence pumping/breathing (itself an AI tell) and — because the gain
    is computed from the first chunk that streams in — zero added latency.
  • Deliberately gentle. Correction is clamped to a narrow window (±6 dB by
    default), so natural prosody and emphasis survive; only genuine loud/quiet
    outliers get moved.
  • A soft limiter catches any peak a boost might push toward clipping.

Because the same leveled PCM is what we feed the AEC reference buffer AND send
to the caller, echo cancellation stays perfectly in sync (and actually benefits
from the steadier reference level).
"""
from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger(__name__)

_EPS = 1e-9
_INT16_MAX = 32767.0


class OutputLoudness:
    """Per-sentence constant-gain loudness leveler for TTS PCM16 output.

    Usage per sentence::

        leveler.start_sentence()
        for pcm in tts.synthesize(...):
            pcm = leveler.process(pcm)     # bytes in, bytes out (same length)
            ...

    Args mirror the ``tts_loudness_*`` settings. Gains are clamped to
    ``[min_gain, max_gain]`` so the leveler can only ever nudge, never
    dramatically re-scale, a sentence.
    """

    def __init__(
        self,
        avg_alpha: float = 0.30,
        max_gain: float = 2.0,
        min_gain: float = 0.5,
        silence_rms: float = 0.005,
        limiter_ceiling: float = 0.98,
    ) -> None:
        self.avg_alpha = float(avg_alpha)
        self.max_gain = float(max_gain)
        self.min_gain = float(min_gain)
        self.silence_rms = float(silence_rms)
        self.limiter_ceiling = float(limiter_ceiling)

        self._avg_rms: float | None = None     # running-average sentence level
        self._sentence_gain: float = 1.0        # constant gain for current sentence
        self._gain_locked: bool = False         # set once the sentence's level is known

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start_sentence(self) -> None:
        """Begin a new sentence. The gain is recomputed from its first voiced
        audio; until then the previous sentence's gain carries over so the very
        first chunk is never left ungained."""
        self._gain_locked = False

    def reset(self) -> None:
        """Full reset between calls/sessions — forget the running level."""
        self._avg_rms = None
        self._sentence_gain = 1.0
        self._gain_locked = False

    # ── processing ─────────────────────────────────────────────────────────────

    def process(self, pcm_bytes: bytes) -> bytes:
        """Level one PCM16 chunk. Returns bytes of identical length (so playhead
        accounting and AEC reference framing are unchanged)."""
        if not pcm_bytes:
            return pcm_bytes
        # Odd byte counts can't be a whole number of int16 samples — pass through
        # untouched rather than risk corrupting the stream.
        if len(pcm_bytes) % 2:
            return pcm_bytes

        x = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / _INT16_MAX
        if x.size == 0:
            return pcm_bytes

        rms = float(np.sqrt(np.mean(x.astype(np.float64) ** 2))) + _EPS

        # Lock the sentence gain from its first voiced chunk. Silence at the head
        # of a sentence is skipped so leading breaths/pauses don't set the level.
        if not self._gain_locked and rms > self.silence_rms:
            if self._avg_rms is None:
                # First sentence of the call defines the reference level — leave
                # it exactly as Sarvam produced it (gain 1.0).
                self._avg_rms = rms
                self._sentence_gain = 1.0
            else:
                desired = self._avg_rms / rms
                self._sentence_gain = float(
                    np.clip(desired, self.min_gain, self.max_gain))
                # Fold this sentence into the running average AFTER leveling, so
                # the reference tracks the loudness the caller actually hears.
                leveled_rms = rms * self._sentence_gain
                self._avg_rms = ((1.0 - self.avg_alpha) * self._avg_rms
                                 + self.avg_alpha * leveled_rms)
            self._gain_locked = True

        if self._sentence_gain == 1.0:
            return pcm_bytes  # nothing to do — avoid needless float round-trip

        y = x * self._sentence_gain
        # Soft limiter (tanh knee) only where the boost would exceed the ceiling,
        # so quiet passages stay linear and only true peaks are tamed.
        ceil = self.limiter_ceiling
        over = np.abs(y) > ceil
        if np.any(over):
            y[over] = np.sign(y[over]) * (
                ceil + (1.0 - ceil) * np.tanh((np.abs(y[over]) - ceil) / (1.0 - ceil)))
        y = np.clip(y, -1.0, 1.0)
        return (y * _INT16_MAX).astype(np.int16).tobytes()

    # ── diagnostics ────────────────────────────────────────────────────────────

    @property
    def current_gain_db(self) -> float:
        return 20.0 * float(np.log10(self._sentence_gain + _EPS))
