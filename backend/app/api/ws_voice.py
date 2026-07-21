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

import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..audio.endpointing import Endpointer, EventType
from ..audio.pipeline import AudioPipeline
from ..audio.output_loudness import OutputLoudness
from ..audio.vad import SileroVAD
from ..barge_in.manager import InterruptionManager
from ..config import Settings
from ..conversation.manager import ConversationManager, TurnChunk
from ..conversation.numbers import looks_like_number_fragment, spoken_to_digits
from ..conversation.state import CallState, CallStateMachine

log = logging.getLogger(__name__)
router = APIRouter()

# Live sessions keyed by telephony call_sid, so an out-of-band keypad webhook
# (Exotel Gather/Passthru → /exotel/dtmf) can deliver digits into the right
# in-progress call. Only Exotel legs (which have a call_sid) are registered.
_SESSIONS_BY_CALL: dict[str, "VoiceSession"] = {}


def session_for_call(call_sid: str) -> "VoiceSession | None":
    return _SESSIONS_BY_CALL.get(call_sid) if call_sid else None

# Shared thread-pool for CPU-bound audio processing (AGC, spectral gate, AEC,
# noisereduce).  Running these synchronously in the event loop blocks the WS
# receiver and makes the mic appear to freeze on longer utterances.
_AUDIO_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="audio")


