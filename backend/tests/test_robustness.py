"""Conversation Robustness Layer — confidence tiering and topic stability.
Pure/offline; no API calls involved."""
from __future__ import annotations

from backend.app.conversation.robustness import (
    ConfidenceTier,
    TopicStability,
    estimate_confidence,
)


def test_high_confidence_when_both_signals_strong():
    est = estimate_confidence(peak_prob=0.9, language_confidence=0.95)
    assert est.tier is ConfidenceTier.HIGH
    assert est.directive() == ""  # nothing injected — proceed normally


def test_low_confidence_when_audio_unclear():
    est = estimate_confidence(peak_prob=0.2, language_confidence=0.9)
    assert est.tier is ConfidenceTier.LOW
    assert "LOW" in est.directive()
    assert "repeat the whole sentence" in est.directive()


def test_medium_confidence_between_thresholds():
    est = estimate_confidence(peak_prob=0.6, language_confidence=0.7)
    assert est.tier is ConfidenceTier.MEDIUM
    assert "MEDIUM" in est.directive()


def test_missing_language_confidence_does_not_drag_down_tier():
    """When a fixed language_code is used, Sarvam returns no language_probability
    at all — absence must not be treated as low confidence."""
    est = estimate_confidence(peak_prob=0.9, language_confidence=None)
    assert est.tier is ConfidenceTier.HIGH


def test_topic_stability_ignores_single_stray_background_word():
    """Exact scenario from the spec: caller is mid-outage-report, a TV in the
    background says something bill-related — must NOT switch topics."""
    topic = TopicStability()
    topic.update("my electricity has been gone since morning")  # -> outage
    assert topic.active == "outage"
    topic.update("bill")  # single stray word, background noise
    assert topic.active == "outage"  # unchanged


def test_topic_stability_switches_after_consistent_new_topic():
    topic = TopicStability()
    topic.update("light gaya hai kal se")
    assert topic.active == "outage"
    topic.update("mera bill bahut zyada aaya hai")
    topic.update("bill ka payment nahi hua abhi tak")
    assert topic.active == "billing"


def test_topic_directive_present_once_active():
    topic = TopicStability()
    topic.update("current nahi aa raha hai")
    assert "outage" in topic.directive()
