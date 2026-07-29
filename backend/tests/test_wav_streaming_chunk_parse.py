"""Regression tests for clipped sentence starts (production call a59c8b6a2e69:
"the greeting was disturbed", "voice fully cracked").

A WAV written to a STREAM cannot know its own length in advance, so the 'data'
chunk size is a placeholder — commonly 0, sometimes 0xFFFFFFFF, sometimes the
declared total rather than what is in THIS message. _parse_wav sliced
    data[pos+8 : pos+8+size]
which returns b"" when the placeholder is 0. Sarvam sends the RIFF header on
the FIRST streamed chunk of each sentence, so that first chunk — the audio for
the opening ~200 ms — was silently discarded, clipping the first syllable off
the greeting and off every sentence. Subsequent chunks are headerless and pass
through untouched, which is why the rest of the sentence survived and the
symptom read as a glitchy/disturbed start rather than as obviously missing
audio.

Also covers the codec-attempt order: requesting raw PCM already at
tts_sample_rate avoids RIFF parsing AND both resampling stages, which is what
the reference deployment does.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.providers.sarvam_tts import _parse_wav


def _wav(pcm: bytes, rate: int = 24000, declared_size: int | None = None) -> bytes:
    """Build a RIFF/WAVE blob. declared_size overrides the 'data' size field so
    streaming placeholders can be simulated."""
    size = len(pcm) if declared_size is None else declared_size
    return (
        b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE"
        + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
        + b"data" + struct.pack("<I", size) + pcm
    )


PCM = bytes(range(256)) * 4          # 1024 bytes of recognisable payload


def test_complete_file_with_correct_size_still_parses():
    pcm, rate = _parse_wav(_wav(PCM))
    assert pcm == PCM
    assert rate == 24000


@pytest.mark.parametrize("placeholder", [0, 0xFFFFFFFF, 0x7FFFFFFF])
def test_streaming_placeholder_size_does_not_discard_the_payload(placeholder):
    """The actual production bug: size=0 returned b"" and the chunk vanished."""
    pcm, rate = _parse_wav(_wav(PCM, declared_size=placeholder))
    assert pcm == PCM, "streamed first chunk was dropped or truncated"
    assert rate == 24000


def test_headerless_continuation_chunk_passes_through():
    """Chunks 2..N carry no RIFF header — must pass through with rate None."""
    pcm, rate = _parse_wav(PCM)
    assert pcm == PCM
    assert rate is None


def test_declared_size_smaller_than_buffer_is_still_honoured():
    """A trustworthy size that genuinely bounds the payload must still trim
    trailing chunks (e.g. a LIST/INFO block after the data)."""
    blob = _wav(PCM, declared_size=512) + b"LIST" + b"\x00" * 8
    pcm, _ = _parse_wav(blob)
    assert pcm == PCM[:512]


def test_rate_is_read_from_the_fmt_chunk():
    for rate in (16000, 22050, 24000):
        _, parsed = _parse_wav(_wav(PCM, rate=rate))
        assert parsed == rate


def test_non_riff_data_is_never_mangled():
    assert _parse_wav(b"") == (b"", None)
    assert _parse_wav(b"\x01\x02\x03") == (b"\x01\x02\x03", None)


# ── codec attempt order: cleanest path first, matching the reference ─────────

def test_pcm_at_pipeline_rate_is_attempted_before_wav():
    """Raw PCM already at tts_sample_rate needs no RIFF parse and no resample.
    WAV must remain in the list as a fallback, just not first."""
    src = (Path(__file__).resolve().parents[1]
           / "app" / "providers" / "sarvam_tts.py").read_text(encoding="utf-8")
    code = "\n".join(ln for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))
    start = code.index("attempts = self._ws_cfg_known or [")
    block = code[start:code.index("]", start)]
    assert block.index('"pcm"') < block.index('"wav"')
    assert "speech_sample_rate" in block
    assert '"wav"' in block          # fallback preserved