class VoiceSession:
    def __init__(self, ws: WebSocket, deps):
        self.ws = ws
        self.deps = deps
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
        self.vad = SileroVAD(
            self.s.vad_threshold, ort_session=getattr(deps, "vad_session", None)
        )
        self.endpointer = Endpointer(self.s, self.vad)
        # Voice locking: on a phone leg (Exotel) the audio environment is stable,
        # so lock onto the caller's voice; browser mics keep the config default.
        is_telephony = bool(getattr(ws, "is_telephony", False))
        self.pipeline = AudioPipeline(
            self.s, self.session_id,
            force_speaker_verify=True if is_telephony else None,
        )

        # OUTPUT loudness leveling — steadies the agent's own volume between
        # sentences (Sarvam returns each at a slightly different level). Gain is
        # one-per-sentence so there is no pumping and no added latency; the same
        # leveled PCM feeds both the caller and the AEC reference.
        self._loudness = OutputLoudness(
            max_gain=self.s.tts_loudness_max_gain,
            min_gain=self.s.tts_loudness_min_gain,
            silence_rms=self.s.tts_loudness_silence_rms,
            limiter_ceiling=self.s.tts_loudness_limiter_ceiling,
            sample_rate=self.s.tts_sample_rate,
            attack_ms=self.s.tts_loudness_attack_ms,
            release_ms=self.s.tts_loudness_release_ms,
            avg_ms=self.s.tts_loudness_avg_ms,
            window_ms=self.s.tts_loudness_window_ms,
        ) if self.s.tts_loudness_normalize else None

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

        # Per-turn latency breakdown (utterance → first audio byte), logged once
        # per turn so slow stages are visible in production; _lat_history feeds
        # the per-call summary at teardown.
        self._lat: dict = {}
        self._lat_history: list[dict] = []

        # Streaming-STT early flush bookkeeping (one flush per silence window).
        self._early_flush_sent: bool = False

        # Streaming STT (optional, Settings.stt_streaming_enabled): transcribes
        # while the caller is still speaking. REST remains the fallback.
        self._stt_stream = None
        if getattr(self.s, "stt_streaming_enabled", False):
            try:
                from ..providers.sarvam_stt_stream import SarvamSTTStream
                self._stt_stream = SarvamSTTStream(
                    self.s, lambda: self.manager.memory.language)
            except Exception as e:
                log.warning("session %s: streaming STT unavailable (%s) — REST only",
                            self.session_id, e)

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def run(self) -> None:
        await self.ws.accept()
        await self._send({"type": "ready", "session_id": self.session_id})
        log.info("session %s: call started", self.session_id)

        # CALLER-ID: record the number the call arrived from, ALWAYS (recognized
        # or not). It rides along on every ops WhatsApp ticket next to the
        # registered mobile, and is forwarded on new-connection requests.
        caller_mobile = self._caller_mobile()
        self.manager.memory.caller_number = caller_mobile

        # Register for out-of-band keypad delivery (Exotel Gather → /exotel/dtmf).
        self._call_sid = getattr(self.ws, "call_sid", None)
        if self._call_sid:
            _SESSIONS_BY_CALL[self._call_sid] = self

        # CALLER-ID RECOGNITION: if the call arrives from a registered mobile
        # (Exotel `from` on the phone leg, or ?from= on the browser demo), look
        # the subscriber up, preload their verified identity, and greet by name.
        caller_first = self.manager.recognize_caller(caller_mobile)
        if caller_first:
            log.info("session %s: recognized caller — greeting %s by name",
                     self.session_id, caller_first)
            # Push the recognized identity to the UI immediately (before any turn).
            await self._send({"type": "memory", **self.manager.memory.snapshot()})

        # Greeting (personalized when the caller was recognized)
        greeting = self.manager.greeting(caller_first)
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

        # Open the streaming-STT socket while the greeting plays (free time).
        if self._stt_stream is not None:
            asyncio.create_task(self._stt_stream.start_if_needed(),
                                name=f"sttstream_{self.session_id}")

        try:
            while True:
                msg = await self.ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                # DUAL INPUT: keypad events surfaced by the Exotel transport.
                if msg.get("type") == "dtmf":
                    await self._on_dtmf(msg.get("digit", ""))
                    continue
                if (data := msg.get("bytes")) is not None:
                    await self._on_audio(data)
                elif (text := msg.get("text")) is not None:
                    await self._on_control(json.loads(text))
        except WebSocketDisconnect:
            pass
        finally:
            await self._teardown()

    async def _teardown(self) -> None:
        cid = getattr(self, "_call_sid", None)
        if cid and _SESSIONS_BY_CALL.get(cid) is self:
            _SESSIONS_BY_CALL.pop(cid, None)
        self.sm.transition(CallState.IDLE, "call_ended")
        for t in (
            self._active_turn_task,
            self._producer_task,
            self._speak_task,
            self._silence_task,
        ):
            if t and not t.done():
                t.cancel()
        self.pipeline.reset()
        if self._stt_stream is not None:
            try:
                await self._stt_stream.close()
            except Exception:
                pass
        self._log_latency_summary()
        self.im.summary_log()
        log.info(
            "session %s closed (turns=%d, interruptions=%d)",
            self.session_id,
            self.manager.turn_no,
            self.im.total_interruptions,
        )

    # ── inbound audio ────────────────────────────────────────────────────────

    async def _on_audio(self, pcm16: bytes) -> None:
        self._frames_rx += 1
        if self._frames_rx == 1:
            log.info("session %s: mic audio flowing", self.session_id)

        # STREAMING STT: feed every frame to Sarvam as it arrives, so the
        # transcript is mostly done before the caller even stops speaking.
        # Non-blocking; any stream failure silently reverts to REST.
        # EXCEPT during the opening greeting: barge-in is suppressed then, and the
        # cold AEC lets the greeting echo into the mic — feeding it would leave
        # 3-4 s of echo in the STT buffer and stall the caller's FIRST finalize
        # (the 2-3 s first-turn lag). The buffer is cleared when the greeting ends.
        if (self._stt_stream is not None and not self._stt_stream.disabled
                and not self._greeting_active):
            self._stt_stream.feed(pcm16)

        peak = float(np.abs(np.frombuffer(pcm16, dtype=np.int16)).max() / 32768.0)
        self._peak = max(self._peak, peak)
        if self._frames_rx % 125 == 0:
            log.info(
                "mic peak (last 5s): %.3f  %s",
                self._peak,
                "— SILENCE" if self._peak < 0.02 else "",
            )
            self._peak = 0.0

        # KEY FIX: pass speaking=True during TTS so Endpointer applies the
        # conservative bargein_min_speech_ms threshold, reducing false positives
        # from echo, fan noise, and keyboard clicks during agent speech.
        is_speaking = self.sm.is_speaking()

        # Dynamic VAD threshold: raise during TTS to further suppress echo barge-ins.
        # The base threshold is restored as soon as TTS ends.
        boost = self.s.bargein_vad_threshold_boost if is_speaking else 0.0
        self.vad.threshold = self._vad_base_threshold + boost

        # ADAPTIVE ENDPOINTING: while a number is being collected, callers pause
        # between digit groups — allow a longer silence before ending the
        # utterance. Normal turns keep the fast cutoff for snappy replies.
        self.endpointer.end_silence_ms = (
            self.s.vad_end_silence_number_ms
            if self.manager.memory.number_buffer.active
            else self.s.vad_end_silence_ms
        )

        for event in self.endpointer.feed(pcm16, speaking=is_speaking):
            if event.type is EventType.SPEECH_START:
                await self._on_speech_start()
            elif event.type is EventType.UTTERANCE:
                self._early_flush_sent = False
                await self._on_utterance(event.pcm16, event.peak_prob)

        # LATENCY — streaming-STT EARLY FLUSH: the endpointer waits out the full
        # vad_end_silence hangover (400 ms) before firing UTTERANCE, but Sarvam
        # can start finalizing the moment the caller plausibly stopped. Once
        # silence inside the current utterance crosses stt_early_flush_silence_ms
        # (default 200), send the flush NOW — the transcript lands while the
        # hangover is still ticking, so finalize() after UTTERANCE is nearly
        # instant. If the caller resumes, the next segment appends harmlessly.
        if self._stt_stream is not None and not self._stt_stream.disabled:
            sil = self.endpointer.silence_ms
            if sil < self.s.stt_early_flush_silence_ms:
                self._early_flush_sent = False          # speech (re)started
            elif self.endpointer.in_speech and not self._early_flush_sent:
                self._early_flush_sent = True
                self._stt_stream.early_flush()

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
                self.session_id,
                self.vad.last_prob,
                self.vad.threshold,
            )

    async def _on_speech_start(self) -> None:
        # NOTE: deliberately does NOT reset the silence watchdog. VAD triggers on
        # ANY sound — TV, background chatter, noise — and resetting here meant a
        # noisy room deferred the no-response disconnect forever while the agent
        # kept re-asking "how can I help". Only a VALIDATED caller utterance
        # (post noise-gate, post voice-lock, post STT) counts as activity now;
        # mid-utterance deferral is handled by Endpointer.in_speech in the monitor.
        log.debug(
            "session %s: speech_start (state=%s)", self.session_id, self.sm.state.value
        )
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
            log.debug(
                "session %s: speech in non-interruptible state — ignored",
                self.session_id,
            )
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
                    self.session_id,
                    since_ms,
                    self.s.bargein_tts_grace_ms,
                )
                return
        if self.im.should_interrupt(self.sm.state, 0.0):
            await self._trigger_barge_in()
        else:
            log.info(
                "session %s: barge-in suppressed by cooldown/debounce", self.session_id
            )

    async def _on_utterance(self, pcm16: bytes, peak_prob: float = 1.0) -> None:
        """Full utterance available — launch a handler task.

        The silence watchdog is NOT reset here: this utterance may still be
        background noise. It resets only after the pipeline + STT confirm real
        caller speech (see _handle_utterance)."""
        # GREETING GUARD: while the opening greeting is still playing, barge-in
        # is deliberately suppressed (the cold AEC lets the greeting echo back
        # into the mic). Starting a turn from that echo would synthesize a reply
        # that plays OVER the greeting — the "dual voice / fluttering" at call
        # start. Drop utterances until the greeting finishes; the caller's real
        # first turn is captured immediately after.
        if self._greeting_active:
            log.info("session %s: utterance during greeting ignored (prevents "
                     "overlap/dual-voice)", self.session_id)
            return
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
            self.session_id,
            state_before.value,
            self.manager.turn_no,
            self.im.total_interruptions,
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
        stt_task: Optional[asyncio.Task] = None
        try:
            self._lat = {"t0": time.monotonic()}
            # LATENCY — the streaming-STT finalize needs no cleaned audio (Sarvam
            # already has every raw frame), so it starts NOW and overlaps the
            # CPU-bound pipeline below instead of waiting behind it.
            if self._stt_stream is not None and not self._stt_stream.disabled:
                stt_task = asyncio.create_task(
                    self._stt_stream.finalize(),
                    name=f"stt_final_{self.session_id}",
                )
            # Run CPU-bound audio processing (AGC → AEC → SpectralGate →
            # noisereduce) in a thread so the event loop stays free to keep
            # receiving mic frames.  Without this, a 3-4 s utterance causes
            # noisereduce to block the loop for ~300 ms and the mic appears
            # to stop responding.
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                _AUDIO_EXECUTOR, self.pipeline.process_utterance, pcm16
            )
            self._lat["pipe_ms"] = (time.monotonic() - self._lat["t0"]) * 1000
            if result.suppressed:
                log.info(
                    "session %s: utterance suppressed — %s",
                    self.session_id,
                    result.suppression_reason,
                )
                # Consume (and discard) the streamed transcript so this noise
                # window's text can never leak into the NEXT turn's finalize.
                if stt_task is not None:
                    try:
                        await stt_task
                    except Exception:
                        pass
                self._lat = {}      # no turn ran — a stale timer would corrupt
                self.sm.transition(CallState.LISTENING, "utterance_suppressed")
                await self._send({"type": "state", "value": "listening"})
                return
            pcm16 = self.pipeline.audio_to_pcm16(result.audio)

            self.sm.transition(CallState.THINKING, "utterance_received")
            await self._send({"type": "state", "value": "thinking"})

            # STT — cancellable; second barge-in during STT just exits cleanly.
            # Streaming path first (transcript usually ready already — finalize
            # started before the pipeline and the early flush landed during the
            # endpoint hangover); REST fallback. stt_ms measures only the wait
            # AFTER the pipeline, so the stage breakdown stays additive.
            t_stt = time.monotonic()
            try:
                tr = None
                if stt_task is not None:
                    tr = await stt_task
                    stt_task = None
                    if tr is not None:
                        self._lat["stt_stream"] = True
                if tr is None:
                    tr = await self.deps.stt.transcribe(pcm16, self.s.input_sample_rate)
                self._lat["stt_ms"] = (time.monotonic() - t_stt) * 1000
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
                self._lat = {}      # no turn ran — drop the stale timer
                self.sm.transition(CallState.LISTENING, "empty_transcript")
                await self._send({"type": "state", "value": "listening"})
                return

            # VALIDATED caller speech (survived noise gate, voice lock, and STT)
            # — only now does the silence/no-response watchdog reset. Background
            # noise and rejected speakers no longer keep the call alive forever.
            self._last_activity = time.monotonic()
            self._no_response_count = 0

            # ── Number Recognition Engine: if the agent is mid-collection of a
            # account/mobile/OTP number and this utterance looks like a
            # bare fragment of one, buffer it instead of running a full LLM
            # turn on a partial number. Only surfaces to the LLM once the
            # complete, validated number is assembled — see conversation/numbers.py.
            memory = self.manager.memory
            # While actively collecting a number, treat a digit-heavy utterance as
            # a fragment even if the noisy code-mix STT wrapped it in stray words
            # ("...7 2 6 7 8 5 Noise 755", "72 Paytm 650755"): spoken_to_digits
            # ignores the non-digit noise, so we still capture the digits instead
            # of running a full LLM turn that assembles a wrong number.
            if memory.number_buffer.active and (
                    looks_like_number_fragment(tr.text)
                    or len(spoken_to_digits(tr.text)) >= 3):
                from ..conversation.numbers import (number_type, group_for_readback,
                                                    mask_digits)
                field = memory.number_buffer.field
                prev_len = len(memory.number_buffer.digits)
                conf = tr.language_confidence if tr.language_confidence is not None else 1.0
                digits, complete = memory.feed_number_fragment(tr.text, confidence=conf)
                t = number_type(field)
                exp = t.exact if (t and t.exact) else memory.number_buffer.expected_len
                # LIVE CAPTURE — push the masked/grouped progress to the UI. Non-
                # blocking telemetry; the audio path is untouched.
                await self._send({
                    "type": "number",
                    "stage": "validating" if complete else "awaiting",
                    "field": field,
                    "label": (t.label if t else field) or "",
                    "masked": mask_digits(digits, exp),
                    "grouped": group_for_readback(digits, field),
                    "count": len(digits),
                    "expected": exp,
                    "confidence": conf,
                    "complete": complete,
                })
                log.info(
                    "session %s: number fragment for '%s' → %r (complete=%s)",
                    self.session_id,
                    field or "(just completed)",
                    digits,
                    complete,
                )
                if not complete:
                    # HUMAN "NOTING": if this fragment added digits, read the
                    # running total back and invite the caller to continue — like
                    # an executive jotting a number. Only when it actually grew
                    # (never on noise / a no-op), and never a full-number confirm
                    # (that happens once, at completion).
                    grew = len(digits) > prev_len and bool(digits)
                    if grew and getattr(self.s, "number_capture_ack_enabled", True):
                        ack = self.manager.number_ack_line(digits, field)
                        if ack is not None:
                            self.sm.transition(CallState.SPEAKING, "number_ack")
                            self.pipeline.notify_tts_started()
                            try:
                                await self._send({"type": "state", "value": "speaking"})
                                await self._speak_sentence(ack)
                                await self._drain_playback("number_ack")
                            except Exception as e:                    # noqa: BLE001
                                log.warning("session %s: number-ack TTS failed: %s",
                                            self.session_id, e)
                            finally:
                                self.pipeline.notify_tts_ended()
                    # Do not send a partial number to the LLM; keep listening.
                    self._lat = {}  # no turn ran — drop the stale timer
                    self.sm.transition(CallState.LISTENING, "number_fragment_buffered")
                    await self._send({"type": "state", "value": "listening"})
                    return

            await self._send({"type": "user", "text": tr.text, "lang": tr.language})

            try:
                await self._run_turn(
                    tr.text, tr.language, peak_prob, tr.language_confidence
                )
            except asyncio.CancelledError:
                log.info(
                    "session %s: turn interrupted after STT (turn %d)",
                    self.session_id,
                    self.manager.turn_no,
                )
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
                await asyncio.sleep(0)  # yield so cancellation propagates
            self.sm.transition(CallState.THINKING, "text_input")
            await self._send(
                {"type": "user", "text": msg.get("text", ""), "lang": "unknown"}
            )
            task = asyncio.create_task(
                self._run_turn_guarded(msg.get("text", ""), "unknown"),
                name=f"text_{self.session_id}",
            )
            self._active_turn_task = task
        elif t == "dtmf":
            # Browser/test keypad → same path as an Exotel keypad event.
            await self._on_dtmf(str(msg.get("digit", "")))
        elif t == "end":
            await self.ws.close()

    async def _on_dtmf(self, digit: str) -> None:
        """Handle one keypad press (DUAL-INPUT number capture).

        Digits/backspace update the live buffer silently (banker's-IVR style —
        no per-key chatter). When the number auto-completes at its exact length,
        or the caller presses the submit key on a valid number, we run one normal
        turn so the agent reads it back and confirms in the caller's language.
        The audio pipeline is never blocked — this only runs between turns."""
        if not getattr(self.s, "dtmf_enabled", True) or not digit:
            return
        memory = self.manager.memory
        if not memory.number_buffer.active:
            # No number is being collected — a stray keypress. Ignore rather than
            # guess which identifier the caller means.
            log.info("session %s: DTMF %r ignored — no active capture",
                     self.session_id, digit)
            return

        res = memory.feed_dtmf_digit(
            digit,
            submit_key=getattr(self.s, "dtmf_submit_key", "#"),
            backspace_key=getattr(self.s, "dtmf_backspace_key", "*"))
        if res is None:
            return
        self.sm.transition(CallState.NUMBER_CAPTURE, f"dtmf:{res.action}")

        # Live UI: reflect keypad progress + input mode + validation.
        snap = memory.number_buffer.snapshot() if memory.number_buffer.active else {}
        await self._send({
            "type": "number",
            "stage": "validating" if (res.complete or res.submitted) else "awaiting",
            "field": snap.get("field"),
            "label": snap.get("label", ""),
            "masked": snap.get("masked", res.digits),
            "grouped": snap.get("grouped", res.digits),
            "count": len(res.digits),
            "expected": snap.get("expected"),
            "confidence": 1.0,
            "complete": res.complete,
            "input_mode": snap.get("input_mode", "dtmf"),
            "prefix_ok": snap.get("prefix_ok", True),
            "valid": res.valid,
            "action": res.action,
        })
        log.info("session %s: DTMF %r → action=%s digits=%r complete=%s valid=%s",
                 self.session_id, digit, res.action, res.digits, res.complete, res.valid)

        # Completed (or explicitly submitted & valid) → confirm via a real turn.
        if res.complete or (res.submitted and res.valid):
            if self.sm.is_interruptible():
                await self._trigger_barge_in()
                await asyncio.sleep(0)
            self.sm.transition(CallState.THINKING, "dtmf_complete")
            from ..conversation.numbers import group_for_readback
            spoken = group_for_readback(res.digits) or res.digits
            await self._send({"type": "user", "text": spoken, "lang": "unknown"})
            task = asyncio.create_task(
                self._run_turn_guarded(spoken, "unknown"),
                name=f"dtmf_turn_{self.session_id}")
            self._active_turn_task = task
        elif res.action in ("cancel", "restart"):
            # Keep the caller informed on a destructive keypad action.
            await self._send({"type": "state", "value": "listening"})
            self.sm.transition(CallState.LISTENING, f"dtmf_{res.action}")
        else:
            self.sm.transition(CallState.LISTENING, "dtmf_digit")
            await self._send({"type": "state", "value": "listening"})

    async def inject_dtmf(self, digits: str) -> int:
        """Deliver keypad digits collected out-of-band (Exotel Gather / Passthru
        webhook → /exotel/dtmf). Each character is fed through the same handler
        as a streamed keypress, so control keys (*, #) work identically. Returns
        the count of characters processed."""
        n = 0
        for ch in (digits or ""):
            if ch.strip():
                await self._on_dtmf(ch)
                n += 1
        return n

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
        self,
        text: str,
        stt_lang: str,
        peak_prob: float = 1.0,
        language_confidence: float | None = None,
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
                        queue.put_nowait(None)  # sentinel: always signal the speaker
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

                # ── agent-initiated hangup (end_call tool after the closing) ──
                if getattr(self.manager, "end_call_requested", False):
                    log.info("session %s: agent ended the call (end_call)",
                             self.session_id)
                    self.sm.transition(CallState.WAITING_FOR_USER, "agent_end_call")
                    await self._send({"type": "call_end"})
                    await self.ws.close()
                    return

                # ── agent-initiated escalation to a senior executive ──
                if getattr(self.manager, "transfer_requested", None) is not None:
                    ctx = self.manager.transfer_requested
                    self.manager.transfer_requested = None
                    await self._handle_transfer(ctx)
                    return

                # Clean completion
                self.sm.transition(CallState.WAITING_FOR_USER, "turn_done")
                await self._send({"type": "memory", **self.manager.memory.snapshot()})
                await self._send({"type": "state", "value": "listening"})
                self.sm.transition(CallState.LISTENING, "after_turn")

            except asyncio.CancelledError:
                # gather already cancelled producer + speak_task.
                # State and memory sends happen in _handle_utterance's except block.
                log.info(
                    "session %s: _run_turn cancelled (turn %d)",
                    self.session_id,
                    self.manager.turn_no,
                )
                raise

            finally:
                self._producer_task = None
                self._speak_task = None

    # ── TTS streaming ─────────────────────────────────────────────────────────

    async def _speak_from_queue(self, queue: asyncio.Queue) -> None:
        """Drain TurnChunks from the producer and send audio to the client."""
        await self._send({"type": "state", "value": "speaking"})
        self.pipeline.notify_tts_started()
        last_status = "speaking"
        try:
            while True:
                chunk = await queue.get()
                if chunk is None:
                    break
                # Non-audio status pings (e.g. "checking" while a tool lookup runs)
                # drive the client's live activity indicator without touching the
                # audio path. Barge-in stays exactly as responsive.
                if chunk.kind == "status":
                    if chunk.text and chunk.text != last_status:
                        last_status = chunk.text
                        await self._send({"type": "state", "value": chunk.text})
                    continue
                if chunk.kind == "sentence":
                    # Restore "speaking" if a status ping (e.g. "checking") was the
                    # last thing the client heard about, so the indicator flips back
                    # the instant real audio resumes.
                    if last_status != "speaking":
                        last_status = "speaking"
                        await self._send({"type": "state", "value": "speaking"})
                    # LATENCY — sentence N+1 prefetch: anything already waiting
                    # in the queue starts synthesizing NOW, in parallel with
                    # sentence N's synthesis + playback. In-flight de-dup in
                    # SarvamTTS means the later synthesize() joins the same
                    # network call instead of duplicating it — consecutive
                    # sentences flow with no audible HTTP round-trip gap.
                    for pending in list(queue._queue):  # noqa: SLF001
                        if (pending is not None and pending.kind == "sentence"
                                and pending.text):
                            self.deps.tts.prefetch(
                                pending.text, pending.language, pending.pace)
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
            t_tts = time.monotonic()
            if self._loudness is not None:
                self._loudness.start_sentence()
            async for pcm in self.deps.tts.synthesize(
                chunk.text, chunk.language, chunk.pace
            ):
                # Steady the per-sentence loudness BEFORE anything downstream sees
                # it, so the caller and the AEC reference get the same leveled audio.
                if self._loudness is not None:
                    pcm = self._loudness.process(pcm)
                # Feed TTS PCM as AEC reference BEFORE sending to client so the
                # reference buffer stays synchronised with what the speaker plays.
                self.pipeline.feed_tts_reference(pcm, self.s.tts_sample_rate)
                await self.ws.send_bytes(pcm)
                self._advance_playhead(pcm)
                self._log_first_audio_latency(t_tts)
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
            self._greeting_active = False  # greeting over — barge-in resumes normally
            # FIRST-TURN LATENCY: drop anything the mic captured during the
            # greeting (its own echo) so the caller's first utterance finalizes
            # clean, not behind seconds of buffered greeting audio. Also reset the
            # endpointer, which may be mid-"speech" on that echo.
            if self._stt_stream is not None and not self._stt_stream.disabled:
                self._stt_stream.reset_buffer()
            self.endpointer.reset()
            self._early_flush_sent = False
            self.pipeline.notify_tts_ended()

    # ── AI → human escalation / call transfer ─────────────────────────────────

    async def _handle_transfer(self, ctx) -> None:
        """Seamless hand-off to a senior executive. Speaks the warm multilingual
        connecting message, streams the UI stages, then performs the actual leg
        transfer via the isolated TransferService. On failure the caller is
        apologised to and offered a callback — never left in silence.

        The summary was already built when the tool fired (no blocking work here),
        so the only added latency is the spoken connecting line the caller hears
        anyway. Streaming stays uninterrupted right up to the transfer."""
        t0 = time.monotonic()
        log.info("session %s: TRANSFER REQUESTED — reason=%s category=%s priority=%s",
                 self.session_id, ctx.escalation_reason, ctx.issue_category,
                 ctx.issue_priority)

        # UI stages: escalating → summarizing → preparing
        await self._send({"type": "transfer", "stage": "escalating",
                          "reason": ctx.escalation_reason,
                          "category": ctx.issue_category,
                          "priority": ctx.issue_priority,
                          "complaint_id": ctx.complaint_id})
        await self._send({"type": "transfer", "stage": "summarizing",
                          "summary": ctx.summary})

        # Speak the warm connecting line (guaranteed multilingual, caller's lang).
        await self._send({"type": "transfer", "stage": "preparing",
                          "executive": self.s.transfer_executive_label})
        self.pipeline.notify_tts_started()
        try:
            await self._speak_sentence(self.manager.transfer_intro_line())
            await self._drain_playback("transfer")
        except Exception as e:                       # noqa: BLE001
            log.warning("session %s: transfer intro TTS failed: %s", self.session_id, e)
        finally:
            self.pipeline.notify_tts_ended()

        # Perform the real transfer (Exotel; simulation when creds/CallSid absent).
        await self._send({"type": "transfer", "stage": "connecting"})
        ctx.call_sid = getattr(self.ws, "call_sid", None)
        ctx.from_number = getattr(self.ws, "from_number", None) or ctx.caller_number
        transfer = getattr(self.deps, "transfer", None)
        try:
            result = await transfer.transfer(ctx) if transfer is not None else None
        except Exception as e:                       # noqa: BLE001
            log.exception("session %s: transfer service error", self.session_id)
            result = None

        if result is not None and result.ok:
            log.info("session %s: TRANSFER %s in %.0fms (exec=%s ref=%s)",
                     self.session_id, result.status.value,
                     (time.monotonic() - t0) * 1000, result.executive,
                     result.reference)
            await self._send({"type": "transfer", "stage": "completed",
                              "executive": result.executive,
                              "reference": result.reference,
                              "reason": ctx.escalation_reason,
                              "category": ctx.issue_category,
                              "priority": ctx.issue_priority,
                              "complaint_id": ctx.complaint_id,
                              "summary": ctx.summary})
            self.sm.transition(CallState.WAITING_FOR_USER, "transferred")
            # REAL Exotel API transfer: Exotel is now bridging the caller's
            # EXISTING leg to the executive and will close our media stream with a
            # `stop`. Closing it ourselves could cut the leg mid-bridge, so we
            # leave it open and let Exotel end it. Flow-handoff and the browser/
            # simulation path DO close here (flow needs the stream to end so the
            # Exotel Connect applet takes over).
            mode_flow = (result.detail or {}).get("mode") == "flow"
            real_api_transfer = (bool(ctx.call_sid) and not mode_flow
                                 and not str(result.reference).startswith("SIM-"))
            if real_api_transfer:
                log.info("session %s: real Exotel transfer in progress — leaving "
                         "the leg to Exotel", self.session_id)
                return
            await self._send({"type": "call_end"})
            await self.ws.close()
            return

        # ── failure: apologise + offer callback, keep the caller on the line ──
        err = (result.error if result is not None else "transfer backend unavailable")
        log.error("session %s: TRANSFER FAILED — %s", self.session_id, err)
        await self._send({"type": "transfer", "stage": "failed", "error": err})
        self.pipeline.notify_tts_started()
        try:
            for chunk in self.manager.transfer_failed_lines():
                await self._speak_sentence(chunk)
            await self._drain_playback("transfer_failed")
        except Exception as e:                       # noqa: BLE001
            log.warning("session %s: transfer-failed TTS error: %s", self.session_id, e)
        finally:
            self.pipeline.notify_tts_ended()
        # Return the caller to normal conversation (they were NOT dropped).
        self.sm.transition(CallState.WAITING_FOR_USER, "transfer_failed")
        await self._send({"type": "state", "value": "listening"})
        self.sm.transition(CallState.LISTENING, "after_transfer_failed")

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
                if self.sm.state not in (
                    CallState.LISTENING,
                    CallState.WAITING_FOR_USER,
                ):
                    self._last_activity = time.monotonic()
                    continue
                if self._active_turn_task and not self._active_turn_task.done():
                    self._last_activity = time.monotonic()
                    continue

                # Mid-utterance (caller — or a TV — currently making sound):
                # don't fire a prompt over it, but do NOT reset the clock either,
                # so continuous background noise can't defer the disconnect.
                if getattr(self.endpointer, "in_speech", False):
                    continue

                if (
                    time.monotonic() - self._last_activity
                    < self.s.silence_prompt_seconds
                ):
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
        chunk = (
            self.manager.no_response_closing()
            if final
            else self.manager.silence_nudge()
        )
        log.info(
            "session %s: no-response prompt %d/%d%s (lang=%s)",
            self.session_id,
            self._no_response_count,
            self.s.silence_max_prompts,
            " — disconnecting" if final else "",
            chunk.language,
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

    def _caller_mobile(self) -> Optional[str]:
        """Best-effort 10-digit caller mobile for caller-ID recognition.

        Sources, in order: the telephony transport's `from_number` (Exotel start
        message) or a `?from=`/`?caller=` query param on the WebSocket URL (the
        browser demo simulates an incoming number). Any country-code prefix is
        dropped to the trailing 10 digits; returns None if unusable."""
        raw = getattr(self.ws, "from_number", None)
        if not raw:
            qp = getattr(self.ws, "query_params", None)
            if qp:
                try:
                    raw = qp.get("from") or qp.get("caller")
                except Exception:
                    raw = None
        if not raw:
            return None
        digits = "".join(ch for ch in str(raw) if ch.isdigit())
        if len(digits) > 10:
            digits = digits[-10:]
        return digits if len(digits) == 10 else None

    def _log_first_audio_latency(self, t_tts_start: float) -> None:
        """Log the per-stage breakdown once, on the first audio byte of a turn.

        pipe = audio cleanup, stt = Sarvam transcription, llm = end of STT →
        first complete sentence out of Gemini, tts = first sentence → first PCM
        byte, total = caller stopped being heard → caller starts hearing us
        (excludes the end-of-speech silence hangover, which adds
        vad_end_silence_ms on top)."""
        lat = self._lat
        if not lat or "t0" not in lat or "logged" in lat or "stt_ms" not in lat:
            return
        lat["logged"] = True
        now = time.monotonic()
        total = (now - lat["t0"]) * 1000
        tts_ms = (now - t_tts_start) * 1000
        llm_ms = total - lat.get("pipe_ms", 0) - lat["stt_ms"] - tts_ms
        log.info(
            "session %s: latency pipe=%.0f stt=%.0f llm=%.0f tts=%.0f | "
            "utterance→first-audio=%.0fms (+%dms endpoint hangover)",
            self.session_id, lat.get("pipe_ms", 0), lat["stt_ms"],
            max(llm_ms, 0), tts_ms, total, int(self.endpointer.end_silence_ms),
        )
        self._lat_history.append({
            "pipe": lat.get("pipe_ms", 0.0), "stt": lat["stt_ms"],
            "llm": max(llm_ms, 0.0), "tts": tts_ms, "total": total,
        })

    def _log_latency_summary(self) -> None:
        """Per-call latency report at teardown: avg and worst per stage."""
        hist = self._lat_history
        if not hist:
            return
        n = len(hist)
        parts = []
        for k in ("pipe", "stt", "llm", "tts", "total"):
            vals = [h[k] for h in hist]
            parts.append(f"{k} avg={sum(vals)/n:.0f} max={max(vals):.0f}")
        log.info("session %s: LATENCY SUMMARY over %d turns | %s | "
                 "target total<800ms excl. endpoint (≈1200ms speech-to-speech)",
                 self.session_id, n, " | ".join(parts))

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
                self.session_id,
                remaining,
                reason,
            )
            await asyncio.sleep(remaining)

    async def _send(self, obj: dict) -> None:
        try:
            await self.ws.send_text(json.dumps(obj, ensure_ascii=False))
        except (RuntimeError, WebSocketDisconnect):
            pass  # socket already closed / client hung up mid-teardown
        except Exception:
            # Any transport-level disconnect (uvicorn ClientDisconnected,
            # starlette WebSocketDisconnect, connection closing) during teardown
            # must not surface as an unretrieved task exception. _send is
            # best-effort telemetry; if the socket is gone there is nothing to do.
            pass


@router.websocket("/ws/call")
async def ws_call(ws: WebSocket):
    await VoiceSession(ws, ws.app.state.deps).run()
