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
    """Resample little-endian 16-bit mono PCM from src_rate to dst_rate.

    STATELESS — treats `pcm` as a complete, standalone signal. Correct for a
    one-shot buffer, but calling this independently once per chunk of a
    continuous STREAM (the greeting, a TTS sentence, live mic audio) resets
    the interpolation reference to index 0 on every call. That produces a
    small timing discontinuity — an audible click — at every chunk boundary,
    which is one confirmed contributor to a real production complaint
    ("voice cracking"). For any streaming caller, use StreamResampler below
    instead; this function is kept for one-shot/offline use and tests."""
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


class StreamResampler:
    """Stateful linear-interpolation PCM16 resampler for a CONTINUOUS stream
    delivered in arbitrary chunks (TTS PCM as it streams in, live mic audio).

    Why this exists: resample_pcm16() above is correct for a single complete
    buffer, but the voice pipeline calls it once per streaming chunk — once
    per Sarvam TTS WAV frame, again per chunk sent to the Exotel leg, and
    again per inbound mic chunk. Each of those independent calls restarts
    linear interpolation at input index 0, so the last output sample of
    chunk N and the first output sample of chunk N+1 are NOT continuous —
    there's a small phase jump at every chunk boundary. With TTS streaming
    in many small chunks per sentence, that adds up to an audible click
    roughly every chunk, which is the leading code-level candidate for the
    "voice cracking" reported on live calls (the other candidate — a
    leg-rate mismatch with what Exotel's App Bazaar URL actually negotiates
    — is a config issue, not something this fixes).

    Carries exactly one float (the previous chunk's last raw sample) plus a
    fractional read-position remainder across calls. The global sequence of
    input-timeline positions sampled is IDENTICAL regardless of how the
    stream is chopped into chunks — feeding one long buffer through a single
    process() call and feeding the same buffer through many small process()
    calls produce numerically identical output (see test_stream_resampler.py).
    Note this is a different sampling grid than the one-shot resample_pcm16()
    above (fixed hop of src/dst per step vs. an evenly-divided endpoint-to-
    endpoint span), so the two are not expected to match each other bit for
    bit on the same single buffer — only StreamResampler-vs-itself, chunked
    vs. unchunked, is guaranteed to match.

    One instance per direction per call (inbound leg→16k, outbound TTS→leg,
    Sarvam-rate→24k) — construct fresh per session/stream, never share.
    """

    def __init__(self, src_rate: int, dst_rate: int):
        self.src_rate = src_rate
        self.dst_rate = dst_rate
        self._ratio = src_rate / dst_rate if dst_rate else 1.0
        self._prev_sample: float | None = None   # last raw sample of the previous chunk
        self._next_pos: float = 0.0               # next output sample's position,
        # expressed in input-sample units relative to the START of the chunk
        # about to be processed. Always in (-1, ratio) — see process() proof.

    def process(self, pcm: bytes) -> bytes:
        if self.src_rate == self.dst_rate:
            return pcm
        if not pcm:
            return b""
        if len(pcm) % 2:
            pcm = pcm[:-1]
            if not pcm:
                return b""

        x = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
        n_in = x.shape[0]

        if self._prev_sample is not None:
            xx = np.concatenate(([self._prev_sample], x))
            idx_offset = 1.0   # xx[1] == x[0]; xx[0] is the previous chunk's tail
        else:
            xx = x
            idx_offset = 0.0

        positions = []
        pos = self._next_pos
        while pos <= n_in - 1:
            positions.append(pos)
            pos += self._ratio
        # Carry the overshoot to the next chunk. pos is the first position that
        # exceeded n_in - 1, and the step before it satisfied pos' <= n_in - 1,
        # so pos == pos' + ratio <= n_in - 1 + ratio → remainder <= ratio - 1.
        # pos > n_in - 1 by loop exit → remainder > -1. So remainder always
        # lands in (-1, ratio - 1], meaning only ONE sample of look-back
        # (self._prev_sample) is ever needed, regardless of the rate ratio.
        self._next_pos = pos - n_in
        if n_in:
            self._prev_sample = float(x[-1])

        if not positions:
            return b""
        src_idx = np.asarray(positions, dtype=np.float64) + idx_offset
        y = np.interp(src_idx, np.arange(xx.shape[0]), xx)
        return np.clip(np.rint(y), -32768, 32767).astype("<i2").tobytes()
