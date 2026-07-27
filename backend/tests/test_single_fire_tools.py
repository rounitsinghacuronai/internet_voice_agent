"""Regression tests for the duplicate-order guard (conversation/manager.py::
_already_done_directive's _SINGLE_FIRE_TOOLS handling) — the fix for a real
production bug: register_new_connection fired twice in one call because a
barge-in cut off the confirmation before the caller heard it, and the agent
had no memory that the first attempt already succeeded. Pure function tests,
no network/LLM — mirrors test_repeat_prevention.py's offline style.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.conversation.manager import _already_done_directive, _SINGLE_FIRE_TOOLS


def test_single_fire_tools_set_contains_the_reported_bug_tool():
    assert "register_new_connection" in _SINGLE_FIRE_TOOLS


def test_new_connection_already_done_forbids_resubmission():
    d = _already_done_directive(["register_new_connection"])
    assert "New connection registered" in d
    assert "DUPLICATE" in d
    assert "again" in d.lower()


def test_plan_change_is_not_treated_as_single_fire():
    # A caller re-requesting a DIFFERENT plan mid-call is legitimate, not a
    # duplicate — request_plan_change must not trigger the single-fire block.
    d = _already_done_directive(["request_plan_change"])
    assert "Plan change" in d
    assert "DUPLICATE" not in d


def test_sim_swap_and_block_sim_are_single_fire():
    for tool, label in (("request_sim_swap", "SIM/eSIM swap"), ("block_sim", "SIM blocked")):
        d = _already_done_directive([tool])
        assert label in d
        assert "DUPLICATE" in d


def test_single_fire_directive_survives_intervening_tool_calls():
    # Mirrors the real production sequence: register, then other lookups
    # happen, then the caller repeats themselves — the guard must still hold.
    d = _already_done_directive(
        ["register_new_connection", "get_new_connection_status", "search_knowledge"])
    assert "New connection registered" in d
    assert "DUPLICATE" in d


def test_directive_wired_into_prompt_messages_for_new_connection():
    """End-to-end (no network): a ConversationManager that already registered
    a new connection this call must carry the duplicate-order warning into
    the system prompt for the next turn."""
    from unittest.mock import MagicMock
    from backend.app.config import Settings
    from backend.app.conversation.manager import ConversationManager

    settings = Settings(gemini_api_key="x", sarvam_api_key="x")
    mgr = ConversationManager(settings, MagicMock(), MagicMock(), "sess_test")
    mgr._tools_used = ["register_new_connection"]
    messages = mgr._messages()
    system_content = messages[0]["content"]
    # (Unlike restart_ont, "register_new_connection" legitimately appears
    # elsewhere in the static tool-calling instructions, so we only check
    # that the human-readable duplicate-order warning is present.)
    assert "New connection registered" in system_content
    assert "DUPLICATE" in system_content
