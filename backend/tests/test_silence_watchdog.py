"""No-response / silence watchdog tests — fully offline, no API keys.

Covers the training-manual "NO RESPONSE" flow:
  • Re-prompt is spoken in the caller's current language.
  • Consecutive silences escalate; the Nth prompt is the closing + disconnect.
  • A caller utterance resets the no-response counter.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend.app.config import get_settings
from backend.app.conversation.manager import ConversationManager
from backend.app.conversation.state import CallState


# ── manager string helpers ───────────────────────────────────────────────────

def _manager():
    s = get_settings()
    return ConversationManager(s, MagicMock(), MagicMock(), "test")


def test_nudge_defaults_to_marathi_before_caller_speaks():
    m = _manager()
    assert m.memory.language == "und"
    chunk = m.silence_nudge()
    assert chunk.language == "mr"
    assert "आहात" in chunk.text  # Marathi "are you there"


def test_nudge_follows_caller_language():
    m = _manager()
    m.memory.language = "hi"
    assert m.silence_nudge().language == "hi"
    m.memory.language = "en"
    assert m.silence_nudge().language == "en"


def test_closing_is_the_manual_no_response_line():
    m = _manager()
    m.memory.language = "mr"
    chunk = m.no_response_closing()
    assert chunk.language == "mr"
    assert "डिस्कनेक्ट" in chunk.text          # disconnect announcement
    assert "धन्यवाद" in chunk.text             # official closing thanks


# ── watchdog flow on a VoiceSession (audio stack mocked) ──────────────────────

def _session():
    """Build a VoiceSession with the heavy audio components stubbed out."""
    from backend.app.api import ws_voice

    deps = MagicMock()
    deps.settings = get_settings()
    deps.llm = MagicMock()
    deps.tools = MagicMock()

    with patch.object(ws_voice, "SileroVAD", MagicMock()), \
         patch.object(ws_voice, "Endpointer", MagicMock()), \
         patch.object(ws_voice, "AudioPipeline", MagicMock()):
        sess = ws_voice.VoiceSession(MagicMock(), deps)

    # Stub the transport / synthesis so no real audio or socket is needed.
    sess._speak_sentence = AsyncMock()
    sess._drain_playback = AsyncMock()
    sess._send = AsyncMock()
    sess.ws = MagicMock()
    sess.ws.close = AsyncMock()
    return sess


@pytest.mark.asyncio
async def test_first_silence_reprompts_and_keeps_listening():
    sess = _session()
    await sess._fire_silence_prompt()

    assert sess._no_response_count == 1
    sess._speak_sentence.assert_awaited_once()      # a nudge was spoken
    sess.ws.close.assert_not_called()               # not disconnected yet
    assert sess.sm.state is CallState.LISTENING     # back to listening
    sess.endpointer.reset.assert_called()           # endpointer un-wedged


@pytest.mark.asyncio
async def test_max_prompts_speaks_closing_and_disconnects():
    sess = _session()
    sess.s.__dict__["silence_max_prompts"] = 3

    await sess._fire_silence_prompt()   # 1
    await sess._fire_silence_prompt()   # 2
    sess.ws.close.assert_not_called()
    await sess._fire_silence_prompt()   # 3 → disconnect

    assert sess._no_response_count == 3
    sess.ws.close.assert_awaited_once()
    assert sess._speak_sentence.await_count == 3


@pytest.mark.asyncio
async def test_bargein_disabled_never_interrupts_agent_speech():
    """With bargein_enabled=False, caller-speech events must not fire a barge-in
    while the agent is speaking (prevents self-echo cancelling the agent)."""
    sess = _session()
    sess.s.__dict__["bargein_enabled"] = False
    sess.sm.transition(CallState.SPEAKING, "tts_start")
    sess._trigger_barge_in = AsyncMock()

    await sess._on_speech_start()

    sess._trigger_barge_in.assert_not_called()
    assert sess.sm.state is CallState.SPEAKING   # still speaking, uninterrupted


@pytest.mark.asyncio
async def test_no_bargein_during_greeting():
    """While the greeting is playing, caller-speech events (usually the greeting's
    own echo) must not fire a barge-in."""
    sess = _session()
    sess.s.__dict__["bargein_enabled"] = True   # isolate: barge-in on, greeting guard is what suppresses
    sess._greeting_active = True
    sess.sm.transition(CallState.SPEAKING, "greeting")
    sess._trigger_barge_in = AsyncMock()

    await sess._on_speech_start()

    sess._trigger_barge_in.assert_not_called()
    assert sess.sm.state is CallState.SPEAKING


@pytest.mark.asyncio
async def test_bargein_resumes_after_greeting():
    """Once the greeting flag clears, a talk-over during agent speech interrupts."""
    sess = _session()
    sess.s.__dict__["bargein_enabled"] = True
    sess._greeting_active = False
    sess.sm.transition(CallState.SPEAKING, "tts_start")
    sess._trigger_barge_in = AsyncMock()
    # Push past the TTS grace window so the interruption is honoured.
    sess._speaking_since = 0.0
    sess.im.should_interrupt = MagicMock(return_value=True)

    await sess._on_speech_start()

    sess._trigger_barge_in.assert_awaited_once()


@pytest.mark.asyncio
async def test_caller_utterance_resets_counter():
    sess = _session()
    await sess._fire_silence_prompt()
    assert sess._no_response_count == 1

    # Caller speaks again → _on_utterance resets the watchdog.
    sess._handle_utterance = AsyncMock()   # don't launch the real handler
    with patch("asyncio.create_task", MagicMock()):
        await sess._on_utterance(b"\x00\x00")
    assert sess._no_response_count == 0
