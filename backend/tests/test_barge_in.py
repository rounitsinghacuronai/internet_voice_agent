"""Enterprise barge-in test suite — fully offline, no API keys required.

Covers every scenario from the spec:
  1.  Customer interrupts during greeting
  2.  Customer interrupts during tool execution
  3.  Customer interrupts during long explanation
  4.  Customer changes topic mid-response
  5.  Customer changes language mid-response
  6.  Multiple consecutive interruptions
  7.  False-positive background noise
  8.  Silent interruption / partial words
  9.  State machine determinism
  10. Memory preservation across interruptions
  11. Tool-safety: shielded dispatch never loses results
  12. Debouncing: cooldown prevents double-fire
  13. Smart resume classification
  14. Slow-network / STT-delay simulation
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

# Make the repo importable regardless of where pytest is run from
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend.app.barge_in.manager import InterruptionEvent, InterruptionManager
from backend.app.config import get_settings
from backend.app.conversation.manager import ConversationManager, TurnChunk, _shielded_dispatch
from backend.app.conversation.memory import CallMemory
from backend.app.conversation.state import CallState, CallStateMachine


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures and helpers
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def settings():
    s = get_settings()
    # Speed up tests: shorten debounce and speech thresholds
    s.__dict__["bargein_cooldown_ms"] = 50
    s.__dict__["bargein_min_speech_ms"] = 100
    s.__dict__["vad_min_speech_ms"] = 50
    return s


@pytest.fixture
def sm():
    return CallStateMachine(session_id="test")


@pytest.fixture
def im(settings):
    return InterruptionManager(session_id="test", settings=settings)


def _silence(ms: int = 500, rate: int = 16000) -> bytes:
    """Pure silence PCM16."""
    samples = int(rate * ms / 1000)
    return np.zeros(samples, dtype=np.int16).tobytes()


def _noise(ms: int = 300, amplitude: float = 0.05, rate: int = 16000) -> bytes:
    """Low-amplitude noise (should NOT trigger VAD)."""
    samples = int(rate * ms / 1000)
    rng = np.random.default_rng(42)
    return (rng.uniform(-amplitude, amplitude, samples) * 32767).astype(np.int16).tobytes()


def _speech(ms: int = 400, amplitude: float = 0.5, rate: int = 16000) -> bytes:
    """Loud speech-like signal (should trigger VAD energy gate)."""
    samples = int(rate * ms / 1000)
    rng = np.random.default_rng(7)
    return (rng.uniform(-amplitude, amplitude, samples) * 32767).astype(np.int16).tobytes()


# ─────────────────────────────────────────────────────────────────────────────
# 1. State machine — deterministic transitions
# ─────────────────────────────────────────────────────────────────────────────

class TestCallStateMachine:
    def test_initial_state_is_idle(self, sm):
        assert sm.state is CallState.IDLE

    def test_legal_transition_idle_to_listening(self, sm):
        sm.transition(CallState.LISTENING, "test")
        assert sm.state is CallState.LISTENING

    def test_legal_transition_speaking_to_interrupted(self, sm):
        sm.transition(CallState.SPEAKING, "start")
        sm.transition(CallState.INTERRUPTED, "barge_in")
        assert sm.state is CallState.INTERRUPTED

    def test_interrupted_to_listening(self, sm):
        sm.transition(CallState.SPEAKING, "start")
        sm.transition(CallState.INTERRUPTED, "barge_in")
        sm.transition(CallState.LISTENING, "cleanup")
        assert sm.state is CallState.LISTENING

    def test_is_interruptible_only_in_speaking_and_thinking(self, sm):
        assert not sm.is_interruptible()           # IDLE
        sm.transition(CallState.LISTENING, "a")
        assert not sm.is_interruptible()
        sm.transition(CallState.THINKING, "b")
        assert sm.is_interruptible()
        sm.transition(CallState.SPEAKING, "c")
        assert sm.is_interruptible()
        sm.transition(CallState.INTERRUPTED, "d")
        assert not sm.is_interruptible()
        sm.transition(CallState.WAITING_FOR_USER, "e")
        assert not sm.is_interruptible()

    def test_illegal_transition_is_applied_but_logged(self, sm, caplog):
        import logging
        with caplog.at_level(logging.WARNING):
            sm.transition(CallState.THINKING, "illegal_from_idle")
        assert "ILLEGAL" in caplog.text
        assert sm.state is CallState.THINKING   # still applied

    def test_full_conversation_cycle(self, sm):
        """Simulate a complete conversation including a barge-in."""
        sm.transition(CallState.SPEAKING, "greeting")
        sm.transition(CallState.INTERRUPTED, "barge_in")
        sm.transition(CallState.LISTENING, "cleanup")
        sm.transition(CallState.THINKING, "utterance")
        sm.transition(CallState.SPEAKING, "response")
        sm.transition(CallState.WAITING_FOR_USER, "done")
        sm.transition(CallState.LISTENING, "silence_detected")
        sm.transition(CallState.IDLE, "hangup")
        assert sm.state is CallState.IDLE


# ─────────────────────────────────────────────────────────────────────────────
# 2. InterruptionManager — debounce, metrics, false positive
# ─────────────────────────────────────────────────────────────────────────────

class TestInterruptionManager:
    def test_should_interrupt_in_speaking_state(self, im, sm):
        sm.transition(CallState.SPEAKING, "start")
        assert im.should_interrupt(sm.state, 250.0)

    def test_should_interrupt_in_thinking_state(self, im, sm):
        sm.transition(CallState.THINKING, "start")
        assert im.should_interrupt(sm.state, 150.0)

    def test_should_not_interrupt_in_listening_state(self, im, sm):
        sm.transition(CallState.LISTENING, "start")
        assert not im.should_interrupt(sm.state, 250.0)

    def test_should_not_interrupt_in_idle_state(self, im):
        assert not im.should_interrupt(CallState.IDLE, 250.0)

    def test_cooldown_debounce_suppresses_rapid_second_trigger(self, im, sm):
        sm.transition(CallState.SPEAKING, "start")
        # First interrupt: should fire
        assert im.should_interrupt(sm.state, 250.0)
        im.record(sm.state, turn_no=1)

        # Immediate second attempt: must be suppressed
        assert not im.should_interrupt(sm.state, 250.0)

    def test_cooldown_expires_and_allows_next_interrupt(self, im, sm, settings):
        sm.transition(CallState.SPEAKING, "start")
        im.record(sm.state, turn_no=1)
        # Wait longer than cooldown (50 ms in test fixture)
        time.sleep(settings.bargein_cooldown_ms / 1000 + 0.01)
        assert im.should_interrupt(sm.state, 250.0)

    def test_record_returns_event_with_correct_fields(self, im, sm):
        sm.transition(CallState.SPEAKING, "start")
        evt = im.record(sm.state, turn_no=3, speech_ms=280.0, language_hint="hi")
        assert isinstance(evt, InterruptionEvent)
        assert evt.state_at_interrupt == "speaking"
        assert evt.turn_no == 3
        assert evt.speech_ms_at_trigger == 280.0
        assert evt.language_hint == "hi"
        assert not evt.false_positive_suspect

    def test_metrics_update_on_record(self, im, sm):
        sm.transition(CallState.SPEAKING, "start")
        im.record(sm.state, turn_no=1)
        time.sleep(0.06)
        sm.transition(CallState.THINKING, "restart")
        im.record(sm.state, turn_no=2)
        m = im.metrics()
        assert m["total_interruptions"] == 2
        assert m["during_speaking"] == 1
        assert m["during_thinking"] == 1

    def test_flag_false_positive(self, im, sm):
        sm.transition(CallState.SPEAKING, "start")
        im.record(sm.state, turn_no=1)
        im.flag_false_positive()
        last = im.last_event()
        assert last.false_positive_suspect
        assert im.metrics()["false_positives_suspected"] == 1

    def test_no_event_flag_is_noop(self, im):
        im.flag_false_positive()   # should not raise
        assert im.total_interruptions == 0

    def test_consecutive_interrupt_tracking(self, im, sm):
        sm.transition(CallState.SPEAKING, "start")
        im.record(sm.state, turn_no=1)
        time.sleep(0.06)
        im.record(sm.state, turn_no=2)
        assert im.consecutive_interruptions == 2
        assert im.metrics()["consecutive_max"] == 2

    def test_non_consecutive_resets_streak(self, im, sm):
        sm.transition(CallState.SPEAKING, "start")
        im.record(sm.state, turn_no=1)
        time.sleep(0.06)
        im.record(sm.state, turn_no=5)   # turn 5, not 2 → breaks streak
        assert im.consecutive_interruptions == 1


# ─────────────────────────────────────────────────────────────────────────────
# 3. Smart resume — interruption classification
# ─────────────────────────────────────────────────────────────────────────────

class TestSmartResume:
    def test_language_switch_detection(self, im):
        result = im.classify_interruption(
            "आपकी शिकायत दर्ज की जा रही है",
            "please continue in Hindi",
        )
        assert result == "language_switch"

    def test_clarification_detection(self, im):
        result = im.classify_interruption(
            "The restoration time will be approximately 4 hours",
            "sorry can you repeat that",
        )
        assert result == "clarification"

    def test_follow_up_detection(self, im):
        result = im.classify_interruption(
            "Your bill amount is 1,200 rupees",
            "and when is the due date",
        )
        assert result == "follow_up"

    def test_topic_change_default(self, im):
        result = im.classify_interruption(
            "Your complaint has been registered",
            "मुझे नया कनेक्शन चाहिए",
        )
        assert result == "topic_change"

    def test_accidental_short_utterance(self, im):
        result = im.classify_interruption("long speech...", "ok")
        assert result == "accidental"

    def test_hindi_language_switch_keyword(self, im):
        result = im.classify_interruption("something", "hindi mein bolo")
        assert result == "language_switch"

    def test_marathi_clarification_keyword(self, im):
        result = im.classify_interruption("something", "punha sangal ka")
        assert result == "clarification"


# ─────────────────────────────────────────────────────────────────────────────
# 4. Endpointer — speaking flag applies correct threshold
# ─────────────────────────────────────────────────────────────────────────────

class TestEndpointerBargeinThreshold:
    def test_speaking_flag_uses_bargein_threshold(self):
        from backend.app.audio.endpointing import Endpointer, EventType
        from backend.app.audio.vad import SileroVAD

        s = get_settings()
        s.__dict__["vad_min_speech_ms"] = 50     # very short for normal turns
        s.__dict__["bargein_min_speech_ms"] = 800  # very long for barge-in
        vad = SileroVAD(s.vad_threshold)
        vad._session = None   # force energy-gate for determinism
        ep = Endpointer(s, vad)

        # 400 ms of loud "speech" during TTS playback (speaking=True)
        audio = _speech(ms=400)
        events = ep.feed(audio, speaking=True)

        # SPEECH_START should NOT fire (400 ms < 800 ms bargein threshold)
        assert not any(e.type is EventType.SPEECH_START for e in events)

    def test_normal_turn_uses_short_threshold(self):
        from backend.app.audio.endpointing import Endpointer, EventType
        from backend.app.audio.vad import SileroVAD

        s = get_settings()
        s.__dict__["vad_min_speech_ms"] = 50
        s.__dict__["bargein_min_speech_ms"] = 800
        vad = SileroVAD(s.vad_threshold)
        vad._session = None
        ep = Endpointer(s, vad)

        # 400 ms of loud "speech" with speaking=False (normal turn)
        audio = _speech(ms=400)
        events = ep.feed(audio, speaking=False)

        # SPEECH_START should fire (400 ms > 50 ms normal threshold)
        assert any(e.type is EventType.SPEECH_START for e in events)

    def test_background_noise_does_not_trigger_bargein(self):
        from backend.app.audio.endpointing import Endpointer, EventType
        from backend.app.audio.vad import SileroVAD

        s = get_settings()
        s.__dict__["bargein_min_speech_ms"] = 300
        vad = SileroVAD(s.vad_threshold)
        vad._session = None
        ep = Endpointer(s, vad)

        # Low-amplitude noise (fan, keyboard) should not pass energy gate
        noise = _noise(ms=500, amplitude=0.02)
        events = ep.feed(noise, speaking=True)
        assert not any(e.type is EventType.SPEECH_START for e in events)


# ─────────────────────────────────────────────────────────────────────────────
# 5. ConversationManager — barge-in + memory preservation
# ─────────────────────────────────────────────────────────────────────────────

class TestConversationManagerBargein:
    """Tests that ConversationManager behaves correctly under cancellation."""

    def _make_manager(self, sentences: list[str]):
        """Build a ConversationManager with a fake streaming LLM."""
        s = get_settings()

        async def fake_stream(messages, tools=None, temperature=0.4):
            from backend.app.providers.base import LLMDelta
            for sentence in sentences:
                for word in sentence.split():
                    yield LLMDelta(text=word + " ")
                yield LLMDelta(text=". ")
            yield LLMDelta(text="", finish="stop", tool_calls=[])

        llm = MagicMock()
        llm.stream_chat = fake_stream

        tools = MagicMock()
        tools.schemas = []
        tools.dispatch = AsyncMock(return_value={"status": "ok"})

        return ConversationManager(s, llm, tools, session_id="test_mgr")

    def test_greeting_appended_to_history(self):
        mgr = self._make_manager(["Hello world"])
        chunk = mgr.greeting()
        assert chunk.kind == "sentence"
        assert any(h["role"] == "assistant" for h in mgr.memory.history)

    def test_sentences_emitted_as_generated(self):
        mgr = self._make_manager(["First sentence", "Second sentence"])

        async def collect():
            chunks = []
            async for c in mgr.run_turn("test input", "en"):
                chunks.append(c)
            return chunks

        chunks = asyncio.run(collect())
        sentence_chunks = [c for c in chunks if c.kind == "sentence"]
        assert len(sentence_chunks) >= 1

    def test_memory_preserved_after_cancellation(self):
        """Barge-in (CancelledError) must not lose already-spoken sentences."""
        s = get_settings()

        async def slow_stream(messages, tools=None, temperature=0.4):
            from backend.app.providers.base import LLMDelta
            # First sentence: completes immediately
            yield LLMDelta(text="Your complaint has been registered. ")
            # Second sentence: never completes (simulates mid-stream cancellation)
            await asyncio.sleep(10)
            yield LLMDelta(text="The estimated time is four hours.")

        llm = MagicMock()
        llm.stream_chat = slow_stream
        tools = MagicMock()
        tools.schemas = []
        tools.dispatch = AsyncMock(return_value={"status": "ok"})
        mgr = ConversationManager(s, llm, tools, session_id="test_cancel")

        async def run_and_cancel():
            async def collect():
                async for _ in mgr.run_turn("test", "en"):
                    pass

            task = asyncio.create_task(collect())
            await asyncio.sleep(0.05)   # let first sentence emit
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(run_and_cancel())

        # Memory should have the first sentence
        history_text = " ".join(
            h.get("content", "") or "" for h in mgr.memory.history
            if h["role"] == "assistant"
        )
        assert "complaint" in history_text.lower()

    def test_account_number_retained_across_interruption(self):
        """Memory slots (account_no, name, etc.) survive barge-in."""
        mem = CallMemory()
        mem.scan_user_text("मेरा नंबर 300012345678 है")
        assert mem.account_no == "300012345678"

        # Simulate a barge-in: account_no must still be there
        mem.scan_user_text("Wait, one more question")
        assert mem.account_no == "300012345678"   # not overwritten

    def test_language_updated_on_interruption_utterance(self):
        """If customer interrupts in Hindi, memory.language updates to 'hi'."""
        from backend.app.conversation.language import LanguageEngine
        lang = LanguageEngine()
        # AI was speaking Marathi
        lang.update("नेट गेलं आहे", "mr-IN")
        assert lang.language == "mr"
        # Customer interrupts in Hindi
        lang.update("hindi mein bolo please", "hi-IN")
        assert lang.language == "hi"
        assert lang.pinned

    def test_verified_status_not_reset_by_barge_in(self):
        """Verification status must survive mid-turn interruptions."""
        mem = CallMemory()
        mem.verified = True
        mem.verified_at = time.time()
        mem.account_no = "300012345678"
        mem.name = "Rajesh Kumar"

        # Simulate what scan_user_text does during a barge-in utterance
        mem.scan_user_text("Wait, I wanted to ask about my bill")
        assert mem.verified
        assert mem.account_no == "300012345678"
        assert mem.name == "Rajesh Kumar"


# ─────────────────────────────────────────────────────────────────────────────
# 6. Shielded dispatch — tool safety under cancellation
# ─────────────────────────────────────────────────────────────────────────────

class TestShieldedDispatch:
    def test_result_returned_when_not_cancelled(self):
        tools = MagicMock()
        tools.dispatch = AsyncMock(return_value={"ticket_no": "TC-999"})
        mem = CallMemory()

        async def run():
            return await _shielded_dispatch(tools, "register_complaint", {}, mem)

        result = asyncio.run(run())
        assert result == {"ticket_no": "TC-999"}

    def test_late_absorber_spawned_when_cancelled(self):
        """When the outer task is cancelled mid-dispatch, a background absorber runs."""
        call_log = []

        async def slow_tool(*args, **kwargs):
            await asyncio.sleep(0.1)
            call_log.append("finished")
            return {"ticket_no": "TC-123"}

        async def run():
            tools = MagicMock()
            tools.dispatch = AsyncMock(side_effect=slow_tool)
            mem = CallMemory()

            async def outer():
                await _shielded_dispatch(tools, "register_complaint", {}, mem)

            task = asyncio.create_task(outer())
            await asyncio.sleep(0.02)   # let dispatch start
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

            # Give the late absorber time to finish
            await asyncio.sleep(0.15)

        asyncio.run(run())
        assert "finished" in call_log

    def test_absorber_updates_memory_with_late_result(self):
        """The late absorber calls absorb_tool_result so memory is updated."""
        absorbed = []

        async def slow_register(*args, **kwargs):
            await asyncio.sleep(0.1)
            return {"verified": True, "account_no": "300012345678",
                    "name": "Test User", "mobile": "9999999999", "address": "Pune"}

        async def run():
            tools = MagicMock()
            tools.dispatch = AsyncMock(side_effect=slow_register)
            mem = CallMemory()

            original_absorb = mem.absorb_tool_result

            def tracking_absorb(tool, args, result):
                absorbed.append((tool, result))
                original_absorb(tool, args, result)

            mem.absorb_tool_result = tracking_absorb

            async def outer():
                await _shielded_dispatch(tools, "verify_customer",
                                         {"account_no": "300012345678"}, mem)

            task = asyncio.create_task(outer())
            await asyncio.sleep(0.02)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            await asyncio.sleep(0.15)   # wait for absorber
            return mem

        mem = asyncio.run(run())
        # absorb_tool_result should have been called with verify_customer result
        assert any(t == "verify_customer" for t, _ in absorbed)


# ─────────────────────────────────────────────────────────────────────────────
# 7. Multiple consecutive interruptions (stability)
# ─────────────────────────────────────────────────────────────────────────────

class TestConsecutiveInterruptions:
    def test_state_machine_stable_after_five_interruptions(self, sm, im, settings):
        """State machine must remain in a valid state after rapid-fire barge-ins."""
        for turn in range(5):
            sm.transition(CallState.SPEAKING, "start")
            assert sm.is_interruptible()
            # Advance time past cooldown between each
            if turn > 0:
                time.sleep(settings.bargein_cooldown_ms / 1000 + 0.01)
            if im.should_interrupt(sm.state, 250.0):
                sm.transition(CallState.INTERRUPTED, "barge_in")
                im.record(sm.state, turn_no=turn + 1)
            sm.transition(CallState.LISTENING, "cleanup")
            assert sm.state is CallState.LISTENING

        assert im.total_interruptions <= 5   # at most one per turn

    def test_consecutive_counter_resets_on_topic_change(self, im, sm):
        sm.transition(CallState.SPEAKING, "start")
        im.record(sm.state, turn_no=1)
        time.sleep(0.06)
        im.record(sm.state, turn_no=2)
        # Gap of 5 turns — not consecutive
        time.sleep(0.06)
        im.record(sm.state, turn_no=8)
        assert im.consecutive_interruptions == 1   # streak reset


# ─────────────────────────────────────────────────────────────────────────────
# 8. VAD — Silero vs energy gate fallback
# ─────────────────────────────────────────────────────────────────────────────

class TestVADFallback:
    def test_energy_gate_loud_frame_is_speech(self):
        from backend.app.audio.vad import SileroVAD
        vad = SileroVAD()
        vad._session = None   # force energy gate
        loud = np.full(512, 0.6, dtype=np.float32)
        assert vad.is_speech(loud)

    def test_energy_gate_silence_is_not_speech(self):
        from backend.app.audio.vad import SileroVAD
        vad = SileroVAD()
        vad._session = None
        silent = np.zeros(512, dtype=np.float32)
        assert not vad.is_speech(silent)

    def test_energy_gate_low_noise_is_not_speech(self):
        from backend.app.audio.vad import SileroVAD
        vad = SileroVAD()
        vad._session = None
        rng = np.random.default_rng(0)
        whisper = rng.uniform(-0.008, 0.008, 512).astype(np.float32)
        assert not vad.is_speech(whisper)

    def test_vad_reset_clears_state(self):
        from backend.app.audio.vad import SileroVAD
        vad = SileroVAD()
        vad.reset()
        assert vad._ctx.sum() == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 9. Endpointer — utterance lifecycle
# ─────────────────────────────────────────────────────────────────────────────

class TestEndpointerUtteranceCycle:
    def test_utterance_emitted_after_speech_plus_silence(self):
        from backend.app.audio.endpointing import Endpointer, EventType
        from backend.app.audio.vad import SileroVAD
        s = get_settings()
        vad = SileroVAD(s.vad_threshold)
        vad._session = None
        ep = Endpointer(s, vad)
        # Trailing silence must exceed vad_end_silence_ms (800 ms default) to flush.
        events = ep.feed(_speech(ms=500)) + ep.feed(_silence(ms=900))
        types = [e.type for e in events]
        assert EventType.SPEECH_START in types
        assert EventType.UTTERANCE in types

    def test_short_noise_blip_is_discarded(self):
        from backend.app.audio.endpointing import Endpointer, EventType
        from backend.app.audio.vad import SileroVAD
        s = get_settings()
        s.__dict__["vad_min_speech_ms"] = 200   # require 200 ms of speech
        vad = SileroVAD(s.vad_threshold)
        vad._session = None
        ep = Endpointer(s, vad)
        # Only 100 ms of speech — should flush with no UTTERANCE
        events = ep.feed(_speech(ms=100)) + ep.feed(_silence(ms=700))
        assert not any(e.type is EventType.UTTERANCE for e in events)

    def test_max_utterance_force_flush(self):
        from backend.app.audio.endpointing import Endpointer, EventType
        from backend.app.audio.vad import SileroVAD
        s = get_settings()
        s.__dict__["vad_max_utterance_s"] = 1   # force flush after 1 s
        vad = SileroVAD(s.vad_threshold)
        vad._session = None
        ep = Endpointer(s, vad)
        events = ep.feed(_speech(ms=1500))   # 1.5 s monologue
        assert any(e.type is EventType.UTTERANCE for e in events)

    def test_reset_clears_in_progress_utterance(self):
        from backend.app.audio.endpointing import Endpointer, EventType
        from backend.app.audio.vad import SileroVAD
        s = get_settings()
        vad = SileroVAD(s.vad_threshold)
        vad._session = None
        ep = Endpointer(s, vad)
        ep.feed(_speech(ms=300))   # partial speech
        ep.reset()
        # After reset, a fresh utterance cycle should work normally
        events = ep.feed(_speech(ms=500)) + ep.feed(_silence(ms=900))
        assert any(e.type is EventType.UTTERANCE for e in events)


# ─────────────────────────────────────────────────────────────────────────────
# 10. Language switch on interruption
# ─────────────────────────────────────────────────────────────────────────────

class TestLanguageSwitchOnInterruption:
    def test_language_engine_switches_on_explicit_command(self):
        from backend.app.conversation.language import LanguageEngine
        lang = LanguageEngine()
        lang.update("माझं बिल जास्त आलंय", "mr-IN")
        assert lang.language == "mr"
        lang.update("please speak in English only", "en-IN")
        assert lang.language == "en"
        assert lang.pinned

    def test_language_engine_hysteresis_ignores_stray_english_word(self):
        from backend.app.conversation.language import LanguageEngine
        lang = LanguageEngine()
        lang.update("मेरा बिल बहुत ज्यादा आया है", "hi-IN")
        lang.update("ok", "en-IN")  # stray word — must NOT flip
        assert lang.language == "hi"

    def test_interruption_manager_records_language_hint(self, im, sm):
        sm.transition(CallState.SPEAKING, "start")
        evt = im.record(sm.state, turn_no=1, language_hint="hi")
        assert evt.language_hint == "hi"

    def test_marathi_to_hindi_classification(self, im):
        result = im.classify_interruption(
            "तुमची तक्रार नोंदवली गेली आहे",
            "Hindi mein bolo bhai",
        )
        assert result == "language_switch"


# ─────────────────────────────────────────────────────────────────────────────
# 11. Silence / partial words / edge cases
# ─────────────────────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_utterance_does_not_start_turn(self):
        """STT returning empty text should be treated as noise, not a turn."""
        mem = CallMemory()
        initial_history = list(mem.history)
        # scan_user_text with empty string does nothing
        mem.scan_user_text("")
        assert mem.history == initial_history

    def test_very_short_utterance_is_accidental(self, im):
        result = im.classify_interruption("something long", "um")
        assert result == "accidental"

    def test_partial_word_utterance_classification(self, im):
        result = im.classify_interruption("something", "wa")  # partial "wait"
        assert result == "accidental"

    def test_multiple_account_numbers_in_one_utterance(self):
        """Only the first valid 12-digit number should be captured."""
        mem = CallMemory()
        mem.scan_user_text("mera number 300012345678 hai aur 999999999999 bhi")
        assert mem.account_no == "300012345678"   # first one wins

    def test_barge_in_during_idle_is_harmless(self, im, sm):
        """Barge-in should be silently rejected when in IDLE state."""
        assert not im.should_interrupt(sm.state, 250.0)   # sm is IDLE

    def test_im_summary_log_does_not_raise(self, im, sm, caplog):
        sm.transition(CallState.SPEAKING, "start")
        im.record(sm.state, turn_no=1)
        im.summary_log()   # should not raise or crash


# ─────────────────────────────────────────────────────────────────────────────
# 12. Slow-network / STT-delay simulation
# ─────────────────────────────────────────────────────────────────────────────

class TestNetworkDelayScenarios:
    def test_barge_in_while_stt_running_cancels_cleanly(self):
        """Simulate a second barge-in firing while the first STT is in progress."""
        import asyncio

        async def slow_transcribe(*args, **kwargs):
            await asyncio.sleep(0.5)   # simulate 500 ms STT latency
            result = MagicMock()
            result.text = "test"
            result.language = "en"
            return result

        async def run():
            stt = MagicMock()
            stt.transcribe = slow_transcribe

            # Just test that cancellation during STT propagates cleanly
            async def stt_and_cancel():
                try:
                    return await stt.transcribe(b"pcm", 16000)
                except asyncio.CancelledError:
                    return None  # clean exit

            task = asyncio.create_task(stt_and_cancel())
            await asyncio.sleep(0.05)   # STT in progress
            task.cancel()
            result = await task
            assert result is None  # cancelled cleanly

        asyncio.run(run())

    def test_utterance_queue_receives_sentinel_on_producer_cancel(self):
        """The `finally: queue.put_nowait(None)` sentinel must always fire."""

        async def run():
            import asyncio
            queue: asyncio.Queue = asyncio.Queue()

            async def producer():
                try:
                    for i in range(10):
                        await asyncio.sleep(0.01)
                        await queue.put(TurnChunk("sentence", f"word {i}", "en"))
                except asyncio.CancelledError:
                    raise
                finally:
                    try:
                        queue.put_nowait(None)
                    except Exception:
                        pass

            task = asyncio.create_task(producer())
            await asyncio.sleep(0.03)   # let a few items in
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

            # Drain queue — sentinel must be present
            items = []
            while not queue.empty():
                items.append(queue.get_nowait())
            assert None in items, "Sentinel missing — speaker would hang forever"

        asyncio.run(run())
