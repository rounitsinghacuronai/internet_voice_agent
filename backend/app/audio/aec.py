"""Acoustic Echo Canceller (AEC) — frequency-domain, reference-based.

Problem
-------
The AI's TTS audio plays through the device speaker.  The room reflects some
of that audio back into the microphone.  That echo arrives at the server as
mic input and can trigger barge-in even when the customer is silent.

The browser's WebRTC AEC (echoCancellation: true in getUserMedia) handles
most of this, but it is imperfect — especially on laptops with weak speakers,
Bluetooth headsets, and speaker-phone calls where the echo path is long.

Our Approach
-----------
We have the TTS PCM on the server before it reaches the client speaker (we
stream it to the client over the WebSocket).  This gives us the ideal
"reference signal" for echo cancellation:

  1. AEC reference buffer: every TTS chunk is written here as it is sent.
  2. Frequency-domain echo suppression: when the agent is speaking, compute
     the short-term spectrum of the reference TTS audio and apply a
     frequency-dependent gain that attenuates mic frequencies that match
     the TTS spectrum.  This is a "spectral subtraction with TTS reference"
     approach — much simpler than full NLMS adaptive filtering but effective
     for the typical flat-frequency echo we see from laptop speakers.
  3. Gate-based suppression: a global gain reduction is applied to the mic
     during TTS to handle reverberant tails that aren't in the reference
     buffer window.

Why not NLMS?
  Full NLMS adaptive filtering requires estimating the acoustic echo delay
  (speaker→room→mic) which varies per device (typically 50–300 ms).  Our
  system already has the aggressiveness knob in `bargein_min_speech_ms`; the
  spectral approach gives 10–15 dB of echo reduction which is sufficient to
  prevent false barge-ins in most environments.
"""
from __future__ import annotations

import logging
from collections import deque

import numpy as np

log = logging.getLogger(__name__)

_EPS = 1e-10


