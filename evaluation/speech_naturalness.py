"""Before / after demo + naturalness report for the Human Speech Generation Engine.

Runs a set of realistic MSEDCL replies — written the way a raw LLM tends to
produce them ("reads like text") — through the engine and prints the spoken
result side by side, then a naturalness metrics comparison.

    python -m evaluation.speech_naturalness           # from repo root

No API keys or network needed — the whole engine is deterministic.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.config import Settings
from backend.app.speech import SpeechContext, SpeechDirector, compare

# (raw LLM line, context) — contexts mirror what the ConversationManager builds.
CASES: list[tuple[str, SpeechContext]] = [
    (
        "महावितरण ग्राहक सेवा केंद्रात आपले स्वागत आहे. मी प्रिया, आपली कशा प्रकारे मदत करू शकते?",
        SpeechContext(
            language="mr", turn_no=0, is_greeting=True, is_first_utterance=True
        ),
    ),
    (
        "I understand. I will now verify your consumer account. Please note that your "
        "consumer number is 170012345678.",
        SpeechContext(
            language="en",
            turn_no=1,
            is_first_utterance=True,
            asking_for_number=None,
            processing=True,
        ),
    ),
    (
        "Your complaint has been successfully registered and the estimated restoration "
        "time is thirty minutes.",
        SpeechContext(
            language="en",
            turn_no=3,
            is_first_utterance=True,
            just_registered_complaint=True,
        ),
    ),
    (
        "आपके बिल की राशि दो हज़ार तीन सौ चालीस रुपये है और यह पंद्रह तारीख को देय है, "
        "कृपया समय पर भुगतान कर दीजिए ताकि आपको लेट फ़ीस न लगे.",
        SpeechContext(
            language="hi",
            turn_no=2,
            is_first_utterance=True,
            topic="billing",
            user_text="mera bill bahut zyada aaya hai har baar",
        ),
    ),
    (
        "Please wait while I check the outage in your area.",
        SpeechContext(
            language="en",
            turn_no=2,
            is_first_utterance=True,
            topic="outage",
            processing=True,
        ),
    ),
    (
        "तुमची तक्रार नोंदवली आहे, तुमचा तक्रार क्रमांक आहे आणि टीमला कळवलं आहे, "
        "ते दोन तासांत पोहोचतील.",
        SpeechContext(
            language="mr",
            turn_no=4,
            is_first_utterance=True,
            just_registered_complaint=True,
        ),
    ),
]


def main() -> None:
    sd = SpeechDirector(
        Settings(tts_pace=1.0, speech_pace_min=0.7, speech_pace_max=1.15)
    )
    before, after, plans = [], [], []

    print("=" * 78)
    print("HUMAN SPEECH GENERATION ENGINE — before / after")
    print("=" * 78)
    for raw, ctx in CASES:
        plan = sd.plan_line(raw, ctx)
        before.append(raw)
        after.append(plan.text)
        plans.append(plan)
        print(f"\n[{plan.style} · {plan.emotion} · pace {plan.pace}]")
        print(f"  BEFORE: {raw}")
        print(f"  AFTER : {plan.text}")
        if plan.notes:
            print(f"  applied: {', '.join(plan.notes)}")

    print("\n" + "=" * 78)
    print("NATURALNESS METRICS")
    print("=" * 78)
    report = compare(before, after, after_plans=plans)
    keys = [
        "naturalness_score",
        "ai_pattern_hits",
        "avg_words_per_sentence",
        "pause_density",
        "rhythm_cv",
        "ack_diversity",
        "repetition_rate",
        "pause_type_variety",
    ]
    print(f"{'metric':<24}{'before':>12}{'after':>12}{'delta':>12}")
    print("-" * 60)
    for k in keys:
        b, a, d = report["before"][k], report["after"][k], report["delta"][k]
        print(f"{k:<24}{b:>12}{a:>12}{d:>+12}")
    print()


if __name__ == "__main__":
    main()
