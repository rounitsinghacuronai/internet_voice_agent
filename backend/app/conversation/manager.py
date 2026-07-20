"""ConversationManager — the brain of one call.

Turn flow:
  user utterance (text + STT lang hint)
    → memory.scan_user_text (slot extraction)
    → LanguageEngine.update
    → SafetyGate (deterministic) → emergency path if tripped
    → compose system prompt (modules + language directive + memory block)
    → Gemini stream; tool_calls → ToolRegistry (gated) → loop (max rounds)
    → sentences streamed OUT as they complete (caller hears the first sentence
      while the rest is still generating)

The manager is transport-agnostic: it yields TurnChunk objects; the WS layer (or a
future Exotel adapter, or the eval harness) consumes them.

BARGE-IN SAFETY GUARANTEES (both handled here, not left to the transport layer):

  1. EAGER MEMORY COMMITS — sentences are appended to memory.history the instant
     they are generated, not after the whole turn completes. A barge-in mid-turn
     therefore discards only the unspoken tail; the caller's heard context is never
     lost.

  2. SHIELDED TOOL CALLS — every backend tool dispatch (register_complaint, OTP
     verification, etc.) is wrapped in asyncio.shield() so a barge-in CancelledError
     cannot abort a write that is already in flight. If the outer task is cancelled
     while a tool is executing, _late_tool_absorb() keeps the tool running in the
     background and absorbs its result into memory when it finishes. The next turn
     picks up the result from memory.history naturally.

  3. LANGUAGE PERSISTENCE — LanguageEngine.update() runs before any LLM generation.
     If the customer interrupted in a different language, memory.language is updated
     before the very first token of the next response is generated.

  4. TOPIC CONTINUITY — only the unspoken portion of the interrupted response is
     discarded. Completed tool results, spoken sentences, and all memory slots are
     preserved across barge-in events. The LLM context window reflects exactly what
     the customer actually heard.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import AsyncIterator

from ..config import Settings
from ..persona import get_persona
from ..prompts.loader import compose_system_prompt
from .escalation import (EscalationDecision, EscalationEngine,
                         build_escalation_summary)
from ..telephony.transfer_service import TransferContext
from ..providers.base import LLMProvider, ProviderError
from ..speech.director import detect_caller_emotion
from ..speech.pipeline import SpeechDirector
from ..speech.plan import SpeechContext, StyleName
from ..tools.registry import ToolRegistry
from . import safety
from .language import LanguageEngine
from .memory import CallMemory
from .robustness import ConfidenceEstimate, TopicStability, estimate_confidence

log = logging.getLogger(__name__)

# Primary split: sentence-ending punctuation followed by space, or immediately
# followed by the start of a new word (no space).
# Secondary split: comma/दash pause — only fires when there is already a
# meaningful chunk in the buffer (≥40 chars) so we don't create sub-second
# clips.  This gets TTS started sooner on long single-sentence responses.
_SENT_SPLIT = re.compile(
    r"(?<=[.!?।؟])\s+"            # ". Next" or "? Next" with space
    r"|(?<=[.!?।])(?=[^\s.!?।])"  # ".Next" no space (Hindi/Marathi run-on)
)
# Comma/pause split used only when buffer is long — avoids tiny clips.
_PAUSE_SPLIT = re.compile(r"(?<=[,،—])\s+")
# Yield a partial buffer at a comma only after this many chars. Kept high (160)
# so short customer-care replies are synthesised as WHOLE sentences — splitting a
# sentence into comma-delimited fragments makes each fragment a separate TTS call,
# which resets prosody and produces the clipped, choppy, robotic delivery. A whole
# sentence lets the voice carry natural intonation and emotion across it. Only a
# genuinely long run-on sentence now splits early, and only for first-audio latency.
_FORCE_FLUSH_CHARS = 160

# Tools that represent a troubleshooting attempt — each successful call that
# leaves the issue unresolved bumps the decision engine's failed-attempts count.
_TROUBLESHOOT_TOOLS = {
    "get_network_status", "get_broadband_status", "run_line_diagnostics",
    "restart_ont", "get_bill", "get_payment_status", "get_usage",
    "get_recharge_history",
}

# NOTE: all identity-bearing fixed lines (greeting, silence nudge, closings,
# safety, emergency follow-ups) live in backend/app/persona.py — the single
# source of truth for the agent's name, gender, and grammar. Nothing here may
# hard-code a name or a gendered verb form.


def _lang_for(memory_lang: str, table: dict) -> str:
    """Pick a language key that exists in `table`, defaulting to Marathi
    (the greeting language) before the caller has established one."""
    return memory_lang if memory_lang in table else "mr"


@dataclass
class TurnChunk:
    """One unit the transport speaks/displays. kind: sentence|done

    `pace` is the per-utterance Sarvam pace planned by the Human Speech Engine
    (None → provider default). `style` is the Voice Director's style label, for
    telemetry / the client UI."""
    kind: str
    text: str = ""
    language: str = "mr"   # Maharashtra deployment — Marathi is the house default
    pace: float | None = None
    style: str = ""


class ConversationManager:
    def __init__(self, settings: Settings, llm: LLMProvider, tools: ToolRegistry,
                 session_id: str = ""):
        self.s = settings
        self.llm = llm
        self.tools = tools
        # PERSONA LOCK: resolved once at session start; immutable for the call.
        self.persona = get_persona(settings)
        # Set when the model calls end_call after the official closing — the
        # transport hangs up once the final audio has finished playing.
        self.end_call_requested = False
        # True once the caller has been recognized from their registered mobile
        # (caller-ID). Drives a standing "already verified — never re-ask"
        # directive so a recognized caller is never sent through verification.
        self._caller_recognized = False
        self.memory = CallMemory(session_id=session_id)
        self.lang = LanguageEngine()
        self.topic = TopicStability()
        self.turn_no = 0
        self._last_confidence: ConfidenceEstimate | None = None
        self._last_user_text = ""
        # Sticky caller-mood tracking: one angry sentence colours the next few
        # turns (a real person doesn't reset to neutral mid-grievance), then
        # decays, and clears immediately on gratitude/relief.
        self._caller_emotion: str | None = None
        self._emotion_set_turn: int = 0

        # ── AI → human escalation ──
        # Decision engine (intent/sentiment/severity/failed-attempts/request/tool).
        self._escalation = EscalationEngine(settings)
        self._escalation_decision: EscalationDecision = EscalationDecision()
        # Set to a TransferContext when transfer_to_senior_executive fires — the
        # VoiceSession reads it after the turn's audio drains and performs the
        # actual leg transfer (mirrors end_call_requested).
        self.transfer_requested: TransferContext | None = None
        self._tools_used: list[str] = []          # every tool called this call
        self._failed_attempts: int = 0            # troubleshooting tries, unresolved
        self._last_tool_results: list[dict] = []  # previous turn's tool results

        # Human Speech Generation Engine + Voice Director. One instance per call
        # (holds the anti-repetition VariationTracker). None → raw sentence → TTS.
        self.speech: SpeechDirector | None = (
            SpeechDirector(settings, llm) if getattr(settings, "speech_enabled", True) else None
        )
        # per-turn voicing state (reset at the start of each LLM turn)
        self._turn_profile = None
        self._turn_ctx: SpeechContext | None = None
        self._turn_is_first = True
        self._turn_processed = False
        self._turn_complaints_before = 0

    # ── speech-engine helpers ─────────────────────────────────────────────────
    def _voice_fixed(self, text: str, lang: str, style: StyleName) -> TurnChunk:
        """Voice a reviewed/fixed line (greeting, safety, apology, silence prompt):
        Voice Director pace + Sarvam formatting only, wording untouched."""
        if not self.speech:
            return TurnChunk("sentence", text, lang)
        plan = self.speech.render_fixed(text, lang, style)
        return TurnChunk("sentence", plan.text, plan.language, pace=plan.pace, style=plan.style)

    def _voice(self, sentence: str, lang: str) -> TurnChunk:
        """Voice one generated sentence through the Human Speech Engine, using the
        per-turn StyleProfile decided by the Voice Director on the first sentence."""
        if not self.speech:
            return TurnChunk("sentence", sentence, lang)
        if self._turn_profile is None or self._turn_ctx is None:
            ctx = self._build_speech_ctx(sentence)
            self._turn_ctx = ctx
            self._turn_profile = self.speech.direct(ctx)
        ctx = self._turn_ctx
        ctx.is_first_utterance = self._turn_is_first
        ctx.processing = self._turn_processed
        plan = self.speech.render(sentence, self._turn_profile, ctx)
        self._turn_is_first = False
        return TurnChunk("sentence", plan.text, plan.language, pace=plan.pace, style=plan.style)

    def _build_speech_ctx(self, sentence: str) -> SpeechContext:
        return SpeechContext(
            language=self.memory.language,
            turn_no=self.turn_no,
            is_first_utterance=True,
            verified=self.memory.verified,
            asking_for_number=self.memory.field_requested_by(sentence),
            just_registered_complaint=len(self.memory.complaints) > self._turn_complaints_before,
            topic=self.topic.active,
            confidence_tier=(self._last_confidence.tier.value if self._last_confidence else "high"),
            caller_emotion=self._caller_emotion,
            processing=self._turn_processed,
            user_text=self._last_user_text,
        )

    # ── caller-sentiment tracking ─────────────────────────────────────────────
    _EMOTION_DECAY_TURNS = 3   # negative mood fades after this many quiet turns

    def _update_caller_emotion(self, user_text: str) -> None:
        sensed = detect_caller_emotion(user_text)
        if sensed == "calm":
            # gratitude/relief — grievance resolved, drop any sticky negative mood
            if self._caller_emotion:
                log.info("caller mood cleared (%s → calm)", self._caller_emotion)
            self._caller_emotion = None
        elif sensed:
            self._caller_emotion = sensed
            self._emotion_set_turn = self.turn_no
        elif (self._caller_emotion
              and self.turn_no - self._emotion_set_turn > self._EMOTION_DECAY_TURNS):
            self._caller_emotion = None          # cooled off on its own

    _MOOD_DIRECTIVES = {
        "angry": ("[CALLER MOOD: angry] The caller is upset. Stay calm and steady — "
                  "never cheerful, never defensive. Acknowledge the problem plainly in "
                  "your own words FIRST, then act immediately. Short sentences. Never "
                  "tell them to calm down, never over-apologise."),
        "frustrated": ("[CALLER MOOD: frustrated] The caller is fed up (repeat issue, "
                       "long wait). Apologise once, sincerely and specifically, take "
                       "ownership ('I'll take care of this'), and get it done — no "
                       "excuses, no process talk."),
        "worried": ("[CALLER MOOD: worried] The caller sounds anxious. Reassure "
                    "calmly and concretely — say exactly what will happen and when. "
                    "No padding, warm steady tone."),
        "elderly": ("[CALLER MOOD: elderly/unsure] Extra patience. One simple step "
                    "at a time, no jargon, gently confirm they got the important "
                    "number."),
    }

    def _mood_directive(self) -> str:
        return self._MOOD_DIRECTIVES.get(self._caller_emotion or "", "")

    # ── public API ───────────────────────────────────────────────────────────
    def recognize_caller(self, mobile: str | None) -> str | None:
        """Caller-ID recognition. If `mobile` belongs to a registered subscriber,
        preload their VERIFIED identity into call memory (caller ID is trusted —
        no further verification is asked) and return their first name so the
        greeting can address them personally. Returns None if unrecognized.

        Reads/writes gate on memory.verified, so seeding it here means a
        recognized caller can be helped immediately without re-stating identity."""
        if not mobile:
            return None
        try:
            res = self.tools.svc.verify_customer(mobile=mobile)
        except Exception as e:  # never let recognition break call start
            log.warning("caller recognition failed for %s: %s", mobile, e)
            return None
        if not res.get("verified"):
            return None
        self.memory.absorb_tool_result("verify_customer", {}, res)
        self._caller_recognized = True
        name = res.get("name") or ""
        return name.split()[0] if name else None

    def greeting(self, caller_first_name: str | None = None) -> TurnChunk:
        text = (self.persona.personal_greeting(caller_first_name)
                if caller_first_name else self.persona.greeting)
        self.memory.history.append({"role": "assistant", "content": text})
        return self._voice_fixed(text, "mr", StyleName.GREETING)

    def silence_nudge(self) -> TurnChunk:
        """Gentle re-prompt spoken when the caller has gone silent."""
        lang = _lang_for(self.memory.language, self.persona.silence_nudge)
        text = self.persona.silence_nudge[lang]
        self.memory.history.append({"role": "assistant", "content": text})
        return self._voice_fixed(text, lang, StyleName.DEFAULT)

    def no_response_closing(self) -> TurnChunk:
        """Final announcement + official closing before disconnecting on no-response."""
        lang = _lang_for(self.memory.language, self.persona.no_response_closing)
        text = self.persona.no_response_closing[lang]
        self.memory.history.append({"role": "assistant", "content": text})
        return self._voice_fixed(text, lang, StyleName.CLOSING)

    # ── escalation: fixed multilingual spoken lines (called by VoiceSession) ──
    def transfer_intro_line(self) -> TurnChunk:
        """Warm hand-off + connecting message, in the caller's current language."""
        lang = _lang_for(self.memory.language, self.persona.transfer_intro)
        text = self.persona.transfer_intro[lang]
        self.memory.history.append({"role": "assistant", "content": text})
        return self._voice_fixed(text, lang, StyleName.CLOSING)

    def transfer_failed_lines(self) -> list[TurnChunk]:
        """Spoken when the transfer could not complete: apologise + offer a
        callback, so the caller is never left in silence."""
        lang = _lang_for(self.memory.language, self.persona.transfer_failed)
        chunks: list[TurnChunk] = []
        for d in (self.persona.transfer_failed, self.persona.transfer_callback):
            text = d[lang]
            self.memory.history.append({"role": "assistant", "content": text})
            chunks.append(self._voice_fixed(text, lang, StyleName.DEFAULT))
        return chunks

    async def run_turn(
        self,
        user_text: str,
        stt_lang: str = "unknown",
        peak_prob: float = 1.0,
        language_confidence: float | None = None,
    ) -> AsyncIterator[TurnChunk]:
        self.turn_no += 1
        t0 = time.perf_counter()
        user_text = user_text.strip()
        if not user_text:
            return
        self._last_user_text = user_text
        self._update_caller_emotion(user_text)

        # DECISION ENGINE: weigh intent/sentiment/severity/failed-attempts/request
        # /tool-response and, when a human is warranted, inject a directive so the
        # LLM reliably calls transfer_to_senior_executive this turn.
        if getattr(self.s, "transfer_enabled", True):
            self._escalation_decision = self._escalation.evaluate(
                user_text, self.memory, mood=self._caller_emotion,
                failed_attempts=self._failed_attempts,
                last_tool_results=self._last_tool_results)
            if self._escalation_decision.should_transfer:
                log.info("turn %d: escalation recommended — %s (%s, %s)",
                         self.turn_no, self._escalation_decision.reason,
                         self._escalation_decision.source,
                         self._escalation_decision.priority)

        self.memory.scan_user_text(user_text)
        # Disarm number collection once its slot is filled (by ANY path — the
        # fragment buffer, scan_user_text, or a verify result). A stale armed
        # buffer kept every later utterance on the slow 900 ms number-dictation
        # endpointing, adding ~500 ms to every turn of the rest of the call.
        nb = self.memory.number_buffer
        if nb.active and nb.field and (
                getattr(self.memory, nb.field, None)
                or (nb.field == "account_no" and self.memory.verified)):
            nb.clear()
        active_lang = self.lang.update(user_text, stt_lang)
        self.memory.language = active_lang
        self.memory.history.append({"role": "user", "content": user_text})

        # Robustness layer: composite confidence proxy (VAD peak-prob + Sarvam's
        # language_probability — there is no true per-word STT confidence) and
        # topic-stability tracking, both feed the system prompt for this turn.
        self._last_confidence = estimate_confidence(peak_prob, language_confidence)
        self.topic.update(user_text)

        # ── deterministic emergency fast-path ──
        verdict = safety.assess(user_text)
        if verdict.emergency:
            async for chunk in self._emergency(verdict, user_text):
                yield chunk
            return

        # ── normal LLM turn with tool loop ──
        try:
            async for chunk in self._llm_turn():
                yield chunk
        except asyncio.CancelledError:
            # Barge-in cancelled this generator mid-stream.
            # Memory already has what was spoken (eager commits below).
            # Re-raise so the task exits cleanly — do NOT yield anything more.
            log.info("turn %d: cancelled by barge-in (lang=%s)", self.turn_no, active_lang)
            raise
        except ProviderError as e:
            log.error("turn failed: %s", e)
            lang = active_lang if active_lang in self.persona.apology else "mr"
            text = self.persona.apology[lang]
            self.memory.history.append({"role": "assistant", "content": text})
            yield self._voice_fixed(text, lang, StyleName.DEFAULT)

        # ── Number Recognition Engine: arm the buffer if the agent just asked
        # for an account/mobile/OTP number, so a fragmented reply across
        # multiple pauses gets merged instead of handled turn-by-turn.
        self._arm_number_collection_if_asked()

        log.info("turn %d done in %.0f ms (lang=%s)", self.turn_no,
                 (time.perf_counter() - t0) * 1000, active_lang)
        yield TurnChunk("done")

    def _arm_number_collection_if_asked(self) -> None:
        last_assistant = next(
            (m["content"] for m in reversed(self.memory.history)
             if m.get("role") == "assistant" and m.get("content")),
            None,
        )
        if not last_assistant:
            return
        field_name = self.memory.field_requested_by(last_assistant)
        if field_name:
            self.memory.start_number_collection(field_name)
            log.info("turn %d: armed number collection for '%s'", self.turn_no, field_name)

    # ── escalation: build the transfer context on tool call ──────────────────
    def _prepare_transfer(self, args: dict, result: dict) -> None:
        """Assemble the TransferContext when transfer_to_senior_executive fires.
        The VoiceSession reads self.transfer_requested after the turn's audio
        drains and performs the actual leg transfer. Identity + summary come from
        call memory / the decision engine, never from the model."""
        decision = self._escalation_decision
        reason = args.get("escalation_reason") or decision.reason or "requested"
        category = (args.get("issue_category") or decision.category
                    or result.get("issue_category") or "")
        priority = (args.get("issue_priority") or result.get("issue_priority")
                    or decision.priority or "MEDIUM").upper()
        # Reuse the engine's decision object so the summary reflects the same
        # category/priority/reason the caller was escalated under.
        summary_decision = EscalationDecision(
            True, reason, category, priority, decision.source or "tool")
        summary = build_escalation_summary(
            self.memory, self._tools_used, summary_decision, self._last_user_text)
        verified = "verified" if self.memory.verified else "not_verified"
        complaint_id = (self.memory.complaints[-1].ticket_no
                        if self.memory.complaints else "")
        self.transfer_requested = TransferContext(
            escalation_reason=reason,
            issue_category=category,
            issue_priority=priority,
            summary=summary,
            customer_name=self.memory.name or "",
            customer_id=self.memory.account_no or "",
            mobile=self.memory.mobile or "",
            caller_number=getattr(self.memory, "caller_number", "") or "",
            language=self.memory.language,
            complaint_id=complaint_id,
            verification_status=verified,
            troubleshooting_done=summary,       # full summary carries the trace
            session_id=self.memory.session_id,
        )
        log.warning("turn %d: TRANSFER prepared — reason=%s category=%s priority=%s ref=%s",
                    self.turn_no, reason, category, priority, result.get("reference"))

    # ── emergency path ───────────────────────────────────────────────────────
    async def _emergency(self, verdict: safety.SafetyVerdict, user_text: str) -> AsyncIterator[TurnChunk]:
        lang = self.memory.language if self.memory.language in ("mr", "hi", "en") else "mr"
        line = safety.safety_line(verdict, lang, self.persona)
        yield self._voice_fixed(line, lang, StyleName.EMERGENCY)     # speak FIRST
        details = f"{user_text[:120]} | caller: {self.memory.mobile or 'unknown'}"
        # Priority tools: shield so barge-in cannot abort these critical writes
        await _shielded_dispatch(
            self.tools, "log_priority_incident",
            {"type": verdict.incident_type, "details": details}, self.memory,
        )
        await _shielded_dispatch(
            self.tools, "transfer_to_human",
            {"reason": "fraud_or_security_incident",
             "context_summary": f"{verdict.incident_type}: {user_text[:150]}"},
            self.memory,
        )
        follow = self.persona.emergency_follow[lang]
        self.memory.history.append({"role": "assistant", "content": f"{line} {follow}"})
        yield self._voice_fixed(follow, lang, StyleName.EMERGENCY)
        yield TurnChunk("done")

    # ── LLM turn with tool loop ──────────────────────────────────────────────
    def _verified_caller_directive(self) -> str:
        """Standing directive for a caller recognized from their registered mobile.
        Kills the 'greeted by name but still asked to verify' behaviour: identity
        is already established by caller ID, so verification must never be asked."""
        if not (self._caller_recognized and self.memory.verified):
            return ""
        return (
            "[CALLER ALREADY VERIFIED] This caller is phoning from their own "
            "registered mobile, so their identity is CONFIRMED and their name, "
            "account number and mobile are in CALL MEMORY. Do NOT ask them to "
            "verify, and never ask for their account number or mobile — you "
            "already have them. Go straight to solving their problem. (An OTP is "
            "still required only for a plan change or a SIM swap.)"
        )

    def _messages(self, knowledge_block: str = "") -> list[dict]:
        confidence_directive = self._last_confidence.directive() if self._last_confidence else ""
        directives = "\n\n".join(
            d for d in (confidence_directive, self.topic.directive(),
                        self._mood_directive(), self._verified_caller_directive(),
                        self._escalation_decision.directive()) if d
        )
        system = compose_system_prompt(self.lang.directive(), self.memory.render_block(),
                                       knowledge_block, directives, persona=self.persona)
        return [{"role": "system", "content": system},
                *self.memory.trimmed_history(self.s.history_max_turns)]

    async def _llm_turn(self) -> AsyncIterator[TurnChunk]:
        knowledge_block = ""
        scratch: list[dict] = []          # tool call/result messages within this turn
        lang = self.memory.language if self.memory.language in ("mr", "hi", "en") else "mr"

        # Reset per-turn voicing state. The Voice Director assigns ONE style for
        # the whole turn (decided on the first spoken sentence) so the reply is
        # delivered as a consistent performance, not sentence-by-sentence drift.
        self._turn_profile = None
        self._turn_ctx = None
        self._turn_is_first = True
        self._turn_processed = False
        self._turn_complaints_before = len(self.memory.complaints)
        # The escalation engine (run in run_turn, before this) has already read
        # last turn's tool results — start this turn's collection fresh.
        self._last_tool_results = []

        for round_no in range(self.s.max_tool_rounds + 1):
            messages = self._messages(knowledge_block) + scratch
            buffer = ""
            spoken: list[str] = []
            tool_calls: list[dict] = []
            t_round = time.perf_counter()
            t_first_token: float | None = None

            async for delta in self.llm.stream_chat(messages, tools=self.tools.schemas):
                if t_first_token is None and (delta.text or delta.tool_calls):
                    t_first_token = time.perf_counter()
                if delta.text:
                    buffer += delta.text
                    # ── primary split: sentence-ending punctuation ──────────
                    parts = _SENT_SPLIT.split(buffer)
                    if len(parts) > 1:
                        for sentence in parts[:-1]:
                            sentence = _sanitize(sentence)
                            if sentence:
                                # EAGER COMMIT: sentence goes to history before TTS plays it
                                # so a barge-in mid-stream never loses what was already heard.
                                spoken.append(sentence)
                                self.memory.history.append(
                                    {"role": "assistant", "content": sentence}
                                )
                                yield self._voice(sentence, lang)
                        buffer = parts[-1]
                    # ── secondary split: comma/pause for long buffers ───────
                    # If no sentence boundary found but buffer is long, split
                    # at the last comma so TTS starts sooner. LATENCY: while
                    # NOTHING has been voiced yet this turn, the threshold drops
                    # to llm_first_flush_chars (default 80) — first audio starts
                    # ~200 ms sooner on a long opening sentence, and only that
                    # first segment pays the split-prosody cost.
                    elif len(buffer) >= (
                            getattr(self.s, "llm_first_flush_chars", 80)
                            if self._turn_is_first else _FORCE_FLUSH_CHARS):
                        pause_parts = _PAUSE_SPLIT.split(buffer)
                        if len(pause_parts) > 1:
                            # yield everything up to the last segment
                            for segment in pause_parts[:-1]:
                                segment = _sanitize(segment)
                                if segment:
                                    spoken.append(segment)
                                    self.memory.history.append(
                                        {"role": "assistant", "content": segment}
                                    )
                                    yield self._voice(segment, lang)
                            buffer = pause_parts[-1]
                if delta.finish:
                    tool_calls = delta.tool_calls

            tail = _sanitize(buffer)
            if tail:
                spoken.append(tail)
                self.memory.history.append({"role": "assistant", "content": tail})
                yield self._voice(tail, lang)

            # Per-round LLM timing — pinpoints whether a slow turn is model TTFT,
            # generation, or the tool loop (see docs/LATENCY_AUDIT.md).
            now = time.perf_counter()
            log.info("turn %d round %d: llm ttft=%.0fms total=%.0fms tools=%s",
                     self.turn_no, round_no,
                     ((t_first_token or now) - t_round) * 1000, (now - t_round) * 1000,
                     [c["function"]["name"] for c in tool_calls] or "none")
            # Log what the agent actually SAID this round (truncated) so spoken
            # output is visible in the server log for debugging — pairs with the
            # "STT → …" line that already records what the caller said.
            if spoken:
                said = " ".join(spoken)
                log.info("turn %d round %d SAID: %r%s", self.turn_no, round_no,
                         said[:300], "…" if len(said) > 300 else "")

            if not tool_calls:
                # Note: sentences already committed individually above; no bulk append needed.
                return

            # ── PERCEIVED-LATENCY SHIELD: never leave the caller in silence while
            # tools + another LLM round run (observed: 10+ s of dead air). If this
            # round called tools without saying anything first, speak a short,
            # persona-correct thinking filler NOW — TTS plays it while the lookups
            # and the next round execute. Fires on ANY silent tool round (not just
            # round 0) so a multi-round tool loop never drops the caller into dead
            # air mid-verification. The per-call VariationTracker guarantees the
            # filler is never the same phrase twice in a row, so it stays human.
            if not spoken and not self.end_call_requested \
                    and self.speech is not None:
                from ..speech.lexicon import HESITATIONS, lang_table
                filler = self.speech.variation.pick(
                    f"hes:{lang}", lang_table(HESITATIONS, lang))
                if filler:
                    self.memory.history.append(
                        {"role": "assistant", "content": filler + "…"})
                    yield self._voice_fixed(filler + "…", lang, StyleName.DEFAULT)

            # PERCEIVED RESPONSIVENESS: tell the transport a lookup is running so
            # the UI can show a live "Checking…" indicator while the tools + next
            # LLM round execute. Non-audio, best-effort telemetry only; the
            # transport re-asserts "speaking" on the next spoken sentence.
            yield TurnChunk("status", text="checking")

            # ── tool execution — SHIELDED so barge-in cannot abort in-flight writes ──
            # content stays None: the spoken sentences were already eager-committed to
            # memory.history above. Repeating them here put the same text in the next
            # round's context TWICE, which nudged the model into repeating itself.
            assistant_msg: dict = {
                "role": "assistant",
                "content": None,
                "tool_calls": tool_calls,
            }
            scratch.append(assistant_msg)

            for call in tool_calls:
                name = call["function"]["name"]
                try:
                    args = json.loads(call["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}

                if name == "end_call":
                    self.end_call_requested = True
                    log.info("turn %d: agent requested end_call (%s)",
                             self.turn_no, args.get("reason", "unspecified"))

                try:
                    result = await _shielded_dispatch(
                        self.tools, name, args, self.memory,
                    )
                except asyncio.CancelledError:
                    # Outer task was cancelled (barge-in) while waiting for shield.
                    # _shielded_dispatch already spawned a late-absorber background task.
                    # Re-raise so the generator exits cleanly.
                    log.info(
                        "turn %d: barge-in during tool %s — late absorber running in background",
                        self.turn_no, name,
                    )
                    raise

                if name == "search_knowledge" and isinstance(result, dict):
                    knowledge_block = result.get("context", "") or knowledge_block

                # ── escalation bookkeeping ──
                self._tools_used.append(name)
                self._last_tool_results.append(result if isinstance(result, dict) else {})
                if name in _TROUBLESHOOT_TOOLS and isinstance(result, dict) \
                        and not result.get("error"):
                    self._failed_attempts += 1     # a troubleshooting attempt was made
                if name == "register_complaint" and isinstance(result, dict) \
                        and result.get("ticket_no"):
                    self._failed_attempts = 0       # logged → fresh slate
                if name == "transfer_to_senior_executive" and isinstance(result, dict) \
                        and result.get("escalation_prepared"):
                    self._prepare_transfer(args, result)

                scratch.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": json.dumps(result, ensure_ascii=False),
                })

            # A real lookup/tool ran this turn — the next spoken sentence may now
            # use a genuine thinking lead-in ("Let me just check…"), never faked.
            self._turn_processed = True
            # spoken sentences for this round already committed above — nothing to bulk append

        log.warning("max tool rounds hit — forcing spoken close")
        fallback = self.persona.apology[lang]
        self.memory.history.append({"role": "assistant", "content": fallback})
        yield self._voice_fixed(fallback, lang, StyleName.DEFAULT)


