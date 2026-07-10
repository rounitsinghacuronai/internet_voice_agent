"""Speech-plan data model — the vocabulary every stage of the Human Speech
Generation Engine speaks in.

The pipeline is:

    Gemini text
      → ResponseOptimizer   (clean written artefacts / optional LLM restructure)
      → VoiceDirector       (SpeechContext → StyleProfile: the "performance")
      → HumanSpeechEngine   (thought-groups, acknowledgement, hesitation, breathing)
      → ProsodyPlanner      (meaning-based pauses + intonation)
      → SarvamFormatter     (Sarvam-specific punctuation + per-utterance pace)
      → SpokenPlan          (Sarvam-ready text + pace + telemetry)

Nothing here talks to a network. Every type is a plain dataclass/enum so the
whole engine is deterministic, unit-testable, and adds no latency.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


# ── languages the engine speaks natively ─────────────────────────────────────
LANGS = ("mr", "hi", "en")


def norm_lang(lang: str) -> str:
    """Collapse an STT/BCP-47 hint ('hi-IN', 'unknown', 'und') to mr|hi|en.

    Marathi is the house default (the greeting language) before the caller has
    established one — matching ConversationManager._lang_for().
    """
    if not lang:
        return "mr"
    low = lang.lower()
    if low.startswith("mr"):
        return "mr"
    if low.startswith("hi"):
        return "hi"
    if low.startswith("en"):
        return "en"
    return "mr"


# ── pauses: typed by MEANING, not by fixed length ────────────────────────────
class PauseType(Enum):
    """Why a pause exists. The formatter maps each to Sarvam punctuation; the
    reason is kept so the evaluator can measure that pauses are intentional and
    varied rather than mechanically identical."""
    MICRO = "micro"                # tiny lift between clauses (a comma beat)
    BREATH = "breath"              # comfortable breathing break in a long line
    THINKING = "thinking"          # "let me check…" — real processing beat
    CONFIRMATION = "confirmation"  # beat before/after a confirmed fact
    EMPATHY = "empathy"            # beat that lets a feeling land
    TRANSITION = "transition"      # moving from one thought to the next
    LISTENING = "listening"        # inviting the caller back in
    COMPLETION = "completion"      # settling at the end of a thought


class Emotion(Enum):
    """Delivery colour the Voice Director assigns. Stays subtle — never theatrical."""
    NEUTRAL = "neutral"
    CONCERNED = "concerned"        # power outage — something's wrong
    HELPFUL = "helpful"            # billing / how-to
    CONFIDENT = "confident"        # complaint registered, action done
    CALM_URGENT = "calm_urgent"    # emergency — steady but quick
    WARM = "warm"                  # greeting / returning caller / closing
    PATIENT = "patient"            # angry, frustrated, or elderly caller
    REASSURING = "reassuring"      # worried caller, safety follow-up
    APOLOGETIC = "apologetic"      # our error / repeated failure


class StyleName(Enum):
    GREETING = "greeting"
    VERIFICATION = "verification"
    OUTAGE = "outage"
    BILLING = "billing"
    COMPLAINT_REGISTERED = "complaint_registered"
    EMERGENCY = "emergency"
    ESCALATION = "escalation"
    CLOSING = "closing"
    DEFAULT = "default"


@dataclass(frozen=True)
class StyleProfile:
    """A speaking style — the Voice Director's output. One profile is chosen per
    turn so the whole reply is delivered as a consistent 'performance' rather
    than each sentence read with the same flat cadence."""
    name: StyleName
    emotion: Emotion
    pace: float = 1.0              # multiplier on Settings.tts_pace (clamped later)
    pause_scale: float = 1.0       # 1.0 = normal; >1 = roomier; <1 = tighter
    warmth: float = 0.6            # 0-1, used by the engine for empathy budget
    lead_in: bool = True           # allow an active-listening acknowledgement
    hesitation_ok: bool = True     # allow a thinking filler when actually processing
    number_pace: float = 0.9       # pace to drop to while speaking digit groups
    preserve_wording: bool = False  # True = reviewed/fixed line, prosody only
    max_thought_chars: int = 150   # split thoughts longer than this for breathing
    label: str = ""                # human-readable, for telemetry / eval

    def with_emotion(self, emotion: Emotion, **over) -> "StyleProfile":
        from dataclasses import replace
        return replace(self, emotion=emotion, **over)


@dataclass
class SpeechContext:
    """Everything the Voice Director needs to pick a style and the engine needs
    to shape delivery. Assembled by the ConversationManager once per turn."""
    language: str = "mr"
    turn_no: int = 0
    is_first_utterance: bool = True   # first spoken sentence of this turn
    is_greeting: bool = False
    is_emergency: bool = False
    is_closing: bool = False
    is_apology: bool = False          # provider-error apology line
    verified: bool = False
    asking_for_number: str | None = None   # consumer_no|mobile|otp|meter_no
    just_registered_complaint: bool = False
    topic: str | None = None          # outage|billing|complaint_status|new_connection
    confidence_tier: str = "high"     # high|medium|low
    caller_emotion: str | None = None  # angry|frustrated|elderly|worried|calm
    processing: bool = False          # a real lookup/tool ran → hesitation allowed
    user_text: str = ""               # last caller utterance (for emotion sensing)

    def lang(self) -> str:
        return norm_lang(self.language)


@dataclass
class Segment:
    """One spoken chunk plus the pause that should follow it."""
    text: str
    pause: PauseType | None = None


@dataclass
class SpokenPlan:
    """Final output handed back to the manager → TTS."""
    text: str                          # Sarvam-ready string
    language: str
    pace: float                        # absolute Sarvam pace for this utterance
    style: str = "default"             # StyleName.value
    emotion: str = "neutral"           # Emotion.value
    segments: list[Segment] = field(default_factory=list)
    raw_in: str = ""                   # original text, for before/after eval
    notes: list[str] = field(default_factory=list)  # transforms applied

    def is_empty(self) -> bool:
        return not self.text.strip()
