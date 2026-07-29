"""Latency-optimization layer — offline regression tests.

Covers the deterministic pieces of the 900–1200 ms speech-to-speech work:
  • endpointer exposes silence progress (drives streaming-STT early flush)
  • early-flush bookkeeping is idempotent per silence window
  • finalize skips the duplicate flush after an early flush
  • first-audio comma-flush threshold applies only while nothing has been
    voiced yet this turn
  • TTS prefetch de-duplicates in-flight synthesis
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.config import get_settings


# ── endpointer: silence progress is visible ──────────────────────────────────
def test_endpointer_reports_silence_progress():
    from backend.app.audio.endpointing import Endpointer
    from backend.app.audio.vad import SileroVAD

    # Copy the cached Settings — other tests mutate it (e.g. shrinking
    # vad_max_utterance_s), which must not leak into this one.
    s = get_settings().model_copy()
    s.vad_min_speech_ms = 150
    s.vad_max_utterance_s = 25
    vad = SileroVAD(s.vad_threshold)
    vad._session = None                     # energy gate, deterministic
    ep = Endpointer(s, vad)
    ep.end_silence_ms = 400                 # pin: test independent of env tuning
    rng = np.random.default_rng(1)
    speech = (rng.uniform(-0.4, 0.4, 16000) * 32767).astype(np.int16).tobytes()
    ep.feed(speech)
    assert ep.in_speech and ep.silence_ms == 0.0
    # ~200 ms of silence — utterance must still be open, silence_ms counting up
    silence = np.zeros(3200, dtype=np.int16).tobytes()   # 200 ms @16k
    ep.feed(silence)
    assert ep.in_speech
    assert 150 <= ep.silence_ms <= 250      # frame-quantized around 200 ms


# ── streaming STT: early flush is idempotent + suppresses duplicate flush ────
class _FakeWS:
    pass


def _make_stream():
    from backend.app.providers.sarvam_stt_stream import SarvamSTTStream
    st = SarvamSTTStream(get_settings(), lambda: "hi")
    st._ws = _FakeWS()                      # pretend connected
    return st


def test_early_flush_queues_one_marker():
    async def run():
        st = _make_stream()
        st.early_flush()
        assert st._q.qsize() == 1           # one flush marker
        assert st._early_flushed_at > 0
        return st
    asyncio.run(run())


def test_finalize_skips_duplicate_flush_after_early_flush():
    async def run():
        st = _make_stream()
        st.early_flush()                    # marker 1 (early)
        st._parts.append("नेट चालत नाही")     # transcript already arrived
        tr = await st.finalize()
        assert tr is not None and "नेट" in tr.text
        assert st._q.qsize() == 1           # NO second flush marker queued
        assert st._parts == []              # window consumed
    asyncio.run(run())


def test_finalize_cold_path_still_flushes():
    async def run():
        st = _make_stream()
        st._early_flushed_at = time.monotonic() - 5.0    # stale — cold finalize
        st._parts.append("hello")
        tr = await st.finalize()
        assert tr is not None
        assert st._q.qsize() == 1           # cold finalize queues its own flush
    asyncio.run(run())


# ── manager: comma-flush threshold is UNIFORM (no first-segment special case) ─
def test_comma_flush_threshold_is_uniform_across_the_whole_turn():
    """Replaces test_first_flush_threshold_only_before_first_voiced_sentence.

    The old first-segment threshold (llm_first_flush_chars=80) traded audio
    quality for ~200 ms of first-audio latency: because most replies open with
    a sentence longer than 80 chars, it split nearly every turn's opening
    sentence at a comma into two independent Sarvam synthesis calls, each with
    its own prosody contour and its own loudness — heard as a pitch restart
    mid-sentence, a possible level step at the seam, and a gap where no
    speaker would pause. The setting is gone and manager.py now applies the
    single 160-char rule to every segment, matching the reference deployment.
    """
    from backend.app.conversation.manager import _FORCE_FLUSH_CHARS
    s = get_settings()
    assert not hasattr(s, "llm_first_flush_chars")   # setting removed
    assert _FORCE_FLUSH_CHARS == 160
    # And the source no longer branches on _turn_is_first for this threshold.
    # Comments are stripped first: the code block that replaced it deliberately
    # NAMES the removed setting to explain why it went, which would otherwise
    # trip the "no longer referenced" assertion below.
    src = (Path(__file__).resolve().parents[1]
           / "app" / "conversation" / "manager.py").read_text(encoding="utf-8")
    code = "\n".join(ln for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))
    assert "elif len(buffer) >= _FORCE_FLUSH_CHARS:" in code
    assert "llm_first_flush_chars" not in code


# ── TTS: prefetch + synthesize share one in-flight task ──────────────────────
def test_tts_prefetch_deduplicates_inflight():
    import httpx
    from backend.app.providers.sarvam_tts import SarvamTTS

    async def run():
        s = get_settings()
        tts = SarvamTTS(s, httpx.AsyncClient(trust_env=False))
        calls = {"n": 0}

        async def fake_fetch(key, text, lang, pace):
            calls["n"] += 1
            await asyncio.sleep(0.02)
            tts._cache[key] = b"\x00\x00" * 100
            tts._inflight.pop(key, None)
            return tts._cache[key]

        tts._fetch = fake_fetch
        tts.prefetch("Your ticket number is TC123.", "en", 1.0)
        await asyncio.sleep(0.005)          # prefetch task started, not finished
        pcm = b""
        async for chunk in tts.synthesize("Your ticket number is TC123.", "en", 1.0):
            pcm += chunk
        assert pcm and calls["n"] == 1      # ONE network call, not two
    asyncio.run(run())
