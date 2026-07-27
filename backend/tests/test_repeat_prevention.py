"""Regression tests for the deterministic 'already done this call' directive
(conversation/manager.py::_already_done_directive) — the fix for callers being
asked to restart their router (or repeat any troubleshooting step) more than
once per call. Pure function, no network/LLM — mirrors test_escalation.py's
offline style.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.conversation.manager import _already_done_directive


def test_empty_tools_used_gives_no_directive():
    assert _already_done_directive([]) == ""


def test_unknown_tool_gives_no_directive():
    # A tool with no _TOOL_LABEL entry (e.g. a read that isn't troubleshooting-
    # relevant) must not produce an empty/garbled directive.
    assert _already_done_directive(["some_untracked_tool"]) == ""


def test_restart_already_done_is_called_out_explicitly():
    d = _already_done_directive(["restart_ont"])
    assert "ALREADY DONE" in d
    assert "Remote ONT restart" in d
    assert "never suggest or offer one again" in d.lower() or \
           "never suggest or offer one again" in d


def test_second_restart_offer_is_explicitly_forbidden():
    # This is the exact regression: router restart offered a second time.
    d = _already_done_directive(["get_network_status", "restart_ont"])
    assert "restart" in d.lower()
    assert "again" in d.lower()


def test_multiple_tools_deduplicated_and_ordered():
    d = _already_done_directive(["get_network_status", "get_network_status",
                                  "run_line_diagnostics", "restart_ont"])
    # Each distinct tool's label appears exactly once even though
    # get_network_status was called twice.
    assert d.count("Area outage check") == 1
    assert "Line diagnostics" in d
    assert "Remote ONT restart" in d


def test_non_troubleshooting_tool_calls_dont_falsely_trigger_restart_language():
    # Billing/account tools were called, but restart never happened — the
    # restart-specific callout must NOT appear.
    d = _already_done_directive(["get_bill", "get_payment_status"])
    assert "Bill lookup" in d
    assert "never suggest or offer one again" not in d


def test_directive_wired_into_prompt_messages():
    """End-to-end (no network): a ConversationManager with tools already used
    must include the directive in the system prompt built for the next turn."""
    from unittest.mock import MagicMock
    from backend.app.config import Settings
    from backend.app.conversation.manager import ConversationManager

    settings = Settings(gemini_api_key="x", sarvam_api_key="x")
    mgr = ConversationManager(settings, MagicMock(), MagicMock(), "sess_test")
    mgr._tools_used = ["restart_ont"]
    messages = mgr._messages()
    system_content = messages[0]["content"]
    assert "restart_ont" not in system_content  # tool name itself isn't leaked
    assert "Remote ONT restart" in system_content
    assert "never suggest or offer one again" in system_content
