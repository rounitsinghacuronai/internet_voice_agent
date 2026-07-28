"""Regression tests for a real production bug (session 929a148d33af): the
escalation engine kept re-triggering transfer_to_senior_executive turn after
turn (opening a new ticket each time — ESCCC264025, ESC21A4847D, ESC7A75999B,
ESCF46B7D6E, all in one call) even after the caller explicitly said, in
Hindi, "मेरे को senior अधिकारी से बात नहीं करनी है" — I do NOT want to talk to
a senior official.

Two compounding bugs, both fixed here:

1. _HUMAN_REQUEST (the "customer wants a human" detector) matches on bare
   keywords like "senior" / "अधिकारी" with no regard for a negation modifying
   them, so a REFUSAL sentence containing those same words was read as a
   POSITIVE request for a human — the opposite of what was said.

2. Rule 5 (sustained frustration: mood angry/frustrated + enough failed
   attempts) re-fires on every turn with no memory of an explicit refusal, so
   even fixing (1) alone would not have stopped the very next turn from
   escalating again purely off the caller's mood.

3. Separately, manager.py's promise-backstop ("agent said it was transferring
   but never called the tool, force it next turn") could ALSO override a
   fresh refusal, since it only checked whether the escalation engine's own
   decision was negative, not whether the caller had just declined.

Fix: a new _TRANSFER_REFUSAL pattern, checked first in evaluate() (overriding
_HUMAN_REQUEST and the sentiment rule, but not an objective backend tool
handoff signal), plus the same check gating manager.py's promise-backstop.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.config import get_settings
from backend.app.conversation.escalation import (
    EscalationEngine, _HUMAN_REQUEST, _TRANSFER_REFUSAL,
)
from backend.app.conversation.manager import ConversationManager


# ── _TRANSFER_REFUSAL pattern itself ─────────────────────────────────────────

def test_refusal_pattern_matches_the_production_sentence():
    text = "मेरे को senior अधिकारी से बात नहीं करनी है"
    assert _TRANSFER_REFUSAL.search(text)


@pytest.mark.parametrize("text", [
    "मुझे transfer नहीं चाहिए",
    "नहीं, मुझे किसी senior से बात नहीं करनी",
    "I don't want to talk to a human",
    "no I don't want an agent",
    "mujhe kisi senior se baat nahi karni",
    "please don't transfer me",
])
def test_refusal_pattern_matches_common_phrasings(text):
    assert _TRANSFER_REFUSAL.search(text)


def test_refusal_pattern_does_not_match_a_plain_positive_request():
    """Sanity check: fixing the false positive must not break the true
    positive case _HUMAN_REQUEST exists for."""
    text = "मुझे सीनियर एग्ज़िक्यूटिव से बात करनी है"   # I want to talk to a senior
    assert not _TRANSFER_REFUSAL.search(text)
    assert _HUMAN_REQUEST.search(text)   # still correctly a positive request


def test_human_request_alone_would_have_false_positived_on_the_refusal():
    """Documents WHY the refusal check has to run before _HUMAN_REQUEST:
    the refusal sentence contains _HUMAN_REQUEST's own keywords."""
    text = "मेरे को senior अधिकारी से बात नहीं करनी है"
    assert _HUMAN_REQUEST.search(text)   # the trap this bug fell into


# ── EscalationEngine.evaluate() ──────────────────────────────────────────────

def _engine():
    return EscalationEngine(get_settings())


def test_evaluate_does_not_escalate_on_explicit_refusal():
    decision = _engine().evaluate(
        "मेरे को senior अधिकारी से बात नहीं करनी है", MagicMock())
    assert decision.should_transfer is False


def test_evaluate_refusal_overrides_sustained_frustration_rule():
    """The production scenario exactly: angry mood + enough failed attempts
    would normally trigger rule 5 every turn. A refusal this turn must still
    win."""
    decision = _engine().evaluate(
        "मेरे को senior अधिकारी से बात नहीं करनी है", MagicMock(),
        mood="frustrated", failed_attempts=5)
    assert decision.should_transfer is False


def test_evaluate_still_escalates_on_a_genuine_human_request():
    decision = _engine().evaluate(
        "मुझे सीनियर एग्ज़िक्यूटिव से बात करनी है", MagicMock())
    assert decision.should_transfer is True
    assert decision.source == "customer_request"


def test_evaluate_refusal_does_not_override_a_backend_tool_handoff_signal():
    """An objective backend signal (e.g. fraud detection asking for manual
    review) is not something the caller's own words should be able to
    override — check 1 (tool) still wins over check 1b (refusal)."""
    decision = _engine().evaluate(
        "मेरे को senior अधिकारी से बात नहीं करनी है", MagicMock(),
        last_tool_results=[{"needs_human": True}])
    assert decision.should_transfer is True
    assert decision.source == "tool"


# ── manager.py promise-backstop must also respect a fresh refusal ───────────
#
# End-to-end through the real run_turn() code path (fake streaming LLM, no
# network) — exercises manager.py's actual promise-backstop guard, not a
# re-implementation of its condition.

def _manager_with_fake_llm(reply_text: str = "ठीक है."):
    s = get_settings()

    async def fake_stream(messages, tools=None, temperature=0.4):
        from backend.app.providers.base import LLMDelta
        for word in reply_text.split():
            yield LLMDelta(text=word + " ")
        yield LLMDelta(text="", finish="stop", tool_calls=[])

    llm = MagicMock()
    llm.stream_chat = fake_stream
    tools = MagicMock()
    tools.schemas = []
    tools.dispatch = AsyncMock(return_value={"status": "ok"})
    return ConversationManager(s, llm, tools, session_id="test_refusal")


def test_promise_backstop_does_not_force_transfer_after_a_refusal():
    """Production scenario: the agent promised a transfer last turn but never
    called the tool (_force_transfer_next armed), and the caller uses THIS
    turn to say they don't want one. The promise-backstop must not override
    that refusal."""
    mgr = _manager_with_fake_llm()
    mgr._force_transfer_next = True

    async def run():
        async for _ in mgr.run_turn(
                "मेरे को senior अधिकारी से बात नहीं करनी है", "hi"):
            pass

    asyncio.run(run())
    assert mgr._escalation_decision.should_transfer is False


def test_promise_backstop_still_forces_transfer_without_a_refusal():
    """Sibling case: the existing promise-backstop behaviour must be
    unchanged when the caller does NOT refuse."""
    mgr = _manager_with_fake_llm()
    mgr._force_transfer_next = True

    async def run():
        async for _ in mgr.run_turn("ठीक है", "hi"):
            pass

    asyncio.run(run())
    assert mgr._escalation_decision.should_transfer is True
    assert mgr._escalation_decision.source == "promise_guard"
