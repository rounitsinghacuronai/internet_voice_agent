"""Regression tests for StreamResampler (telephony/resample.py) — the fix for
a real production complaint ("voice cracking"). Root cause: TTS PCM is
resampled independently per streaming chunk at two call sites (Sarvam's own
WAV chunks -> 24kHz in sarvam_tts.py, then 24kHz -> Exotel leg rate in
exotel.py), and the old stateless resample_pcm16() restarts its interpolation
reference at index 0 on every call — a phase discontinuity (click) at every
chunk boundary. StreamResampler carries continuity across chunks instead.

Pure function tests, no network/audio hardware — uses synthetic sine tones
exactly like the existing TestResampler class in test_exotel.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.telephony.resample import StreamResampler, resample_pcm16


def _pcm(ms: int, rate: int, freq: float = 220.0, amp: float = 0.4) -> bytes:
    n = int(rate * ms / 1000)
    t = np.arange(n) / rate
    return (np.sin(2 * np.pi * freq * t) * amp * 32767).astype("<i2").tobytes()


def _i16(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm, dtype="<i2").astype(np.float64)


def _split(pcm: bytes, chunk_bytes: int) -> list[bytes]:
    """Split PCM16 bytes into chunks of chunk_bytes (kept even for 16-bit alignment)."""
    chunk_bytes -= chunk_bytes % 2
    return [pcm[i:i + chunk_bytes] for i in range(0, len(pcm), chunk_bytes)] or [b""]


# ── basic behavior (mirrors TestResampler in test_exotel.py) ──────────────────

class TestStreamResamplerBasics:
    def test_passthrough_when_rates_equal(self):
        r = StreamResampler(16000, 16000)
        pcm = _pcm(50, 16000)
        assert r.process(pcm) == pcm

    def test_empty_input(self):
        r = StreamResampler(24000, 16000)
        assert r.process(b"") == b""

    def test_odd_byte_count_is_handled(self):
        r = StreamResampler(8000, 16000)
        out = r.process(b"\x01\x02\x03")
        assert len(out) % 2 == 0

    def test_output_length_scales_with_ratio_over_a_full_stream(self):
        pcm = _pcm(1000, 24000)   # 24000 samples, fed as one chunk
        r = StreamResampler(24000, 16000)
        out = r.process(pcm)
        n_out = len(out) // 2
        assert abs(n_out - 16000) <= 2


# ── the core invariant: chunking must not change the result ───────────────────

class TestChunkingInvariance:
    """The whole point of StreamResampler: feeding a signal through many small
    process() calls must reproduce feeding it through one big call."""

    @pytest.mark.parametrize("src,dst", [
        (24000, 8000),    # TTS -> narrowband leg (heaviest real ratio)
        (24000, 16000),   # TTS -> wideband leg
        (8000, 16000),    # inbound leg -> STT rate (upsample)
        (16000, 24000),   # arbitrary upsample
    ])
    @pytest.mark.parametrize("chunk_ms", [200, 40, 17, 5])
    def test_chunked_matches_unchunked(self, src, dst, chunk_ms):
        pcm = _pcm(600, src, freq=300.0)
        reference = StreamResampler(src, dst).process(pcm)

        chunk_bytes = max(2, int(src * chunk_ms / 1000) * 2)
        r = StreamResampler(src, dst)
        pieces = [r.process(c) for c in _split(pcm, chunk_bytes)]
        chunked = b"".join(pieces)

        # Lengths must match exactly, and — since the position sequence is
        # provably chunk-invariant (see resample.py docstring) — so must the
        # rounded int16 samples themselves, allowing only for a possible
        # off-by-one sample at the very tail from generator exhaustion.
        ref = _i16(reference)
        got = _i16(chunked)
        n = min(len(ref), len(got))
        assert abs(len(ref) - len(got)) <= 1
        assert np.array_equal(ref[:n], got[:n])

    def test_tiny_one_sample_chunks_still_match(self):
        """Pathological case: chunks smaller than the resample hop itself
        (e.g. a single 2-byte sample per call) must not lose or duplicate
        samples relative to the unchunked reference."""
        pcm = _pcm(100, 24000, freq=440.0)
        reference = StreamResampler(24000, 8000).process(pcm)

        r = StreamResampler(24000, 8000)
        pieces = [r.process(pcm[i:i + 2]) for i in range(0, len(pcm), 2)]
        chunked = b"".join(pieces)

        ref = _i16(reference)
        got = _i16(chunked)
        n = min(len(ref), len(got))
        assert abs(len(ref) - len(got)) <= 1
        assert np.array_equal(ref[:n], got[:n])


# ── old vs new: chunking sensitivity ───────────────────────────────────────────

class TestOldApproachIsChunkSizeSensitive:
    """The old stateless resample_pcm16(), called once per chunk, restarts its
    input-position grid at 0 every call — so its output DEPENDS on how the
    stream happens to be chopped into chunks, which a correct streaming
    resampler's output must not. This doesn't assert a specific artifact
    size (that depends on content/rate/chunk-size in ways not worth pinning
    to a magic number); it asserts the qualitative property that motivated
    this fix: two different, equally valid chunkings of the SAME signal
    produce two DIFFERENT outputs from the old function, but the SAME
    output (mod tail rounding) from StreamResampler."""

    def test_old_function_output_depends_on_chunking_new_does_not(self):
        pcm = _pcm(500, 24000, freq=300.0, amp=0.8)

        def old_chunked(chunk_ms):
            cb = int(24000 * chunk_ms / 1000) * 2
            return b"".join(resample_pcm16(c, 24000, 8000) for c in _split(pcm, cb))

        def new_chunked(chunk_ms):
            cb = int(24000 * chunk_ms / 1000) * 2
            r = StreamResampler(24000, 8000)
            return b"".join(r.process(c) for c in _split(pcm, cb))

        old_a, old_b = _i16(old_chunked(50)), _i16(old_chunked(13))
        new_a, new_b = _i16(new_chunked(50)), _i16(new_chunked(13))

        n_old = min(len(old_a), len(old_b))
        n_new = min(len(new_a), len(new_b))
        old_diff = np.abs(old_a[:n_old] - old_b[:n_old]).max()
        new_diff = np.abs(new_a[:n_new] - new_b[:n_new]).max()

        assert old_diff > 0, (
            "expected the old stateless function to disagree with itself across "
            "two different chunkings of the same signal")
        assert new_diff == 0, (
            "StreamResampler must produce the identical result regardless of "
            "how the input stream was chunked")
