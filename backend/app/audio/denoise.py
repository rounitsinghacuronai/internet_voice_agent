"""Server-side noise suppression. Browser already applies WebRTC NS; this is a second
pass for phone/telephony audio. Uses `noisereduce` (spectral gating) when installed —
same role as RNNoise without a native build. No-op passthrough otherwise."""
from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger(__name__)


class Denoiser:
    def __init__(self, enabled: bool = True, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self._nr = None
        if enabled:
            try:
                import noisereduce  # type: ignore

                self._nr = noisereduce
                log.info("noisereduce enabled")
            except ImportError:
                log.info("noisereduce not installed — denoise passthrough")

    def process(self, audio: np.ndarray) -> np.ndarray:
        """audio: float32 mono [-1,1]. Applied per-utterance (not per-frame) so the
        spectral profile has enough context; frame-level latency is untouched."""
        if self._nr is None or len(audio) < self.sample_rate // 4:
            return audio
        try:
            return self._nr.reduce_noise(
                y=audio, sr=self.sample_rate, stationary=True, prop_decrease=0.75
            ).astype(np.float32)
        except Exception as e:  # pragma: no cover
            log.warning("denoise failed (%s) — passthrough", e)
            return audio
