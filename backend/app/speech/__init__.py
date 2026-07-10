"""Human Speech Generation Engine + Voice Director.

An intelligent layer between the LLM and Sarvam TTS that turns 'an AI reading
text' into 'a human naturally speaking'. It does NOT optimise the LLM or the TTS
engine — it transforms raw LLM output into natural spoken dialogue before it
reaches TTS.

Pipeline:
    Gemini → ResponseOptimizer → VoiceDirector → HumanSpeechEngine
           → ProsodyPlanner → SarvamFormatter → Sarvam TTS

Public entry point: SpeechDirector (backend.app.speech.pipeline).
"""
from __future__ import annotations

from .director import VoiceDirector, detect_caller_emotion
from .engine import HumanSpeechEngine
from .evaluate import SpeechNaturalnessEvaluator, compare
from .formatter import SarvamFormatter
from .numbers_speech import format_numbers_for_speech
from .optimizer import ResponseOptimizer
from .pipeline import SpeechDirector
from .plan import (
    Emotion,
    PauseType,
    Segment,
    SpeechContext,
    SpokenPlan,
    StyleName,
    StyleProfile,
    norm_lang,
)
from .profiles import PROFILES, base_profile
from .prosody import ProsodyPlanner
from .variation import VariationTracker

__all__ = [
    "SpeechDirector",
    "VoiceDirector",
    "HumanSpeechEngine",
    "ProsodyPlanner",
    "SarvamFormatter",
    "ResponseOptimizer",
    "VariationTracker",
    "SpeechContext",
    "SpokenPlan",
    "StyleProfile",
    "StyleName",
    "Segment",
    "PauseType",
    "Emotion",
    "PROFILES",
    "base_profile",
    "norm_lang",
    "detect_caller_emotion",
    "format_numbers_for_speech",
    "SpeechNaturalnessEvaluator",
    "compare",
]