# ── helpers ──────────────────────────────────────────────────────────────────

async def _shielded_dispatch(
    tools: ToolRegistry,
    name: str,
    args: dict,
    memory: CallMemory,
) -> dict:
    """Dispatch a tool call inside asyncio.shield().

    If the outer task is cancelled (barge-in), the tool keeps running in the
    background. When it finishes, its result is absorbed into memory so the
    next turn can use it.

    Returns the result dict on success, or raises CancelledError (which the
    caller should handle by re-raising to exit the generator cleanly).
    """
    tool_future: asyncio.Future = asyncio.ensure_future(
        tools.dispatch(name, args, memory)
    )
    try:
        return await asyncio.shield(tool_future)
    except asyncio.CancelledError:
        # Outer task cancelled; spawn a background absorber so the tool result
        # is never lost even if this coroutine is being torn down.
        asyncio.create_task(
            _late_tool_absorb(tool_future, name, args, memory),
            name=f"late_absorb_{name}",
        )
        raise


async def _late_tool_absorb(
    fut: asyncio.Future,
    name: str,
    args: dict,
    memory: CallMemory,
) -> None:
    """Background task: wait for a shielded tool to finish, then absorb the result.

    This runs after a barge-in cancels the main turn. The tool (e.g., register_complaint)
    was already in flight — we let it complete and update memory so the next AI turn
    can reference the result (e.g., "Your complaint SR-12345 has been registered").
    """
    try:
        result = await fut
        memory.absorb_tool_result(name, args, result)
        log.info("late_absorb: tool '%s' finished post-barge-in → %s",
                 name, str(result)[:120])
    except asyncio.CancelledError:
        log.warning("late_absorb: tool '%s' was also cancelled — result lost", name)
    except Exception as e:
        log.warning("late_absorb: tool '%s' failed post-barge-in: %s", name, e)