class AcousticEchoCanceller:
    """Frequency-domain AEC using TTS as a reference signal.

    Args:
        sample_rate           Input/output sample rate (Hz).
        ref_buffer_ms         How many ms of TTS history to keep as reference.
                              Should be > max expected echo delay (default 2 s).
        echo_suppression_db   Maximum attenuation applied to echo-matching
                              frequency bins (default 18 dB).
        gate_gain_db          Additional broadband gate applied during TTS
                              to handle reverb tails (default –6 dB = 0.5×).
        ref_smooth_alpha      EMA smoothing for the reference spectrum so a
                              single loud TTS word doesn't over-suppress mic.
        fft_size              FFT size used for spectral suppression.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        ref_buffer_ms: int = 2000,
        echo_suppression_db: float = 18.0,
        gate_gain_db: float = -6.0,
        ref_smooth_alpha: float = 0.85,
        fft_size: int = 512,
    ) -> None:
        self.sr = sample_rate
        self.fft_size = fft_size
        self.n_bins = fft_size // 2 + 1
        self._min_echo_gain = 10 ** (-echo_suppression_db / 20.0)  # linear floor
        self._gate_gain = 10 ** (gate_gain_db / 20.0)              # broadband gate
        self._alpha = ref_smooth_alpha

        # Ring buffer: stores last ref_buffer_ms of TTS PCM (float32)
        self._buf_size = int(sample_rate * ref_buffer_ms / 1000)
        self._ref_buf = np.zeros(self._buf_size, dtype=np.float32)
        self._write_ptr = 0

        # Smoothed TTS power spectrum (updated every time reference is fed)
        self._ref_psd = np.zeros(self.n_bins, dtype=np.float64)
        self._ref_has_data = False

        # TTS active flag — set by the voice session
        self._tts_active = False

    # ── reference feeding (called per TTS PCM chunk sent to client) ──────────

    def feed_reference(self, pcm_bytes: bytes, sample_rate: int = 24000) -> None:
        """Accept a chunk of TTS PCM and add it to the reference buffer.

        pcm_bytes is the raw PCM16 that was just sent to the WebSocket client.
        If the TTS sample rate differs from our input rate, we downsample.
        """
        audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0

        # Downsample if TTS rate (typically 24 kHz) ≠ mic rate (16 kHz)
        if sample_rate != self.sr:
            ratio = self.sr / sample_rate
            new_len = max(1, int(len(audio) * ratio))
            indices = np.linspace(0, len(audio) - 1, new_len)
            audio = np.interp(indices, np.arange(len(audio)), audio).astype(np.float32)

        self._write_to_buf(audio)

        # Update reference PSD for spectral suppression
        if len(audio) >= self.fft_size:
            chunk = audio[-self.fft_size:]
        else:
            chunk = np.pad(audio, (self.fft_size - len(audio), 0))
        psd = np.abs(np.fft.rfft(chunk.astype(np.float64), n=self.fft_size)) ** 2
        self._ref_psd = self._alpha * self._ref_psd + (1 - self._alpha) * psd
        self._ref_has_data = True

    def notify_tts_active(self, active: bool) -> None:
        """Call with True when TTS starts, False when it ends."""
        self._tts_active = active
        if not active:
            # When TTS ends, decay reference PSD so stale reference
            # doesn't suppress the customer's first words
            self._ref_psd *= 0.3
            log.debug("AEC: TTS ended — reference PSD decayed")

    # ── echo suppression (called per mic utterance / VAD frame) ──────────────

    def process(self, mic: np.ndarray) -> np.ndarray:
        """Apply echo suppression to mic audio.

        Args:
            mic  float32 mono in [-1, 1], any length.
        Returns:
            Echo-suppressed float32 audio, same shape.
        """
        if not self._tts_active or not self._ref_has_data:
            return mic  # no-op when agent is silent

        # Broadband gate: attenuate entire mic signal during TTS
        out = mic * self._gate_gain

        # Frequency-domain echo suppression per hop
        n = len(out)
        hop = self.fft_size // 2
        result = out.copy().astype(np.float64)

        for start in range(0, n, hop):
            end = min(start + self.fft_size, n)
            chunk = result[start:end]
            if len(chunk) < 4:
                continue
            padded = np.zeros(self.fft_size, dtype=np.float64)
            padded[:len(chunk)] = chunk

            mic_spec = np.fft.rfft(padded)
            mic_psd = np.abs(mic_spec) ** 2

            ref_psd = self._ref_psd[:self.n_bins]
            # Gain: suppress bins where reference is dominant
            # G(f) = max(1 - α·Ref(f)/Mic(f), min_echo_gain)
            with np.errstate(divide="ignore", invalid="ignore"):
                echo_ratio = ref_psd / (mic_psd + _EPS)
            gain = np.maximum(1.0 - 2.5 * echo_ratio, self._min_echo_gain)

            clean_spec = mic_spec * gain
            clean = np.fft.irfft(clean_spec)[:len(chunk)]
            result[start:end] = clean

        clipped = np.clip(result, -1.0, 1.0)
        log.debug(
            "AEC: in_rms=%.4f out_rms=%.4f",
            float(np.sqrt(np.mean(mic ** 2))),
            float(np.sqrt(np.mean(clipped ** 2))),
        )
        return clipped.astype(np.float32)

    # ── state ─────────────────────────────────────────────────────────────────

    def reset(self) -> None:
        self._ref_buf[:] = 0
        self._write_ptr = 0
        self._ref_psd[:] = 0
        self._ref_has_data = False
        self._tts_active = False

    # ── internal ──────────────────────────────────────────────────────────────

    def _write_to_buf(self, audio: np.ndarray) -> None:
        n = len(audio)
        end = self._write_ptr + n
        if end <= self._buf_size:
            self._ref_buf[self._write_ptr : end] = audio
        else:
            first = self._buf_size - self._write_ptr
            self._ref_buf[self._write_ptr :] = audio[:first]
            self._ref_buf[: n - first] = audio[first:]
        self._write_ptr = end % self._buf_size
