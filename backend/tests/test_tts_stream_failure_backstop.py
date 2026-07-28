"""Regression test for a real risk found while investigating a production
report (session acbfc95ee8dc): the caller said the agent's speech went
'cracking'/unrecognizable in the final ~10 seconds of the call. Log evidence:
turn 6's text generation finished at 10:32:49 but the playback-drain log
(meaning ALL of that turn's audio had finally finished being synthesized and
sent) didn't fire until 10:33:00 — an ~11s wall-clock gap for a reply whose
own audio only plays ~6-7s, with no error logged anywhere in between.

While tracing that stall, found sarvam_tts.py's streaming synthesize() had a
real, separate bug: if the Sarvam TTS WebSocket fails PARTWAY through a
sentence, the code logged a warning and returned (silently truncating that
sentence — the rest is never spoken) WITHOUT disabling stream mode for the
rest of the call, the way the sibling "failed before first chunk" branch
already does. On a genuinely degraded connection this meant every following
sentence could hit the exact same silent truncation, repeatedly, instead of
falling back to the more reliable REST path after the first sign of trouble.
Not proven to be THE cause of the 11s stall (no such warning appears in that
window), but a real, low-risk-to-fix robustness gap found along the way.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.config import get_settings
from backend.app.providers.sarvam_tts import SarvamTTS


def _make_tts() -> SarvamTTS:
    s = get_settings()
    s.tts_streaming_enabled = True
    return SarvamTTS(s, httpx.AsyncClient(trust_env=False))


def test_mid_stream_failure_disables_streaming_for_rest_of_session():
    async def run():
        tts = _make_tts()
        assert tts._stream_disabled is False

        async def flaky_ws(text, lang, pace):
            yield b"\x00\x00" * 50           # got_any=True
            raise ConnectionError("socket reset mid-sentence")

        tts._synthesize_ws = flaky_ws

        # First call: streaming attempted, fails mid-stream, truncates.
        chunks = [c async for c in tts.synthesize("Pehla vaakya.", "hi", 1.0)]
        assert chunks                        # partial audio was yielded
        assert tts._stream_disabled is True  # the fix: now disabled, not left on

        # Second call, a DIFFERENT sentence: must NOT touch _synthesize_ws again
        # (streaming is disabled) — falls through to the REST path instead.
        ws_called = {"n": 0}

        async def should_not_be_called(text, lang, pace):
            ws_called["n"] += 1
            yield b""

        tts._synthesize_ws = should_not_be_called

        async def fake_full(text, lang, pace):
            return b"\x00\x00" * 20

        tts._synthesize_full = fake_full
        chunks2 = [c async for c in tts.synthesize("Doosra vaakya.", "hi", 1.0)]
        assert ws_called["n"] == 0
        assert chunks2
    asyncio.run(run())


def test_failure_before_first_chunk_still_disables_streaming():
    """Sibling branch — must keep working exactly as before."""
    async def run():
        tts = _make_tts()

        async def dead_ws(text, lang, pace):
            raise ConnectionError("never connected")
            yield b""  # pragma: no cover - unreachable, makes this an async gen

        tts._synthesize_ws = dead_ws

        async def fake_full(text, lang, pace):
            return b"\x00\x00" * 20

        tts._synthesize_full = fake_full
        chunks = [c async for c in tts.synthesize("Kuch bhi.", "hi", 1.0)]
        assert tts._stream_disabled is True
        assert chunks   # REST fallback still produced audio
    asyncio.run(run())