# PROMPT-LEAKAGE GUARD. Under churn (e.g. the caller switching languages several
# times, plus garbled code-mix STT), the model occasionally regurgitates an
# INSTRUCTION from the system prompt verbatim instead of following it — observed
# in production as the agent speaking "ACTIVE LANGUAGE: Marathi. Reply ENTIRELY
# in this language…" aloud. None of these internal markers ever belong in speech,
# so any sentence carrying one is dropped before it reaches TTS.
_LEAK_MARKERS = re.compile(
    r"ACTIVE LANGUAGE|CALL MEMORY|KNOWLEDGE CONTEXT|CALLER MOOD|CALLER ALREADY VERIFIED"
    r"|GRAMMATICAL GENDER|PUNE REGION ONLY|MAHARASHTRA CIRCLE|Identity verified:"
    r"|Reply ENTIRELY in this language|\[CALLER|\[KNOWLEDGE",
    re.IGNORECASE,
)


def _sanitize(text: str) -> str:
    """Strip anything unspeakable that slips through (markdown, labels, and any
    leaked system-prompt directive — see _LEAK_MARKERS).

    Also strips parenthetical asides entirely. Observed in production: the model
    sometimes writes a number/code out phonetically for natural speech, then adds
    a parenthetical "written form" repeat right after it (e.g. "SR two six zero...
    (SR260782D4E6)") — a habit from written text where a raw form in parens is
    helpful, but here the TTS reads BOTH, so the caller hears the same ticket/
    account/OTP number spoken twice in a row. Since nothing in a voice-only call
    should ever need a parenthetical aside (there's no reader to skip past it),
    dropping the content is always safe, not just a narrow fix for this one case.
    """
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"[*_#`]+", "", text)
    text = re.sub(r"^\s*(?:[-•]|\d+[.)])\s*", "", text, flags=re.M)
    cleaned = re.sub(r"\s+", " ", text).strip()
    # Drop any sentence that carries a leaked internal directive — the caller
    # must never hear the agent's own instructions read out loud.
    if cleaned and _LEAK_MARKERS.search(cleaned):
        log.warning("dropped leaked prompt directive from speech: %r", cleaned[:90])
        return ""
    return cleaned
