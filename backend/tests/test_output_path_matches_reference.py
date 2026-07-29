"""Locks the OUTBOUND audio path to the reference deployment's behaviour.

Context: voice quality regressed against the reference project (same codebase,
different domain). A file-by-file audit of the output path found exactly two
stages present here that the reference does not have, both added locally and
neither verified on a live call:

  1. audio/output_loudness.py, applied per TTS chunk in ws_voice._speak_sentence.
     Documented in config.py as "ONE constant gain per sentence, so there is no
     intra-sentence pumping" — but start_sentence() is a no-op and process()
     is a CONTINUOUS compressor (10 ms window, 30 ms RMS detector, 120 ms
     attack / 350 ms release, every window driven toward a 2.5 s running
     average, up to +/-6 dB). Speech carries ~15-20 dB of dynamic range within
     one sentence, so that boosts consonants, ducks vowels and rides the gain
     across each phrase — compressor pumping, heard as wandering volume,
     flattened dynamics and a processed/"robotic" timbre.

  2. llm_first_flush_chars=80 in conversation/manager.py, which dropped the
     comma-split threshold from 160 to 80 for the first segment of each turn.
     Most replies open with a sentence longer than 80 chars, so in practice
     nearly every turn's opening sentence was cut at a comma into two separate
     Sarvam calls, each with its own prosody contour and loudness: pitch
     restart mid-sentence, possible level step at the seam, and a synthesis gap
     where no speaker would pause.

Both are now off/removed. These tests fail if either comes back silently.

Deliberately NOT reverted (audited, kept — each is a strict improvement over
the reference and none of them alters the signal the way the two above do):
telephony/resample.py's StreamResampler (removes a per-chunk interpolation
discontinuity the reference still has), sarvam_tts.py's REST WAV-header parse
+ resample (the reference assumes the returned rate and plays at the wrong
speed when it differs), and the narrower speech_pace_min/max band (0.9-1.08 vs
the reference's 0.7-1.25 — narrower means LESS sentence-to-sentence speed
variation, which is the stated goal).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.config import Settings, get_settings


# ── 1. no output-side gain processing by default ─────────────────────────────

def test_output_loudness_is_disabled_by_default():
    """The reference has no output gain stage at all; Sarvam's PCM reaches the
    leg untouched. That is the quality bar being matched."""
    assert Settings().tts_loudness_normalize is False


def test_voice_session_builds_no_loudness_stage_by_default():
    from backend.app.api import ws_voice

    deps = MagicMock()
    deps.settings = get_settings()
    with patch.object(ws_voice, "SileroVAD", MagicMock()), \
         patch.object(ws_voice, "Endpointer", MagicMock()), \
         patch.object(ws_voice, "AudioPipeline", MagicMock()):
        sess = ws_voice.VoiceSession(MagicMock(), deps)
    assert sess._loudness is None


@pytest.mark.asyncio
async def test_tts_pcm_reaches_the_transport_byte_identical():
    """End-to-end on the real _speak_sentence path: every byte Sarvam produced
    must arrive at the transport unmodified. Any resampling, gain, limiting or
    padding stage silently re-introduced into the output path breaks this."""
    from backend.app.api import ws_voice
    from backend.app.conversation.manager import TurnChunk

    deps = MagicMock()
    deps.settings = get_settings()

    # Three chunks with full-scale peaks and near-silence: a compressor or a
    # limiter would visibly alter these; a passthrough cannot.
    source = [b"\x00\x7f" * 200, b"\x01\x00" * 200, b"\xff\x7f" * 200]

    async def fake_synthesize(text, lang, pace):
        for c in source:
            yield c

    deps.tts.synthesize = fake_synthesize

    with patch.object(ws_voice, "SileroVAD", MagicMock()), \
         patch.object(ws_voice, "Endpointer", MagicMock()), \
         patch.object(ws_voice, "AudioPipeline", MagicMock()):
        sess = ws_voice.VoiceSession(MagicMock(), deps)

    sent: list[bytes] = []
    sess.ws = MagicMock()
    sess.ws.send_bytes = AsyncMock(side_effect=lambda b: sent.append(b))
    sess._send = AsyncMock()
    sess._advance_playhead = MagicMock()
    sess._log_first_audio_latency = MagicMock()

    await sess._speak_sentence(
        TurnChunk("sentence", text="नमस्कार", language="mr", pace=1.0))

    assert b"".join(sent) == b"".join(source)


# ── 2. one uniform comma-flush threshold, no first-segment special case ──────

def test_first_flush_setting_is_gone():
    assert not hasattr(Settings(), "llm_first_flush_chars")


def test_manager_uses_the_single_force_flush_threshold():
    from backend.app.conversation.manager import _FORCE_FLUSH_CHARS

    assert _FORCE_FLUSH_CHARS == 160
    src = (Path(__file__).resolve().parents[1]
           / "app" / "conversation" / "manager.py").read_text(encoding="utf-8")
    code = "\n".join(ln for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))
    assert "elif len(buffer) >= _FORCE_FLUSH_CHARS:" in code
    assert "llm_first_flush_chars" not in code


# ── 3. the stages that WERE kept must stay wired in ──────────────────────────

def test_streaming_resamplers_still_used_on_both_legs():
    """Guards against 'fixing' quality by reverting to the reference's
    stateless per-chunk resample_pcm16, which clicks at every chunk seam."""
    for mod in ("telephony/exotel.py", "providers/sarvam_tts.py"):
        src = (Path(__file__).resolve().parents[1] / "app" / mod
               ).read_text(encoding="utf-8")
        assert "StreamResampler" in src, f"{mod} lost its streaming resampler"


def test_pace_band_stays_narrow():
    s = Settings()
    assert s.speech_pace_min >= 0.9
    assert s.speech_pace_max <= 1.08
    assert s.tts_pace == 1.0
