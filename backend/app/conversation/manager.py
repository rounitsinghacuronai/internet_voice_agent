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
    def greeting(self) -> TurnChunk:
        text = self.persona.greeting
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

        self.memory.scan_user_text(user_text)
        # Disarm number collection once its slot is filled (by ANY path — the
        # fragment buffer, scan_user_text, or a verify result). A stale armed
        # buffer kept every later utterance on the slow 900 ms number-dictation
        # endpointing, adding ~500 ms to every turn of the rest of the call.
        nb = self.memory.number_buffer
        if nb.active and nb.field and (
                getattr(self.memory, nb.field, None)
                or (nb.field == "consumer_no" and self.memory.verified)):
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
        # for a consumer/mobile/OTP/meter number, so a fragmented reply across
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

    # ── emergency path ───────────────────────────────────────────────────────
    async def _emergency(self, verdict: safety.SafetyVerdict, user_text: str) -> AsyncIterator[TurnChunk]:
        lang = self.memory.language if self.memory.language in ("mr", "hi", "en") else "mr"
        line = safety.safety_line(verdict, lang, self.persona)
        yield self._voice_fixed(line, lang, StyleName.EMERGENCY)     # speak FIRST
        location = self.memory.location or user_text[:120]
        # Safety tools: shield so barge-in cannot abort these critical writes
        await _shielded_dispatch(
            self.tools, "log_safety_incident",
            {"type": verdict.incident_type, "location": location}, self.memory,
        )
        await _shielded_dispatch(
            self.tools, "transfer_to_human",
            {"reason": "safety_emergency",
             "context_summary": f"{verdict.incident_type}: {user_text[:150]}"},
            self.memory,
        )
        follow = self.persona.emergency_follow[lang]
        self.memory.history.append({"role": "assistant", "content": f"{line} {follow}"})
        yield self._voice_fixed(follow, lang, StyleName.EMERGENCY)
        yield TurnChunk("done")

    # ── LLM turn with tool loop ──────────────────────────────────────────────
    def _messages(self, knowledge_block: str = "") -> list[dict]:
        confidence_directive = self._last_confidence.directive() if self._last_confidence else ""
        directives = "\n\n".join(
            d for d in (confidence_directive, self.topic.directive(),
                        self._mood_directive()) if d
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
                    # at the last comma so TTS starts sooner.
                    elif len(buffer) >= _FORCE_FLUSH_CHARS:
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

            if not tool_calls:
                # Note: sentences already committed individually above; no bulk append needed.
                return

            # ── PERCEIVED-LATENCY SHIELD: never leave the caller in silence while
            # tools + another LLM round run (observed: 10+ s of dead air). If the
            # model called tools without saying anything first, speak a short,
            # persona-correct thinking filler NOW — TTS plays it while the
            # lookups and the next round execute.
            if round_no == 0 and not spoken and not self.end_call_requested \
                    and self.speech is not None:
                from ..speech.lexicon import HESITATIONS, lang_table
                filler = self.speech.variation.pick(
                    f"hes:{lang}", lang_table(HESITATIONS, lang))
                if filler:
                    self.memory.history.append(
                        {"role": "assistant", "content": filler + "…"})
                    yield self._voice_fixed(filler + "…", lang, StyleName.DEFAULT)

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


def _sanitize(text: str) -> str:
    """Strip anything unspeakable that slips through (markdown, labels).

    Also strips parenthetical asides entirely. Observed in production: the model
    sometimes writes a number/code out phonetically for natural speech, then adds
    a parenthetical "written form" repeat right after it (e.g. "SR two six zero...
    (SR260782D4E6)") — a habit from written text where a raw form in parens is
    helpful, but here the TTS reads BOTH, so the caller hears the same complaint/
    consumer/OTP number spoken twice in a row. Since nothing in a voice-only call
    should ever need a parenthetical aside (there's no reader to skip past it),
    dropping the content is always safe, not just a narrow fix for this one case.
    """
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"[*_#`]+", "", text)
    text = re.sub(r"^\s*(?:[-•]|\d+[.)])\s*", "", text, flags=re.M)
    return re.sub(r"\s+", " ", text).strip()
