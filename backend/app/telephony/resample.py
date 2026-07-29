"""Sample-rate conversion for PCM16 mono audio.

The conversation stack runs at 16 kHz (STT input) and 24 kHz (TTS output); the
Exotel leg runs at whatever the Voicebot applet negotiates (8/16/24 kHz). This
module does the linear-interpolation resampling that bridges them.

Linear interpolation (not polyphase/sinc) is deliberate: it is dependency-free
(numpy only), fast enough for the real-time path, and the audio on both ends is
speech that is already band-limited by Sarvam TTS and the telephony codec, so the
aliasing a proper anti-alias filter would remove is inaudible in the 300-3400 Hz
voice band. Chunks are resampled independently; at 200 ms TTS granularity the
per-chunk boundary error is negligible for speech.
"""
from __future__ import annotations

import numpy as np


def resample_pcm16(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Resample little-endian 16-bit mono PCM from src_rate to dst_rate."""
    if src_rate == dst_rate or not pcm:
        return pcm
    # Guard odd byte counts (a truncated frame) — drop the trailing byte.
    if len(pcm) % 2:
        pcm = pcm[:-1]
        if not pcm:
            return b""

    x = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
    n_in = x.shape[0]
    n_out = int(round(n_in * dst_rate / src_rate))
    if n_out <= 0:
        return b""

    # Map output sample positions back onto the input timeline and interpolate.
    src_idx = np.linspace(0.0, n_in - 1, num=n_out, dtype=np.float64)
    y = np.interp(src_idx, np.arange(n_in), x)
    return np.clip(np.rint(y), -32768, 32767).astype("<i2").tobytes()
