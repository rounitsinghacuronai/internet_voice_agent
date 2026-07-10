"""WS voice gateway — one VoiceSession per call.  Full-duplex:

  RX task  : binary PCM16 16k frames → denoise → VAD/endpointing
               • SPEECH_START while agent is speaking/thinking → BARGE-IN
               • UTTERANCE → STT → ConversationManager turn
  TX (speak): manager sentences → Sarvam TTS → binary PCM16 24k to client

Also accepts {"type":"text"} messages so the same session can be driven by
typing (testing / accessibility / future Exotel media-stream adapter).

────────────────────────────────────────────────────────────────────────────
BARGE-IN DESIGN (enterprise-grade)
────────────────────────────────────────────────────────────────────────────

Problems with the naive "cancel _speak_task" approach that existed before:
  1. Only the TTS task was cancelled; the LLM producer kept streaming tokens
     and held the _turn_lock — next utterance blocked until LLM finished.
  2. CallStateMachine was defined but never wired in, so barge-in was not
     state-gated (could fire during IDLE/LISTENING).
  3. The `speaking` flag was never passed to the Endpointer, so the
     conservative bargein_min_speech_ms threshold was never used.

This implementation fixes all three:

  A. TASK TRACKING: _active_turn_task points to the asyncio.Task wrapping
     _handle_utterance. Cancelling it propagates CancelledError → _run_turn
     → asyncio.gather(), which cancels BOTH producer and speaker atomically.
     _turn_lock releases via async-with context manager on exception exit.

  B. STATE MACHINE: every state change goes through CallStateMachine.transition()
     which validates and logs every hop. _trigger_barge_in() checks
     sm.is_interruptible() before acting.

  C. ENDPOINTER SPEAKING FLAG: _on_audio() passes sm.is_speaking() so the
     endpointer uses bargein_min_speech_ms (250 ms) during TTS and the
     shorter vad_min_speech_ms (150 ms) in normal turn-taking.

  D. INTERRUPTION MANAGER: debouncing, metrics, false-positive flagging, and
     language-switch detection live in barge_in.manager.

  E. TOOL SAFETY: ConversationManager wraps every dispatch in asyncio.shield();
     _late_tool_absorb() captures results that complete post-barge-in so
     memory is never corrupted mid-write.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import ast
import numpy as np
import websockets

from ..audio.endpointing import Endpointer, EventType
from ..audio.pipeline import AudioPipeline
from ..audio.vad import SileroVAD
from ..barge_in.manager import InterruptionManager
from ..config import Settings
from ..conversation.manager import ConversationManager, TurnChunk
from ..conversation.numbers import looks_like_number_fragment
from ..conversation.state import CallState, CallStateMachine

log = logging.getLogger(__name__)

# Shared thread-pool for CPU-bound audio processing (AGC, spectral gate, AEC,
# noisereduce).  Running these synchronously in the event loop blocks the WS
# receiver and makes the mic appear to freeze on longer utterances.
_AUDIO_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="audio")


class VoiceSession:
    def __init__(self, ws, deps, metadata: dict):
        self.ws = ws
        self.deps = deps
        self.metadata = metadata
        self.s: Settings = deps.settings
        self.session_id = uuid.uuid4().hex[:12]

        # Core conversation logic
        self.manager = ConversationManager(
            self.s, deps.llm, deps.tools, self.session_id
        )

        # Audio pipeline
        self._vad_base_threshold = self.s.vad_threshold
        # Reuse the model loaded once at process startup (main.py) instead of
        # reloading it per call — this used to block the event loop before the
        # WebSocket even finished accepting (see audio/vad.py for detail).
        self.vad = SileroVAD(self.s.vad_threshold, ort_session=getattr(deps, "vad_session", None))
        self.endpointer = Endpointer(self.s, self.vad)
        self.pipeline = AudioPipeline(self.s, self.session_id)

        # State machine — single source of truth for what the call is doing
        self.sm = CallStateMachine(session_id=self.session_id)

        # Interruption manager — debouncing, metrics, false-positive tracking
        self.im = InterruptionManager(session_id=self.session_id, settings=self.s)

        # Task references for atomic barge-in cancellation.
        # _active_turn_task is the _handle_utterance asyncio.Task; cancelling it
        # propagates through _run_turn → asyncio.gather → both subtasks.
        self._active_turn_task: Optional[asyncio.Task] = None
        self._producer_task: Optional[asyncio.Task] = None
        self._speak_task: Optional[asyncio.Task] = None

        # Guards ConversationManager (not re-entrant)
        self._turn_lock = asyncio.Lock()

        # monotonic time the agent most recently STARTED speaking. Used to apply
        # the post-TTS grace window that rejects echo self-triggers (see
        # _on_speech_start). 0.0 = agent has never spoken yet.
        self._speaking_since: float = 0.0

        # Peak VAD probability observed during the current agent-speaking window.
        # Logged as it climbs so we can SEE how far the caller's voice is being
        # suppressed by the browser echo canceller during double-talk and tune
        # the interrupt threshold precisely. Reset each time the agent starts.
        self._speak_peak_prob: float = 0.0

        # Server-side estimate of the wall-clock (monotonic) time at which the
        # CLIENT will finish playing everything we have streamed so far. TTS is
        # sent to the browser far faster than real-time, so this mirrors the
        # client's Web Audio scheduler (playTime) exactly: it lets the server
        # stay in SPEAKING — with barge-in live — until playback actually drains,
        # instead of flipping to LISTENING the moment the last byte is sent.
        self._playhead_mono: float = 0.0

        # Mic diagnostics
        self._frames_rx = 0
        self._peak = 0.0

        # Silence / no-response watchdog. _last_activity is the monotonic time of
        # the caller's most recent detected speech (or the end of the agent's last
        # utterance); the monitor re-prompts when it has been quiet too long.
        self._last_activity: float = time.monotonic()
        self._no_response_count: int = 0
        self._silence_task: Optional[asyncio.Task] = None

        # True while the opening greeting is playing. At call start the echo
        # canceller hasn't converged, so the agent's own (loud) greeting leaks
        # into the mic and self-triggers barge-in. We suppress barge-in for the
        # greeting only (see _on_speech_start); normal barge-in resumes after.
        self._greeting_active: bool = False

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def run(self) -> None:
        await self._send({"type": "ready", "session_id": self.session_id})
        log.info("session %s: call started", self.session_id)

        # Greeting
        greeting = self.manager.greeting()
        self._greeting_active = True
        self.sm.transition(CallState.SPEAKING, "greeting")
        self._speaking_since = time.monotonic()
        self._speak_peak_prob = 0.0
        self._playhead_mono = 0.0
        self._speak_task = asyncio.create_task(
            self._speak([greeting]), name=f"greet_{self.session_id}"
        )

        # Watchdog that re-prompts (and eventually disconnects) on caller silence.
        self._last_activity = time.monotonic()
        self._silence_task = asyncio.create_task(
            self._silence_monitor(), name=f"silence_{self.session_id}"
        )

        try:
            async for msg in self.ws:
                if isinstance(msg, bytes):
                    await self._on_audio(msg)
                elif isinstance(msg, str):
                    await self._on_control(json.loads(msg))
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            await self._teardown()

    async def _teardown(self) -> None:
        self.sm.transition(CallState.IDLE, "call_ended")
        for t in (self._active_turn_task, self._producer_task, self._speak_task,
                  self._silence_task):
            if t and not t.done():
                t.cancel()
        self.pipeline.reset()
        self.im.summary_log()
        log.info(
            "session %s closed (turns=%d, interruptions=%d)",
            self.session_id, self.manager.turn_no, self.im.total_interruptions,
        )

    # ── inbound audio ────────────────────────────────────────────────────────

    async def _on_audio(self, pcm16: bytes) -> None:
        self._frames_rx += 1
        if self._frames_rx == 1:
            log.info("session %s: mic audio flowing", self.session_id)

        peak = float(np.abs(np.frombuffer(pcm16, dtype=np.int16)).max() / 32768.0)
        self._peak = max(self._peak, peak)
        if self._frames_rx % 125 == 0:
            log.info("mic peak (last 5s): %.3f  %s", self._peak,
                     "— SILENCE" if self._peak < 0.02 else "")
            self._peak = 0.0

        # KEY FIX: pass speaking=True during TTS so Endpointer applies the
        # conservative bargein_min_speech_ms threshold, reducing false positives
        # from echo, fan noise, and keyboard clicks during agent speech.
        is_speaking = self.sm.is_speaking()

        # Dynamic VAD threshold: raise during TTS to further suppress echo barge-ins.
        # The base threshold is restored as soon as TTS ends.
        boost = self.s.bargein_vad_threshold_boost if is_speaking else 0.0
        self.vad.threshold = self._vad_base_threshold + boost

        for event in self.endpointer.feed(pcm16, speaking=is_speaking):
            if event.type is EventType.SPEECH_START:
                await self._on_speech_start()
            elif event.type is EventType.UTTERANCE:
                await self._on_utterance(event.pcm16, event.peak_prob)

        # DIAGNOSTIC: while the agent is speaking, surface how high the caller's
        # VAD probability climbs. If this stays well below the interrupt
        # threshold whenever you talk over the agent, the browser echo canceller
        # is suppressing your voice and the fix is browser-side (relax
        # echoCancellation) rather than more server threshold tuning.
        if is_speaking and self.vad.last_prob > self._speak_peak_prob + 0.05:
            self._speak_peak_prob = self.vad.last_prob
            log.info(
                "session %s: double-talk VAD prob during agent speech: %.2f "
                "(interrupt threshold %.2f)",
                self.session_id, self.vad.last_prob, self.vad.threshold,
            )

    async def _on_speech_start(self) -> None:
        # Any detected caller speech means they are present — reset the silence clock.
        self._last_activity = time.monotonic()
        log.debug("session %s: speech_start (state=%s)", self.session_id, self.sm.state.value)
        # Barge-in globally disabled (e.g. speaker+mic testing where the agent's own
        # audio echoes back and self-triggers). Let the current utterance finish.
        if not self.s.bargein_enabled:
            return
        # During the opening greeting the AEC is still cold and the greeting echo
        # self-triggers barge-in — suppress it until the greeting finishes.
        if self._greeting_active:
            log.info("session %s: barge-in suppressed during greeting", self.session_id)
            return
        if not self.sm.is_interruptible():
            log.debug("session %s: speech in non-interruptible state — ignored",
                      self.session_id)
            return
        # Post-TTS grace window: while the agent is SPEAKING, ignore speech that
        # arrives in the first bargein_tts_grace_ms after TTS started. On a
        # speaker setup the browser echo canceller is still reconverging after
        # the caller's own utterance, and residual echo spikes past the VAD the
        # instant playback begins — producing a self-barge-in that cuts the agent
        # off before it can speak. A genuine interruption comes later, once the
        # agent is actually talking, so it is unaffected. THINKING has no audio
        # playing and is never gated here.
        if self.sm.state is CallState.SPEAKING:
            since_ms = (time.monotonic() - self._speaking_since) * 1000
            if since_ms < self.s.bargein_tts_grace_ms:
                log.info(
                    "session %s: barge-in suppressed by TTS grace (%.0f/%d ms)",
                    self.session_id, since_ms, self.s.bargein_tts_grace_ms,
                )
                return
        if self.im.should_interrupt(self.sm.state, 0.0):
            await self._trigger_barge_in()
        else:
            log.info("session %s: barge-in suppressed by cooldown/debounce", self.session_id)

    async def _on_utterance(self, pcm16: bytes, peak_prob: float = 1.0) -> None:
        """Full utterance available — launch a handler task."""
        # Caller responded — reset the silence watchdog.
        self._last_activity = time.monotonic()
        self._no_response_count = 0
        if self._active_turn_task and self._active_turn_task.done():
            self._active_turn_task = None

        task = asyncio.create_task(
            self._handle_utterance(pcm16, peak_prob),
            name=f"utt_{self.session_id}_{self.manager.turn_no + 1}",
        )
        self._active_turn_task = task

    # ── barge-in ─────────────────────────────────────────────────────────────

    async def _trigger_barge_in(self) -> None:
        """Atomically stop TTS + LLM generation and switch to INTERRUPTED.

        Cancellation chain:
          _active_turn_task.cancel()
            → CancelledError injected into _handle_utterance at current await
            → propagates into _run_turn
            → asyncio.gather(producer, speaker) cancels BOTH subtasks
            → _turn_lock released by async-with __aexit__
            → next _handle_utterance task acquires lock immediately
        """
        state_before = self.sm.state
        self.sm.transition(CallState.INTERRUPTED, "barge_in")

        # Record timestamp BEFORE cancellation for accurate metrics
        self.im.record(state_before, self.manager.turn_no)

        # Cancel the whole turn pipeline
        if self._active_turn_task and not self._active_turn_task.done():
            self._active_turn_task.cancel()

        # Belt-and-suspenders: also cancel subtasks in case the task reference
        # is stale (e.g., barge-in fires while still in STT phase, before
        # _run_turn has been entered and subtasks registered)
        if self._producer_task and not self._producer_task.done():
            self._producer_task.cancel()
        if self._speak_task and not self._speak_task.done():
            self._speak_task.cancel()

        await self._send({"type": "interrupted"})
        log.info(
            "session %s: BARGE-IN (was=%s turn=%d total=%d)",
            self.session_id, state_before.value,
            self.manager.turn_no, self.im.total_interruptions,
        )

    # ── utterance handling ───────────────────────────────────────────────────

    async def _handle_utterance(self, pcm16: bytes, peak_prob: float = 1.0) -> None:
        """AGC→AEC→SpectralGate→denoise→SpeakerVerify → STT → run_turn.

        The whole coroutine is cancellable (barge-in can fire again here).
        Utterances rejected by the audio pipeline are silently dropped so
        TV audio / background conversations never reach STT.

        IMPORTANT: _active_turn_task is always cleared in the outer finally
        block, regardless of which exit path is taken (suppressed, STT error,
        empty transcript, normal completion, or CancelledError).  Without this
        guarantee the task reference stays stale and the next utterance from
        the caller appears to be ignored.
        """
        try:
            # Run CPU-bound audio processing (AGC → AEC → SpectralGate →
            # noisereduce) in a thread so the event loop stays free to keep
            # receiving mic frames.  Without this, a 3-4 s utterance causes
            # noisereduce to block the loop for ~300 ms and the mic appears
            # to stop responding.
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                _AUDIO_EXECUTOR, self.pipeline.process_utterance, pcm16
            )
            if result.suppressed:
                log.info(
                    "session %s: utterance suppressed — %s",
                    self.session_id, result.suppression_reason,
                )
                self.sm.transition(CallState.LISTENING, "utterance_suppressed")
                await self._send({"type": "state", "value": "listening"})
                return
            pcm16 = self.pipeline.audio_to_pcm16(result.audio)

            self.sm.transition(CallState.THINKING, "utterance_received")
            await self._send({"type": "state", "value": "thinking"})

            # STT — cancellable; second barge-in during STT just exits cleanly
            try:
                tr = await self.deps.stt.transcribe(pcm16, self.s.input_sample_rate)
            except asyncio.CancelledError:
                log.info("session %s: STT cancelled (second barge-in)", self.session_id)
                self.sm.transition(CallState.LISTENING, "stt_cancelled")
                await self._send({"type": "state", "value": "listening"})
                raise  # re-raise so outer finally still runs
            except Exception as e:
                log.error("session %s: STT failed: %s", self.session_id, e)
                self.sm.transition(CallState.LISTENING, "stt_error")
                await self._send({"type": "state", "value": "listening"})
                return

            log.info("STT → %r (lang=%s)", tr.text[:80], tr.language)

            # Empty result after a recent barge-in → suspected false positive
            if not tr.text or len(tr.text.strip()) < 2:
                last = self.im.last_event()
                if last is not None:
                    self.im.flag_false_positive()
                self.sm.transition(CallState.LISTENING, "empty_transcript")
                await self._send({"type": "state", "value": "listening"})
                return

            # ── Number Recognition Engine: if the agent is mid-collection of a
            # consumer/mobile/OTP/meter number and this utterance looks like a
            # bare fragment of one, buffer it instead of running a full LLM
            # turn on a partial number. Only surfaces to the LLM once the
            # complete, validated number is assembled — see conversation/numbers.py.
            memory = self.manager.memory
            if memory.number_buffer.active and looks_like_number_fragment(tr.text):
                digits, complete = memory.feed_number_fragment(tr.text)
                log.info(
                    "session %s: number fragment for '%s' → %r (complete=%s)",
                    self.session_id, memory.number_buffer.field or "(just completed)",
                    digits, complete,
                )
                if not complete:
                    # Keep listening silently — do not send a partial number to
                    # the LLM, and do not ask the caller to repeat anything.
                    self.sm.transition(CallState.LISTENING, "number_fragment_buffered")
                    await self._send({"type": "state", "value": "listening"})
                    return

            await self._send({"type": "user", "text": tr.text, "lang": tr.language})

            try:
                await self._run_turn(tr.text, tr.language, peak_prob, tr.language_confidence)
            except asyncio.CancelledError:
                log.info("session %s: turn interrupted after STT (turn %d)",
                         self.session_id, self.manager.turn_no)
                self.sm.transition(CallState.LISTENING, "interrupted_after_stt")
                await self._send({"type": "state", "value": "listening"})
                raise  # re-raise so outer finally still runs

        finally:
            # Only clear the reference if it still points to THIS task.
            # After a barge-in, _on_utterance may have already assigned a NEW
            # _handle_utterance task to _active_turn_task BEFORE our cancellation
            # cleanup finishes.  Clearing unconditionally would orphan that new
            # task — the next barge-in wouldn't be able to cancel it.
            if self._active_turn_task is asyncio.current_task():
                self._active_turn_task = None

    # ── control messages ─────────────────────────────────────────────────────

    async def _on_control(self, msg: dict) -> None:
        t = msg.get("type")
        if t == "start":
            self.endpointer.reset()
        elif t == "text":
            # Typed input: barge-in first, then start the new turn
            if self.sm.is_interruptible():
                await self._trigger_barge_in()
                await asyncio.sleep(0)   # yield so cancellation propagates
            self.sm.transition(CallState.THINKING, "text_input")
            await self._send({"type": "user", "text": msg.get("text", ""), "lang": "unknown"})
            task = asyncio.create_task(
                self._run_turn_guarded(msg.get("text", ""), "unknown"),
                name=f"text_{self.session_id}",
            )
            self._active_turn_task = task
        elif t == "end":
            await self.ws.close()

    async def _run_turn_guarded(self, text: str, stt_lang: str) -> None:
        """Thin wrapper for text-mode turns with CancelledError handling."""
        try:
            await self._run_turn(text, stt_lang)
        except asyncio.CancelledError:
            self.sm.transition(CallState.LISTENING, "text_turn_interrupted")
            await self._send({"type": "state", "value": "listening"})
        finally:
            self._active_turn_task = None

    # ── turn execution ────────────────────────────────────────────────────────

    async def _run_turn(
        self, text: str, stt_lang: str,
        peak_prob: float = 1.0, language_confidence: float | None = None,
    ) -> None:
        """One full AI turn: LLM generation + TTS streaming.

        Barge-in safety:
          • asyncio.gather(producer, speak_task) means a CancelledError on THIS
            coroutine (from _active_turn_task.cancel()) cancels BOTH subtasks.
          • _turn_lock releases via async-with __aexit__ even on CancelledError,
            so the next utterance never blocks.
          • ConversationManager uses asyncio.shield() around every tool dispatch,
            so in-flight backend writes complete in the background.
        """
        async with self._turn_lock:
            queue: asyncio.Queue[TurnChunk | None] = asyncio.Queue()
            producer = speak_task = None

            async def produce() -> None:
                try:
                    async for chunk in self.manager.run_turn(
                        text, stt_lang, peak_prob, language_confidence
                    ):
                        await queue.put(chunk)
                except asyncio.CancelledError:
                    log.debug("session %s: producer cancelled", self.session_id)
                    raise
                finally:
                    try:
                        queue.put_nowait(None)   # sentinel: always signal the speaker
                    except Exception:
                        pass

            try:
                producer = asyncio.create_task(
                    produce(), name=f"producer_{self.session_id}"
                )
                self._producer_task = producer

                self.sm.transition(CallState.SPEAKING, "tts_start")
                self._speaking_since = time.monotonic()
                self._speak_peak_prob = 0.0
                self._playhead_mono = 0.0
                speak_task = asyncio.create_task(
                    self._speak_from_queue(queue),
                    name=f"speaker_{self.session_id}",
                )
                self._speak_task = speak_task

                # gather auto-cancels both when this coroutine is cancelled
                await asyncio.gather(producer, speak_task)

                # All audio SENT — but the client is still PLAYING it. Stay in
                # SPEAKING (barge-in live, cancellable) until playback drains, so
                # the caller can interrupt for the whole time they hear the agent.
                await self._drain_playback("turn")

                # Clean completion
                self.sm.transition(CallState.WAITING_FOR_USER, "turn_done")
                await self._send({"type": "memory", **self.manager.memory.snapshot()})
                await self._send({"type": "state", "value": "listening"})
                self.sm.transition(CallState.LISTENING, "after_turn")

            except asyncio.CancelledError:
                # gather already cancelled producer + speak_task.
                # State and memory sends happen in _handle_utterance's except block.
                log.info("session %s: _run_turn cancelled (turn %d)",
                         self.session_id, self.manager.turn_no)
                raise

            finally:
                self._producer_task = None
                self._speak_task = None

    # ── TTS streaming ─────────────────────────────────────────────────────────

    async def _speak_from_queue(self, queue: asyncio.Queue) -> None:
        """Drain TurnChunks from the producer and send audio to the client."""
        await self._send({"type": "state", "value": "speaking"})
        self.pipeline.notify_tts_started()
        try:
            while True:
                chunk = await queue.get()
                if chunk is None:
                    break
                if chunk.kind == "sentence":
                    await self._speak_sentence(chunk)
        finally:
            self.pipeline.notify_tts_ended()

    async def _speak_sentence(self, chunk: TurnChunk) -> None:
        """Synthesize one sentence, stream PCM to client, feed AEC reference."""
        try:
            msg = {"type": "assistant", "text": chunk.text}
            if chunk.style:
                msg["style"] = chunk.style
            await self._send(msg)
            await self._send({"type": "audio_start"})
            async for pcm in self.deps.tts.synthesize(chunk.text, chunk.language, chunk.pace):
                # Feed TTS PCM as AEC reference BEFORE sending to client so the
                # reference buffer stays synchronised with what the speaker plays.
                self.pipeline.feed_tts_reference(pcm, self.s.tts_sample_rate)
                await self.ws.send(pcm)
                self._advance_playhead(pcm)
            await self._send({"type": "audio_end"})
        except asyncio.CancelledError:
            try:
                await self._send({"type": "audio_end"})
            except Exception:
                pass
            raise
        except Exception as e:
            log.error("session %s: TTS failed: %s", self.session_id, e)

    async def _speak(self, chunks: list[TurnChunk]) -> None:
        """Speak a pre-built list of chunks (used for the greeting)."""
        self.pipeline.notify_tts_started()
        try:
            for chunk in chunks:
                await self._speak_sentence(chunk)
            # Keep SPEAKING (barge-in live) until the greeting finishes PLAYING
            # on the client, not just finishes sending.
            await self._drain_playback("greeting")
            self.sm.transition(CallState.WAITING_FOR_USER, "greeting_done")
            await self._send({"type": "state", "value": "listening"})
            self.sm.transition(CallState.LISTENING, "after_greeting")
        except asyncio.CancelledError:
            try:
                await self._send({"type": "audio_end"})
            except Exception:
                pass
            raise
        except Exception as e:
            log.error("session %s: greeting TTS failed: %s", self.session_id, e)
        finally:
            self._greeting_active = False   # greeting over — barge-in resumes normally
            self.pipeline.notify_tts_ended()

    # ── silence / no-response watchdog ─────────────────────────────────────────

    async def _silence_monitor(self) -> None:
        """Re-prompt the caller when they go quiet; disconnect after N no-responses.

        Runs for the whole call. It only acts while the agent is idle-waiting for
        the caller (LISTENING / WAITING_FOR_USER with no turn in flight), so it
        never talks over the caller or the agent. Firing a prompt also resets the
        endpointer, which recovers a session where endpointing has wedged and
        stopped flushing utterances (the "stuck after a few turns" symptom).
        """
        try:
            while True:
                await asyncio.sleep(1.0)

                # Busy (agent thinking/speaking, or a turn being handled) → not silence.
                if self.sm.state not in (CallState.LISTENING, CallState.WAITING_FOR_USER):
                    self._last_activity = time.monotonic()
                    continue
                if self._active_turn_task and not self._active_turn_task.done():
                    self._last_activity = time.monotonic()
                    continue

                if time.monotonic() - self._last_activity < self.s.silence_prompt_seconds:
                    continue

                await self._fire_silence_prompt()

                # Final prompt disconnects the call — stop monitoring.
                if self._no_response_count >= self.s.silence_max_prompts:
                    break
        except asyncio.CancelledError:
            pass
        except Exception as e:  # never let the watchdog crash the session silently
            log.error("session %s: silence monitor error: %s", self.session_id, e)

    async def _fire_silence_prompt(self) -> None:
        """Speak one re-prompt (or the final closing) and return to listening."""
        self._no_response_count += 1
        final = self._no_response_count >= self.s.silence_max_prompts
        chunk = (self.manager.no_response_closing() if final
                 else self.manager.silence_nudge())
        log.info(
            "session %s: no-response prompt %d/%d%s (lang=%s)",
            self.session_id, self._no_response_count, self.s.silence_max_prompts,
            " — disconnecting" if final else "", chunk.language,
        )

        self.sm.transition(CallState.SPEAKING, "silence_prompt")
        self._speaking_since = time.monotonic()
        self._speak_peak_prob = 0.0
        self._playhead_mono = 0.0
        task = asyncio.create_task(
            self._speak_prompt(chunk, final=final), name=f"prompt_{self.session_id}"
        )
        self._speak_task = task
        try:
            await task
        except asyncio.CancelledError:
            # Caller barged in over the prompt — they're back; barge-in handling
            # already moved the state on, so just swallow it here.
            pass

    async def _speak_prompt(self, chunk: TurnChunk, final: bool) -> None:
        """Synthesize a silence prompt, then either disconnect or resume listening."""
        self.pipeline.notify_tts_started()
        try:
            await self._speak_sentence(chunk)
            await self._drain_playback("silence_prompt")
            if final:
                self.sm.transition(CallState.WAITING_FOR_USER, "no_response_disconnect")
                await self.ws.close()
                return
            self.sm.transition(CallState.WAITING_FOR_USER, "silence_prompt_done")
            await self._send({"type": "state", "value": "listening"})
            self.sm.transition(CallState.LISTENING, "after_silence_prompt")
            # Recover a possibly-wedged endpointer so the next utterance flushes.
            self.endpointer.reset()
            self._last_activity = time.monotonic()
        except asyncio.CancelledError:
            try:
                await self._send({"type": "audio_end"})
            except Exception:
                pass
            raise
        except Exception as e:
            log.error("session %s: silence prompt TTS failed: %s", self.session_id, e)
        finally:
            self.pipeline.notify_tts_ended()

    # ── helpers ───────────────────────────────────────────────────────────────

    def _advance_playhead(self, pcm: bytes) -> None:
        """Advance the server's copy of the client playback clock as PCM is sent.

        Mirrors the browser's Web Audio scheduler exactly:
            playTime = max(playTime, currentTime + 0.04); playTime += duration
        PCM is 16-bit mono at tts_sample_rate, so duration = bytes / 2 / rate.
        """
        now = time.monotonic()
        dur = len(pcm) / 2.0 / self.s.tts_sample_rate
        start = max(self._playhead_mono, now + 0.04)
        self._playhead_mono = start + dur

    async def _drain_playback(self, reason: str) -> None:
        """Block (cancellably) until the client has finished PLAYING streamed audio.

        Keeps the call in SPEAKING so barge-in stays live for the entire time the
        caller can hear the agent. A barge-in cancels the enclosing turn task,
        which cancels this sleep — so an interruption during the drain is handled
        exactly like one during synthesis.
        """
        remaining = self._playhead_mono - time.monotonic()
        if remaining > 0.05:
            log.info(
                "session %s: draining playback %.2fs (%s) — barge-in stays live",
                self.session_id, remaining, reason,
            )
            await asyncio.sleep(remaining)

    async def _send(self, obj: dict) -> None:
        try:
            await self.ws.send(json.dumps(obj, ensure_ascii=False))
        except (RuntimeError, websockets.exceptions.ConnectionClosed):
            pass  # socket already closed / client hung up mid-teardown
        except Exception:
            # Any transport-level disconnect (connection closing) during teardown
            # must not surface as an unretrieved task exception. _send is
            # best-effort telemetry; if the socket is gone there is nothing to do.
            pass


class WebSocketServer:
    def __init__(self, host, port, deps):
        self.host = host
        self.port = port
        self.deps = deps

    async def handle_connection(self, websocket):
        connection_id = uuid.uuid4().hex[:12]
        remote_ip, remote_port = websocket.remote_address if websocket.remote_address else ("UNKNOWN", 0)
        log.info(f"Connection attempt from {remote_ip}:{remote_port}")
        
        # Wait for initial metadata (Exotel way)
        try:
            first_message = await websocket.recv()
            if isinstance(first_message, str):
                if len(first_message) > 2 and first_message.startswith('"') and first_message.endswith('"'):
                    inner_content = first_message[1:-1]
                    metadata_data = ast.literal_eval(inner_content)
                    log.info(f"Initial metadata received: {metadata_data}")
                else:
                    log.error(f"Unexpected first message format: {first_message}")
                    await websocket.close()
                    return
            else:
                log.error(f"Unexpected first message type: {type(first_message)}")
                await websocket.close()
                return
        except websockets.exceptions.ConnectionClosed:
            return
        except Exception as e:
            log.error(f"Error receiving initial metadata: {e}")
            await websocket.close()
            return
            
        session = VoiceSession(websocket, self.deps, metadata=metadata_data)
        await session.run()

async def serve(host, port, deps):
    server = WebSocketServer(host, port, deps)
    async with websockets.serve(server.handle_connection, host, port):
        await asyncio.Future()  # run forever
