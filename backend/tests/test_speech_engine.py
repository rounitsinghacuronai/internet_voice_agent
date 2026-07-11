"""Human Speech Generation Engine + Voice Director — offline unit tests.

Pure/deterministic: no API keys, no network. Covers de-AI rewriting, spoken
number formatting, Voice Director style selection, caller-emotion adaptation,
acknowledgement/hesitation lead-ins (and the no-double-ack guard), thought
grouping, prosody/intonation, Sarvam pace planning, and the naturalness
evaluator's before/after signal.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.config import Settings
from backend.app.speech import (
    SpeechContext,
    SpeechDirector,
    SpeechNaturalnessEvaluator,
    StyleName,
    VariationTracker,
    compare,
    detect_caller_emotion,
    format_numbers_for_speech,
)
from backend.app.speech.director import VoiceDirector
from backend.app.speech.optimizer import ResponseOptimizer
from backend.app.speech.plan import PauseType, Segment


def _director(base_pace: float = 1.0) -> SpeechDirector:
    # explicit settings so pace assertions don't depend on the repo .env
    s = Settings(tts_pace=base_pace, speech_pace_min=0.7, speech_pace_max=1.15)
    return SpeechDirector(s)


# ── Response Optimizer: de-AI + spoken forms ─────────────────────────────────
def test_deai_rewrites_apology_and_please_note():
    opt = ResponseOptimizer()
    out, notes = opt.clean("I apologize. Please note that your bill is due.", "en")
    assert "apolog" not in out.lower()
    assert "please note" not in out.lower()
    assert "I'm sorry" in out and "de-AI" in notes


def test_deai_rewrites_successfully_registered():
    opt = ResponseOptimizer()
    out, _ = opt.clean("Your complaint has been successfully registered.", "en")
    assert "successfully" not in out.lower()
    assert "is registered" in out


def test_spoken_contractions_english_only():
    opt = ResponseOptimizer()
    out, notes = opt.clean("It is done and I will call you.", "en")
    assert "it's" in out.lower() and "i'll" in out.lower()
    assert out.startswith("It's")  # capitalization preserved at start
    assert "contraction" in notes


def test_clean_strips_parentheses_and_markdown():
    opt = ResponseOptimizer()
    out, _ = opt.clean("Your bill is **2340** (approx.) rupees.", "en")
    assert "(" not in out and "*" not in out
    assert "2340" in out


# ── Number Pronunciation Planning (output side) ──────────────────────────────
def test_consumer_number_grouped_444():
    out, changed = format_numbers_for_speech(
        "Your consumer number is 170012345678.", "en"
    )
    assert changed
    assert "1 7 0 0, 1 2 3 4, 5 6 7 8" in out


def test_mobile_number_grouped_55():
    out, changed = format_numbers_for_speech("Registered mobile 9820012345.", "en")
    assert changed and "9 8 2 0 0, 1 2 3 4 5" in out


def test_otp_grouped_33():
    out, _ = format_numbers_for_speech("Your OTP is 480216.", "en")
    assert "4 8 0, 2 1 6" in out


def test_amount_is_not_digit_grouped():
    out, changed = format_numbers_for_speech("Your bill is Rs 23400 this month.", "en")
    assert not changed and "23400" in out


def test_number_formatting_is_idempotent():
    once, _ = format_numbers_for_speech("consumer 170012345678", "en")
    twice, _ = format_numbers_for_speech(once, "en")
    assert once == twice


# ── Voice Director: style selection ──────────────────────────────────────────
def test_director_picks_emergency():
    d = VoiceDirector()
    assert d.direct(SpeechContext(is_emergency=True)).name is StyleName.EMERGENCY


def test_director_picks_greeting_and_closing():
    d = VoiceDirector()
    assert d.direct(SpeechContext(is_greeting=True)).name is StyleName.GREETING
    assert d.direct(SpeechContext(is_closing=True)).name is StyleName.CLOSING


def test_director_picks_verification_when_asking_for_number():
    d = VoiceDirector()
    p = d.direct(SpeechContext(asking_for_number="consumer_no", verified=False))
    assert p.name is StyleName.VERIFICATION
    assert p.number_pace <= 0.8  # slower for numbers


def test_director_picks_complaint_registered():
    d = VoiceDirector()
    p = d.direct(SpeechContext(just_registered_complaint=True, verified=True))
    assert p.name is StyleName.COMPLAINT_REGISTERED


def test_director_topic_styles():
    d = VoiceDirector()
    assert (
        d.direct(SpeechContext(topic="outage", verified=True)).name is StyleName.OUTAGE
    )
    assert (
        d.direct(SpeechContext(topic="billing", verified=True)).name
        is StyleName.BILLING
    )


# ── caller-emotion sensing + adaptation ──────────────────────────────────────
def test_detect_angry_and_worried():
    assert detect_caller_emotion("light kab tak, har baar yahi hota hai") == "angry"
    assert detect_caller_emotion("this is the worst service") == "angry"
    assert detect_caller_emotion("I'm scared there's a dangerous wire") == "worried"
    assert detect_caller_emotion("mera bill dekhna hai") is None


def test_angry_caller_gets_patient_slower_delivery():
    d = VoiceDirector()
    p = d.direct(
        SpeechContext(
            topic="billing", verified=True, user_text="kab tak, har baar yahi hota hai"
        )
    )
    assert p.emotion.value == "patient"
    assert p.pace <= 0.94


# ── engine: the LLM's own words pass through — no canned prepends ─────────────
# Canned rotated lead-ins ("Alright…", "Let me just check…") were removed: the
# mechanical prepend made every turn open with a stock phrase, which is the #1
# audible bot tell. Openers are the LLM's job now (prompts/modules/02_style.md).
def test_no_canned_lead_in_injected_on_first_line():
    sd = _director()
    ctx = SpeechContext(
        language="en", turn_no=2, is_first_utterance=True, topic="billing"
    )
    plan = sd.plan_line("Your bill is due on the fifteenth.", ctx)
    assert plan.text.lower().startswith("your bill")


def test_llm_own_opener_passes_through_untouched():
    sd = _director()
    ctx = SpeechContext(
        language="en", turn_no=2, is_first_utterance=True, topic="billing"
    )
    plan = sd.plan_line("Alright, your bill is due on the fifteenth.", ctx)
    assert plan.text.lower().startswith("alright")


def test_no_hesitation_injected_even_when_processing():
    sd = _director()
    ctx = SpeechContext(
        language="en",
        turn_no=2,
        is_first_utterance=True,
        topic="billing",
        processing=True,
    )
    plan = sd.plan_line("Your bill is two thousand rupees.", ctx)
    assert plan.text.lower().startswith("your bill")


def test_not_first_line_gets_no_lead():
    sd = _director()
    ctx = SpeechContext(
        language="en", turn_no=2, is_first_utterance=False, topic="billing"
    )
    plan = sd.plan_line("The technician will visit tomorrow.", ctx)
    assert not plan.text.startswith("…")


# ── engine: thought grouping for breathing ───────────────────────────────────
def test_long_line_is_broken_into_thought_groups():
    sd = _director()
    ctx = SpeechContext(language="en", turn_no=3, is_first_utterance=False)
    long = (
        "your complaint is registered, the field team has already been informed, "
        "they will reach your area within about two hours, and you will get an SMS "
        "update with the technician details shortly after they are assigned"
    )
    assert len(long) > 150
    plan = sd.plan_line(long, ctx)
    assert len(plan.segments) >= 2


# ── prosody: question intonation invites the caller back ─────────────────────
def test_question_gets_terminal_question_mark():
    sd = _director()
    ctx = SpeechContext(language="en", turn_no=1, is_first_utterance=False)
    plan = sd.plan_line("Could you share your consumer number", ctx)
    assert plan.text.rstrip().endswith("?")
    assert plan.segments[-1].pause is PauseType.LISTENING


# ── formatter: preserve wording, pace planning ───────────────────────────────
def test_preserve_wording_keeps_text_but_sets_pace():
    sd = _director()
    line = "कृपया त्या ठिकाणापासून लगेच दूर व्हा."
    plan = sd.render_fixed(line, "mr", StyleName.EMERGENCY)
    assert line.rstrip(".") in plan.text  # wording intact
    assert plan.style == "emergency"


def test_pace_drops_for_long_numbers():
    sd = _director(base_pace=1.0)
    ctx = SpeechContext(
        language="en", turn_no=1, is_first_utterance=False, verified=True
    )
    plan = sd.plan_line("Your consumer number is 170012345678.", ctx)
    assert plan.pace <= 0.86  # dropped toward number_pace for clarity


def test_pace_is_clamped():
    sd = _director(base_pace=2.0)  # absurd base
    ctx = SpeechContext(language="en", turn_no=1, is_first_utterance=False)
    plan = sd.plan_line("All done for you.", ctx)
    assert plan.pace <= 1.15


# ── variation: no back-to-back repetition ────────────────────────────────────
def test_variation_never_repeats_back_to_back():
    v = VariationTracker()
    opts = ["Alright", "Okay", "Sure"]
    picks = [v.pick("ack:en", opts) for _ in range(6)]
    assert all(a != b for a, b in zip(picks, picks[1:]))


# ── evaluator: before/after shows improvement ────────────────────────────────
def test_evaluator_shows_improvement_after_engine():
    sd = _director()
    before = [
        "Your complaint has been successfully registered and the estimated "
        "restoration time is thirty minutes.",
        "I understand. I will now verify your consumer account. Please note that "
        "your consumer number is 170012345678.",
    ]
    after, plans = [], []
    for i, line in enumerate(before):
        ctx = SpeechContext(
            language="en",
            turn_no=i + 1,
            is_first_utterance=True,
            just_registered_complaint=(i == 0),
            processing=True,
        )
        p = sd.plan_line(line, ctx)
        after.append(p.text)
        plans.append(p)

    result = compare(before, after, after_plans=plans)
    assert result["after"]["ai_pattern_hits"] < result["before"]["ai_pattern_hits"]
    assert result["after"]["naturalness_score"] >= result["before"]["naturalness_score"]


def test_evaluator_scores_are_bounded():
    ev = SpeechNaturalnessEvaluator()
    m = ev.evaluate(["Alright… your bill is due on the fifteenth."])
    assert 0.0 <= m.naturalness_score <= 100.0
