"""Spectral Noise Gate — adaptive noise floor estimation + Wiener suppression.

Tackles the single most common noise complaint for voice agents: steady-state
background noise (fan, air conditioner, hum, traffic) that sits below speech
level but above the VAD energy gate, causing false triggers.

Algorithm
---------
  1. The gate maintains a running estimate of the noise PSD (power spectral
     density) updated only from frames classified as silence.
  2. For every incoming frame / utterance, a Wiener-style gain is computed:

         G(f) = max(1 – α·|N(f)|² / |X(f)|², β)

     where |N(f)|² is the noise PSD, |X(f)|² is the noisy signal PSD,
     α (over-subtraction, default 2.5) controls aggressiveness, and β
     (spectral floor, default 0.002) prevents total suppression of any bin.

  3. The clean output is G(f)·X(f) transformed back to time domain via
     overlap-add IFFT.

Unlike `noisereduce` (which is applied per-utterance after VAD), the
SpectralNoiseGate runs on every individual 32 ms VAD frame so that the
Silero model sees cleaner input and produces more reliable probabilities.
"""
from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger(__name__)

_EPS = 1e-10


class SpectralNoiseGate:
    """Online spectral noise suppressor.

    Args:
        sample_rate        Input sample rate (Hz).
        fft_size           FFT window size (samples).  Default 512 = 32 ms@16k.
        noise_alpha        Noise PSD smoothing: higher = slower update (0–1).
        over_subtraction   α — how aggressively to subtract noise (1.5–4).
        spectral_floor     β — minimum gain applied to any frequency bin.
        init_silence_ms    Duration (ms) of silence used to prime the noise
                           floor at session start before any speech is heard.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        fft_size: int = 512,
        noise_alpha: float = 0.92,
        over_subtraction: float = 2.5,
        spectral_floor: float = 0.002,
        init_silence_ms: float = 800.0,
    ) -> None:
        self.sr = sample_rate
        self.fft_size = fft_size
        self.n_bins = fft_size // 2 + 1
        self._alpha = noise_alpha
        self._over_sub = over_subtraction
        self._floor = spectral_floor

        # Noise PSD estimate — initialised to a very small value
        self._noise_psd: np.ndarray = np.full(self.n_bins, 1e-8, dtype=np.float64)

        # Count silent frames consumed in the priming window
        self._init_frames_needed = int(
            init_silence_ms / 1000.0 * sample_rate / fft_size
        )
        self._init_frames_seen = 0
        self._primed = False

        # Hanning window (reused across calls)
        self._window = np.hanning(fft_size)

    # ── public API ────────────────────────────────────────────────────────────

    def process_frame(self, frame: np.ndarray, is_speech: bool) -> np.ndarray:
        """Process one 32 ms VAD frame.

        Args:
            frame      float32 mono, length fft_size (512 samples @ 16k).
            is_speech  True if VAD already determined this frame is voiced.
                       When False, the frame is used to refine the noise floor.
        Returns:
            Noise-suppressed float32 frame, same length as input.
        """
        # Zero-pad / trim to fft_size
        n = len(frame)
        padded = np.zeros(self.fft_size, dtype=np.float64)
        padded[:min(n, self.fft_size)] = frame[:self.fft_size].astype(np.float64)

        spec = np.fft.rfft(padded * self._window)
        mag_sq = np.abs(spec) ** 2

        if not is_speech:
            self._update_noise(mag_sq)

        gain = self._wiener_gain(mag_sq)
        clean_spec = spec * gain
        clean = np.fft.irfft(clean_spec)[:n]

        # Undo window energy loss
        win_energy = np.mean(self._window[:n] ** 2) or 1.0
        clean /= (win_energy ** 0.5 + _EPS)

        return np.clip(clean, -1.0, 1.0).astype(np.float32)

    def process_utterance(self, audio: np.ndarray) -> np.ndarray:
        """Suppress noise from a complete utterance using overlap-add.

        This is the path called per-utterance before STT.  Overlap-add gives
        smooth results even for long recordings.
        """
        n = len(audio)
        if n < self.fft_size // 2:
            return audio.astype(np.float32)

        hop = self.fft_size // 2
        out = np.zeros(n + self.fft_size, dtype=np.float64)
        win_sum = np.zeros(n + self.fft_size, dtype=np.float64)

        for start in range(0, n, hop):
            end = min(start + self.fft_size, n)
            chunk = audio[start:end].astype(np.float64)
            if len(chunk) < self.fft_size:
                chunk = np.pad(chunk, (0, self.fft_size - len(chunk)))

            spec = np.fft.rfft(chunk * self._window)
            mag_sq = np.abs(spec) ** 2
            gain = self._wiener_gain(mag_sq)
            clean_frame = np.fft.irfft(spec * gain)

            out[start : start + self.fft_size] += clean_frame * self._window
            win_sum[start : start + self.fft_size] += self._window ** 2

        win_sum = np.maximum(win_sum[:n], _EPS)
        result = out[:n] / win_sum
        return np.clip(result, -1.0, 1.0).astype(np.float32)

    def update_noise_from_silence(self, audio: np.ndarray) -> None:
        """Force a noise floor update from audio known to be silence.

        Call this with the first few seconds of audio before the call's
        greeting is spoken, or from frames the VAD marks as non-speech.
        """
        hop = self.fft_size
        for start in range(0, len(audio) - hop, hop):
            chunk = audio[start : start + hop].astype(np.float64)
            spec = np.fft.rfft(chunk * self._window)
            self._update_noise(np.abs(spec) ** 2)

    def reset(self) -> None:
        self._noise_psd[:] = 1e-8
        self._init_frames_seen = 0
        self._primed = False

    # ── internals ─────────────────────────────────────────────────────────────

    def _update_noise(self, mag_sq: np.ndarray) -> None:
        """Exponential smoothing of noise PSD estimate."""
        if not self._primed:
            self._init_frames_seen += 1
            # During priming: use fast update (alpha=0.5) to bootstrap quickly
            alpha = 0.5
            if self._init_frames_seen >= self._init_frames_needed:
                self._primed = True
                log.debug("spectral gate: noise floor primed (%.1f dB avg)",
                          10 * np.log10(np.mean(self._noise_psd) + _EPS))
        else:
            alpha = self._alpha

        self._noise_psd = alpha * self._noise_psd + (1 - alpha) * mag_sq

    def _wiener_gain(self, mag_sq: np.ndarray) -> np.ndarray:
        """Compute Wiener suppression gain G(f) ∈ [β, 1]."""
        n = len(mag_sq)
        noise = self._noise_psd[:n]
        # G(f) = max(1 - α·N(f)/X(f), β)
        with np.errstate(divide="ignore", invalid="ignore"):
            gain = np.maximum(
                1.0 - self._over_sub * noise / (mag_sq + _EPS),
                self._floor,
            )
        return gain.astype(np.float64)

    @property
    def noise_floor_db(self) -> float:
        """Current estimated noise floor in dBFS (for diagnostics)."""
        return float(10.0 * np.log10(np.mean(self._noise_psd) + _EPS))
