"""Regression tests for a real production bug (session faefdb53dbff): a
recognized, VERIFIED caller (Rounit, matched via caller ID at call start)
asked for a brand-new connection. Across all three turns the agent never
once asked for the caller's name and never offered/confirmed a contact
number — it silently pulled "Rounit Singh" and the caller-ID mobile out of
CALL MEMORY and called register_new_connection the instant it had an
address and PIN code. The caller's complaint: "it registered my request
without asking my phone number my name and any other detail."

Root cause: `_verified_caller_directive()` unconditionally tells the model
"never ask for their account number or mobile — you already have them. Go
straight to solving their problem." That's correct for an EXISTING-account
lookup/issue, but a NEW CONNECTION request is a separate thing being
submitted to the installation team — its name/contact fields must still be
confirmed out loud even for an already-verified caller. The directive and
04_tools.md's NEW CONNECTION bullet were both missing that carve-out.

Fix: both now explicitly state the exception. These tests assert the
carve-out language is present in the directive (and reaches the composed
system prompt) whenever the caller is recognized+verified, and that the
prompt module documents the same override.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.config import get_settings
from backend.app.conversation.manager import ConversationManager
from backend.app.tools.registry import ToolRegistry
from backend.app.tools.telecom import TelecomServices


@pytest.fixture
def manager(tmp_path):
    settings = get_settings()
    svc = TelecomServices(tmp_path / "test.db")
    tools = ToolRegistry(settings, svc)
    return ConversationManager(settings, MagicMock(), tools, "sess_test")


def test_directive_empty_for_unrecognized_caller(manager):
    assert manager._verified_caller_directive() == ""


def test_directive_carves_out_new_connection_for_recognized_verified_caller(manager):
    manager.recognize_caller("9820012345")  # Ramesh Patil's registered mobile
    directive = manager._verified_caller_directive()
    assert "NEW CONNECTION" in directive
    assert "does NOT apply to it" in directive
    assert "ask for the caller's full name" in directive
    assert "OFFER/confirm the contact number" in directive
    # The original "don't re-ask for verification" behaviour must still hold
    # for ordinary account lookups — this is a carve-out, not a removal.
    assert "Do NOT ask them to verify" in directive


def test_new_connection_carveout_reaches_composed_system_prompt(manager):
    manager.recognize_caller("9820012345")
    messages = manager._messages()
    system = messages[0]["content"]
    assert "NEW CONNECTION" in system
    assert "does NOT apply to it" in system


def test_directive_absent_for_recognized_but_unverified_caller(manager):
    manager.recognize_caller("9820012345")
    manager.memory.verified = False
    assert manager._verified_caller_directive() == ""


def _read_tools_module() -> str:
    path = (
        Path(__file__).resolve().parents[1]
        / "app" / "prompts" / "modules" / "04_tools.md"
    )
    return path.read_text(encoding="utf-8")


def test_tools_module_documents_the_recognized_caller_override():
    text = _read_tools_module()
    assert "THIS APPLIES EVEN TO A CALLER ALREADY RECOGNIZED/VERIFIED" in text
    assert "does NOT cover it" in text
    assert "never silently reuse a name or mobile from CALL MEMORY" in text
