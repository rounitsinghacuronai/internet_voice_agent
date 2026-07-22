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
    """Exact scenario from the spec: caller is mid-fault-report, a TV in the
    background says something bill-related — must NOT switch topics."""
    topic = TopicStability()
    topic.update("my internet not working since morning")  # -> internet
    assert topic.active == "internet"
    topic.update("bill")  # single stray word, background noise
    assert topic.active == "internet"  # unchanged


def test_topic_stability_switches_after_consistent_new_topic():
    topic = TopicStability()
    topic.update("net nahi chal raha hai kal se")
    assert topic.active == "internet"
    topic.update("mera bill bahut zyada aaya hai")
    topic.update("bill ka payment nahi hua abhi tak")
    assert topic.active == "billing"


def test_topic_directive_present_once_active():
    topic = TopicStability()
    topic.update("signal nahi aa raha hai")
    assert "network" in topic.directive()


def test_new_connection_intent_not_read_as_broken_internet():
    """A caller ORDERING service names broadband/fiber/wifi/router — these must
    NOT be classified as an existing-service internet FAULT (which injected an
    out-of-context 'sorry your wifi isn't working' troubleshooting directive)."""
    from backend.app.conversation.robustness import detect_topic
    assert detect_topic("I want to take a new broadband connection") == "new_connection"
    assert detect_topic("a new fiber connection please") == "new_connection"
    # bare product noun without a fault cue is NOT an internet-trouble topic
    assert detect_topic("is a wifi router included") != "internet"
    assert detect_topic("which fiber plans do you have") != "internet"
    # a genuine fault still routes to internet
    assert detect_topic("my internet not working since morning") == "internet"
    assert detect_topic("wifi is very slow today") == "internet"


def test_new_connection_topic_stays_locked_through_product_talk():
    """Full call flow: the active topic must remain new_connection while the
    caller discusses fiber plans / wifi router — never flip to an internet fault."""
    topic = TopicStability()
    for u in ["hi I want a new connection", "yes for broadband at home",
              "which fiber plans do you have", "is the wifi router included",
              "how soon can you install it"]:
        topic.update(u)
    assert topic.active == "new_connection"
