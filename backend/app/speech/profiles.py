"""Style-profile library — the Voice Director's palette.

Each conversation phase gets a distinct 'performance': greeting is warm and
medium-paced; verification is deliberate and slower so numbers land; an outage
is calm and concerned; an emergency is calm but direct. Caller-emotion
modifiers layer on top (an angry caller is met with more patience and a
slightly slower, steadier pace — never matched heat).
"""
from __future__ import annotations

from dataclasses import replace

from .plan import Emotion, StyleName, StyleProfile

# ── base profiles, one per conversation phase ────────────────────────────────
PROFILES: dict[StyleName, StyleProfile] = {
    StyleName.GREETING: StyleProfile(
        name=StyleName.GREETING, emotion=Emotion.WARM,
        pace=1.0, pause_scale=1.0, warmth=0.9,
        lead_in=False, hesitation_ok=False, number_pace=0.9,
        preserve_wording=True, label="warm, welcoming, medium pace",
    ),
    StyleName.VERIFICATION: StyleProfile(
        name=StyleName.VERIFICATION, emotion=Emotion.HELPFUL,
        pace=0.95, pause_scale=1.2, warmth=0.6,
        lead_in=True, hesitation_ok=True, number_pace=0.8,
        max_thought_chars=130, label="clear, deliberate, slower for numbers",
    ),
    StyleName.OUTAGE: StyleProfile(
        name=StyleName.OUTAGE, emotion=Emotion.CONCERNED,
        pace=0.97, pause_scale=1.1, warmth=0.8,
        lead_in=True, hesitation_ok=True, number_pace=0.85,
        label="calm, reassuring",
    ),
    StyleName.BILLING: StyleProfile(
        name=StyleName.BILLING, emotion=Emotion.HELPFUL,
        pace=0.97, pause_scale=1.1, warmth=0.65,
        lead_in=True, hesitation_ok=True, number_pace=0.85,
        max_thought_chars=160, label="patient, explanatory",
    ),
    StyleName.COMPLAINT_REGISTERED: StyleProfile(
        name=StyleName.COMPLAINT_REGISTERED, emotion=Emotion.CONFIDENT,
        pace=0.98, pause_scale=1.15, warmth=0.75,
        lead_in=False, hesitation_ok=False, number_pace=0.78,
        label="confident, reassuring",
    ),
    StyleName.EMERGENCY: StyleProfile(
        name=StyleName.EMERGENCY, emotion=Emotion.CALM_URGENT,
        pace=0.98, pause_scale=0.9, warmth=0.7,
        lead_in=False, hesitation_ok=False, number_pace=0.8,
        preserve_wording=True, max_thought_chars=120,
        label="calm, direct, urgent",
    ),
    StyleName.ESCALATION: StyleProfile(
        name=StyleName.ESCALATION, emotion=Emotion.REASSURING,
        pace=0.97, pause_scale=1.05, warmth=0.75,
        lead_in=True, hesitation_ok=False, number_pace=0.82,
        label="steady, reassuring hand-off",
    ),
    StyleName.CLOSING: StyleProfile(
        name=StyleName.CLOSING, emotion=Emotion.WARM,
        pace=1.02, pause_scale=1.0, warmth=0.9,
        lead_in=False, hesitation_ok=False, number_pace=0.9,
        label="friendly, concise",
    ),
    StyleName.DEFAULT: StyleProfile(
        name=StyleName.DEFAULT, emotion=Emotion.HELPFUL,
        pace=1.0, pause_scale=1.0, warmth=0.65,
        lead_in=True, hesitation_ok=True, number_pace=0.85,
        label="warm, professional",
    ),
}


def base_profile(name: StyleName) -> StyleProfile:
    return PROFILES.get(name, PROFILES[StyleName.DEFAULT])


# ── caller-emotion modifiers (subtle — never theatrical) ──────────────────────
def apply_caller_emotion(profile: StyleProfile, caller_emotion: str | None) -> StyleProfile:
    """Adjust a base profile for how the CALLER sounds. Mirrors the caller in
    warmth, never in anger: an angry caller gets more patience and a steadier,
    slightly slower pace, not a matched edge."""
    if not caller_emotion:
        return profile

    if caller_emotion == "angry":
        # Stay calm, slow slightly, keep it steady. Patience over empathy-spam.
        return replace(profile, emotion=Emotion.PATIENT,
                       pace=min(profile.pace, 0.94), pause_scale=max(profile.pause_scale, 1.1),
                       lead_in=True)
    if caller_emotion == "frustrated":
        return replace(profile, emotion=Emotion.APOLOGETIC,
                       pace=min(profile.pace, 0.96), warmth=max(profile.warmth, 0.8),
                       lead_in=True)
    if caller_emotion == "elderly":
        # Extra patience and warmth, one simple step at a time, numbers slower.
        return replace(profile, emotion=Emotion.PATIENT,
                       pace=min(profile.pace, 0.9), pause_scale=max(profile.pause_scale, 1.25),
                       number_pace=min(profile.number_pace, 0.75),
                       max_thought_chars=min(profile.max_thought_chars, 120), warmth=0.85)
    if caller_emotion == "worried":
        # Drop small talk, become calm and steady and reassuring.
        return replace(profile, emotion=Emotion.REASSURING,
                       pace=min(profile.pace, 0.96), warmth=max(profile.warmth, 0.8))
    if caller_emotion == "calm":
        # Match their easy energy: efficient, don't over-slow or manufacture empathy.
        return replace(profile, warmth=min(profile.warmth, 0.7))
    return profile
