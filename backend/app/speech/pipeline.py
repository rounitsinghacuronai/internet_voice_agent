"""SpeechDirector — the facade the ConversationManager calls.

One instance per call. It wires the stages together and holds the per-call
VariationTracker so acknowledgements and phrasings never repeat back-to-back:

    direct(ctx)                → StyleProfile      (Voice Director, once per turn)
    render(text, profile, ctx) → SpokenPlan        (deterministic, zero-latency)
    render_async(...)          → SpokenPlan        (optional LLM restructure first)

The deterministic render() is the real-time path wired into the live pipeline.
render_async() adds the optional micro-LLM restructuring pass when
Settings.speech_llm_restructure is on (higher human-feel, some added latency).
"""
from __future__ import annotations

from ..config import Settings
from .director import VoiceDirector
from .engine import HumanSpeechEngine
from .formatter import SarvamFormatter
from .numbers_speech import format_numbers_for_speech
from .optimizer import ResponseOptimizer
from .plan import Segment, SpeechContext, SpokenPlan, StyleName, StyleProfile, norm_lang
from .profiles import base_profile
from .prosody import ProsodyPlanner
from .variation import VariationTracker


class SpeechDirector:
    def __init__(self, settings: Settings, llm=None):
        self.s = settings
        self.director = VoiceDirector()
        self.optimizer = ResponseOptimizer(llm)
        self.engine = HumanSpeechEngine()
        self.prosody = ProsodyPlanner()
        self.formatter = SarvamFormatter()
        self.variation = VariationTracker()
        self._pace_min = getattr(settings, "speech_pace_min", 0.7)
        self._pace_max = getattr(settings, "speech_pace_max", 1.15)

    # ── Voice Director ────────────────────────────────────────────────────────
    def direct(self, ctx: SpeechContext) -> StyleProfile:
        return self.director.direct(ctx)

    # ── deterministic render (real-time path) ─────────────────────────────────
    def render(self, text: str, profile: StyleProfile, ctx: SpeechContext) -> SpokenPlan:
        lang = ctx.lang()
        raw_in = text
        cleaned, notes = self.optimizer.clean(text, lang)
        return self._finish(cleaned, raw_in, lang, profile, ctx, notes)

    # ── async render (optional LLM restructuring pass) ────────────────────────
    async def render_async(self, text: str, profile: StyleProfile,
                           ctx: SpeechContext) -> SpokenPlan:
        lang = ctx.lang()
        raw_in = text
        cleaned, notes = self.optimizer.clean(text, lang)
        if getattr(self.s, "speech_llm_restructure", False) and not profile.preserve_wording:
            restructured, used = await self.optimizer.restructure(cleaned, lang, profile)
            if used:
                cleaned = restructured
                notes.append("llm-restructure")
        return self._finish(cleaned, raw_in, lang, profile, ctx, notes)

    # ── shared tail: engine → prosody → numbers → formatter ───────────────────
    def _finish(self, cleaned: str, raw_in: str, lang: str, profile: StyleProfile,
                ctx: SpeechContext, notes: list[str]) -> SpokenPlan:
        segments = self.engine.shape(cleaned, lang, profile, ctx, self.variation)
        segments = self.prosody.plan(segments, lang, profile, ctx)

        num_changed = False
        formatted: list[Segment] = []
        for seg in segments:
            new_text, changed = format_numbers_for_speech(seg.text, lang)
            num_changed = num_changed or changed
            formatted.append(Segment(new_text, seg.pause))
        if num_changed:
            notes.append("numbers")

        text, pace = self.formatter.render(
            formatted, lang, profile, self.s.tts_pace, self._pace_min, self._pace_max
        )
        self.variation.remember(text)
        return SpokenPlan(
            text=text, language=lang, pace=pace,
            style=profile.name.value, emotion=profile.emotion.value,
            segments=formatted, raw_in=raw_in, notes=notes,
        )

    # ── reviewed / fixed lines (greeting, safety, apology, silence prompts) ───
    def render_fixed(self, text: str, lang: str,
                     style_name: StyleName = StyleName.DEFAULT) -> SpokenPlan:
        """Deliver a reviewed, fixed line: prosody, pace and number formatting
        only — no acknowledgement, hesitation, restructuring or de-AI rewriting.
        Used for the greeting, safety lines, apologies and silence prompts."""
        from dataclasses import replace
        profile = replace(base_profile(style_name), preserve_wording=True, lead_in=False,
                          hesitation_ok=False)
        ctx = SpeechContext(language=lang, is_first_utterance=True)
        return self.render(text, profile, ctx)

    # ── convenience for standalone use (tests / evaluator) ────────────────────
    def plan_line(self, text: str, ctx: SpeechContext) -> SpokenPlan:
        profile = self.direct(ctx)
        return self.render(text, profile, ctx)
