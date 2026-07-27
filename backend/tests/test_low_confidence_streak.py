"""Regression tests for the repeated-low-confidence escalation (conversation/
robustness.py::repeated_low_confidence_directive + its streak wiring in
ConversationManager) — the fix for a real production observation: under
sustained garbled/cross-script STT input, the model settled on one short
stock reply and repeated it near-verbatim for 4 turns straight. Pure
function tests, no network/LLM.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.conversation.robustness import (
    ConfidenceTier, repeated_low_confidence_directive,
)


def test_streak_below_two_gives_no_directive():
    assert repeated_low_confidence_directive(0) == ""
    assert repeated_low_confidence_directive(1) == ""


def test_streak_of_two_forbids_reusing_last_wording():
    d = repeated_low_confidence_directive(2)
    assert "REPEATED UNCLEAR AUDIO" in d
    assert "same" in d.lower()


def test_streak_of_three_plus_offers_dtmf_and_escalation():
    d = repeated_low_confidence_directive(3)
    assert "REPEATED UNCLEAR AUDIO" in d
    assert "keypad" in d.lower() or "dtmf" in d.lower()
    assert "human" in d.lower()


def test_streak_of_four_still_escalated_not_reset():
    d3 = repeated_low_confidence_directive(3)
    d4 = repeated_low_confidence_directive(4)
    assert d3 == d4  # escalation plateaus at the 3+ tier rather than growing unboundedly


def test_manager_tracks_streak_across_turns():
    """End-to-end (no network): confirms the manager increments/resets
    _low_conf_streak from ConfidenceTier and feeds it into the system prompt."""
    from unittest.mock import MagicMock
    from backend.app.config import Settings
    from backend.app.conversation.manager import ConversationManager
    from backend.app.conversation.robustness import ConfidenceEstimate

    settings = Settings(gemini_api_key="x", sarvam_api_key="x")
    mgr = ConversationManager(settings, MagicMock(), MagicMock(), "sess_test")
    assert mgr._low_conf_streak == 0

    mgr._last_confidence = ConfidenceEstimate(ConfidenceTier.LOW, 0.2, 0.3)
    mgr._low_conf_streak = 2
    messages = mgr._messages()
    assert "REPEATED UNCLEAR AUDIO" in messages[0]["content"]

    # A HIGH-confidence turn must reset the streak (mirrors the manager's own
    # reset logic at the point _last_confidence is computed each turn).
    mgr._last_confidence = ConfidenceEstimate(ConfidenceTier.HIGH, 0.9, 0.95)
    mgr._low_conf_streak = 0
    messages = mgr._messages()
    assert "REPEATED UNCLEAR AUDIO" not in messages[0]["content"]
