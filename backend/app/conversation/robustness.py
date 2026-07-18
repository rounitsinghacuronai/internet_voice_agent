"""Conversation Robustness Layer.

Sits between STT output and the Conversation Manager's LLM turn. Two jobs:

  1. Confidence estimation — Sarvam's API does NOT return a per-word/utterance
     transcription confidence score (confirmed against their docs: the only
     confidence-shaped field is `language_probability`, which measures
     confidence in the DETECTED LANGUAGE, not transcript accuracy). This
     module combines that with the VAD's peak speech-probability for the
     utterance (a proxy for audio clarity — echo, noise, and distant/quiet
     speech all suppress it) into a composite tier: HIGH / MEDIUM / LOW.
     This is an honest proxy, not real STT confidence — documented here so
     nobody mistakes it for one later.

  2. Turning that tier into a prompt directive so the LLM (which is far
     better at natural, targeted clarification than any hand-coded string
     template) knows how to react:
       HIGH   → proceed normally, no hedging.
       MEDIUM → use conversation history/context to infer intent rather than
                re-asking; only confirm if the inference materially changes
                what tool gets called.
       LOW    → confirm ONLY the uncertain part, never ask the caller to
                repeat the whole sentence.

Intent/context stability (background noise not derailing the active topic)
lives alongside this as `intent_stability_directive`, extending the same
hysteresis pattern already used by LanguageEngine (see language.py) from
"which language" to "which topic is active" — a single stray background
word (TV, another speaker) should never look like a topic change.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ConfidenceTier(Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# Tunable thresholds. peak_prob is the VAD's speech-probability ceiling for
# the utterance (0-1); language_confidence is Sarvam's language_probability
# (0-1, or None when unavailable e.g. a fixed language_code was requested).
_HIGH_PEAK = 0.75
_LOW_PEAK = 0.45
_HIGH_LANG = 0.80
_LOW_LANG = 0.50


@dataclass
class ConfidenceEstimate:
    tier: ConfidenceTier
    peak_prob: float
    language_confidence: float | None

    def directive(self) -> str:
        if self.tier is ConfidenceTier.HIGH:
            return ""  # nothing to inject — proceed exactly as normal
        if self.tier is ConfidenceTier.MEDIUM:
            return (
                "[TRANSCRIPTION CONFIDENCE: MEDIUM] The speech recognizer had moderate "
                "difficulty with this utterance (background noise or unclear audio). "
                "Use the conversation history and context to infer the caller's intended "
                "meaning rather than asking them to repeat themselves. Only ask a "
                "clarifying question if the ambiguity actually changes what you would say "
                "or do next."
            )
        return (
            "[TRANSCRIPTION CONFIDENCE: LOW] The speech recognizer likely misheard part of "
            "this utterance (heavy background noise, distant speech, or a garbled word). "
            "Do NOT ask the caller to repeat the whole sentence. Identify specifically which "
            "part is uncertain (e.g. a number, a name) and confirm ONLY that part in one "
            "short question, exactly like a careful human agent would."
        )


def estimate_confidence(
    peak_prob: float,
    language_confidence: float | None,
) -> ConfidenceEstimate:
    """Combine VAD peak-probability and Sarvam's language_probability into one tier.

    Weighting: peak_prob is the stronger signal (it reflects the actual audio
    that was fed to STT), language_confidence is a secondary signal and is
    ignored (treated as neutral/high) when absent rather than dragging the
    estimate down — many calls run with a fixed language_code, in which case
    Sarvam doesn't return this field at all.
    """
    lang_conf = language_confidence if language_confidence is not None else 1.0

    if peak_prob >= _HIGH_PEAK and lang_conf >= _HIGH_LANG:
        tier = ConfidenceTier.HIGH
    elif peak_prob < _LOW_PEAK or lang_conf < _LOW_LANG:
        tier = ConfidenceTier.LOW
    else:
        tier = ConfidenceTier.MEDIUM

    return ConfidenceEstimate(tier=tier, peak_prob=peak_prob, language_confidence=language_confidence)


# ── Intent / context stability ───────────────────────────────────────────────
# Same hysteresis idea as LanguageEngine (language.py): a topic only changes
# after the caller's utterance is CLEARLY about something else, not on the
# strength of a single ambiguous word that might be background noise (a TV,
# another person in the room) bleeding into the transcript.

_TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "network": ("no network", "network nahi", "network गया", "नेटवर्क नाही", "नेटवर्क नहीं",
                "signal nahi", "no signal", "सिग्नल", "call drop", "कॉल ड्रॉप", "call cut",
                "tower", "टॉवर", "roaming", "रोमिंग", "volte", "sms nahi", "data not working",
                "data nahi chal", "internet slow on phone"),
    "internet": ("internet nahi", "internet not working", "net nahi chal", "नेट चालत नाही",
                 "नेट नहीं चल", "wifi", "वायफाय", "वाईफाई", "broadband", "ब्रॉडबँड",
                 "ब्रॉडबैंड", "fiber", "फायबर", "router", "राउटर", "राऊटर", "red light",
                 "लाल लाइट", "los", "slow internet", "speed kam", "स्पीड", "disconnect ho"),
    "billing": ("bill", "बिल", "बिलाची", "recharge", "रिचार्ज", "payment", "पेमेंट",
                "बिल भरणे", "balance", "बॅलन्स", "बैलेंस", "refund", "रिफंड", "plan",
                "प्लॅन", "प्लान", "pack", "पॅक", "पैक"),
    "sim": ("sim", "सिम", "esim", "ई-सिम", "porting", "पोर्ट", "mnp", "kyc", "केवायसी",
            "sim band", "sim block", "नवीन सिम", "नया सिम"),
    "complaint_status": ("complaint status", "track complaint", "ticket number", "शिकायत",
                          "तक्रार क्रमांक", "ticket no", "तिकीट"),
    "new_connection": ("new connection", "नवीन कनेक्शन", "नया कनेक्शन", "application status",
                       "installation", "इन्स्टॉलेशन", "इंस्टालेशन"),
}


def detect_topic(text: str) -> str | None:
    low = text.lower()
    for topic, keywords in _TOPIC_KEYWORDS.items():
        if any(k in low for k in keywords):
            return topic
    return None


@dataclass
class TopicStability:
    """Per-call topic tracker. Requires 2 consecutive utterances clearly about
    a NEW topic before switching — one stray background word never flips it,
    mirroring LanguageEngine's hysteresis for language drift."""
    active: str | None = None
    _streak_topic: str | None = None
    _streak_count: int = 0

    def update(self, text: str) -> str | None:
        detected = detect_topic(text)
        if detected is None:
            return self.active
        if self.active is None:
            self.active = detected
            return self.active
        if detected == self.active:
            self._streak_count = 0
            return self.active
        if detected == self._streak_topic:
            self._streak_count += 1
        else:
            self._streak_topic = detected
            self._streak_count = 1
        if self._streak_count >= 2:
            self.active = detected
            self._streak_topic = None
            self._streak_count = 0
        return self.active

    def directive(self) -> str:
        if not self.active:
            return ""
        return (
            f"[ACTIVE TOPIC: {self.active}] Stay focused on this topic. If a word in the "
            "transcript seems unrelated (e.g. a TV, radio, or another person's voice bleeding "
            "into the call), ignore it rather than switching topics — only follow the caller "
            "to a new topic if their own statement is clearly and deliberately about something "
            "else, not a single stray word."
        )
