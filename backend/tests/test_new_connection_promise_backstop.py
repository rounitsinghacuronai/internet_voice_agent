"""Regression tests for the new-connection-registration promise backstop
(conversation/manager.py::_promised_new_connection / _force_new_connection_next).

Real production bug (session acbfc95ee8dc, journalctl): the agent told the
caller their new-connection request was being registered — TWICE, in two
different turns, with two different phrasings ("... दर्ज कर रहा हूँ" and
"मैं यह अनुरोध दर्ज कर रहा हूँ ... आपको एक एप्लीकेशन नंबर मिलेगा") — and
never once called register_new_connection (tools=none both turns). The
caller was promised an application number that never existed. Mirrors the
existing transfer-promise backstop (_promised_transfer/_force_transfer_next),
which has the same shape and no direct test either — pure function tests
for the detector, plus a directive-wiring test in the same style as
test_single_fire_tools.py's test_directive_wired_into_prompt_messages_for_new_connection.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.conversation.manager import _promised_new_connection


# ── the exact production evidence ────────────────────────────────────────────

def test_production_turn_4_text_is_detected():
    text = ("ठीक है, मैं आपका नाम Rounit Singh, पता Pride World City, J-102, "
            "Long Island, Pune 411047, और ब्रॉडबैंड कनेक्शन के लिए इसी नंबर "
            "7267850755 पर संपर्क करने का अनुरोध दर्ज कर रहा हूँ। आपको किसी "
            "खास प्लान की जानकारी चाहिए या मैं आपके लिए सबसे अच्छा प्लान "
            "बताऊँ?")
    assert _promised_new_connection(text)


def test_production_turn_5_text_is_detected():
    text = ("ठीक है, मैं यह अनुरोध दर्ज कर रहा हूँ। हमारी टीम जल्द ही आपसे "
            "संपर्क करेगी। आपको एक एप्लीकेशन नंबर मिलेगा जिससे आप अपने "
            "आवेदन की स्थिति ट्रैक कर सकते हैं। और कुछ सहायता चाहिए क्या?")
    assert _promised_new_connection(text)


def test_english_phrasing_is_detected():
    assert _promised_new_connection(
        "Okay, I'm registering your new connection request now.")
    assert _promised_new_connection(
        "I'm submitting your application right away.")


# ── negative cases: must not false-positive on unrelated tool promises ──────

def test_complaint_registration_mentioning_connection_is_not_flagged():
    """A COMPLAINT about an existing connection, not a NEW connection request
    — must not trip the new-connection backstop."""
    text = "मैं शिकायत दर्ज कर रहा हूँ, आपका कनेक्शन जल्द ठीक हो जाएगा।"
    assert not _promised_new_connection(text)


def test_bare_mention_of_connection_without_register_verb_is_not_flagged():
    assert not _promised_new_connection(
        "आपका ब्रॉडबैंड कनेक्शन ठीक चल रहा है।")


def test_register_verb_without_any_connection_context_is_not_flagged():
    assert not _promised_new_connection("ठीक है, मैं आपकी मदद करता हूँ।")


def test_empty_text_is_not_flagged():
    assert not _promised_new_connection("")
    assert not _promised_new_connection(None)


# ── directive wiring: mirrors test_single_fire_tools.py's established style ──

def test_directive_wired_into_prompt_messages():
    """End-to-end (no network): once the backstop is armed for this turn, the
    forced-registration directive must reach the system prompt."""
    from unittest.mock import MagicMock
    from backend.app.config import Settings
    from backend.app.conversation.manager import ConversationManager

    settings = Settings(gemini_api_key="x", sarvam_api_key="x")
    mgr = ConversationManager(settings, MagicMock(), MagicMock(), "sess_test")
    assert mgr._force_new_connection_next is False   # default
    assert mgr._new_connection_directive == ""       # default
    mgr._new_connection_directive = (
        "[UNFULFILLED PROMISE] Last turn you told the caller their new "
        "connection request was being registered/submitted, but "
        "register_new_connection was never actually called.")
    messages = mgr._messages()
    system_content = messages[0]["content"]
    assert "UNFULFILLED PROMISE" in system_content
    assert "register_new_connection" in system_content
