"""Regression tests for remembering a returning caller's language across
calls (tools/telecom.py's customer_language table + conversation/manager.py's
recognize_caller()/persist_language_preference()).

Real production case (session 4a7d7c4497e6): a recognized, returning caller
whose language had resolved to Hindi across several EARLIER calls this same
day had their opening line on a NEW call mis-transcribed by STT into the
wrong script entirely (Sarvam auto-detect hallucinating Punjabi — a
recurring, unfixable-on-our-side STT quirk, see test_devanagari_boundary_bug
and the language.py module notes). The Language Engine correctly registered
no signal from that garbled text (lang=und) — but with nothing else to go
on, the reply fell back to the deployment's Marathi house default instead of
the Hindi this caller always actually uses, reading as an unexplained
language change to the caller. Fix: remember what a VERIFIED customer's call
resolved to, and seed the NEXT call with it instead of guessing blind.
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


# ── TelecomServices: storage layer ───────────────────────────────────────────

def test_set_and_read_back_preferred_language(tmp_path):
    svc = TelecomServices(tmp_path / "test.db")
    r = svc.set_preferred_language("300012345678", "hi")
    assert r["updated"] is True
    res = svc.verify_customer(account_no="300012345678")
    assert res["preferred_language"] == "hi"


def test_no_preference_on_file_reads_back_none(tmp_path):
    svc = TelecomServices(tmp_path / "test.db")
    res = svc.verify_customer(account_no="300012345678")
    assert res["verified"] is True
    assert res["preferred_language"] is None


def test_set_preferred_language_upserts_not_duplicates(tmp_path):
    svc = TelecomServices(tmp_path / "test.db")
    svc.set_preferred_language("300012345678", "hi")
    svc.set_preferred_language("300012345678", "mr")   # caller switched language
    res = svc.verify_customer(account_no="300012345678")
    assert res["preferred_language"] == "mr"


def test_invalid_language_is_rejected(tmp_path):
    svc = TelecomServices(tmp_path / "test.db")
    r = svc.set_preferred_language("300012345678", "pa")   # not hi/mr/en
    assert r["updated"] is False


def test_preference_survives_a_fresh_telecomservices_instance(tmp_path):
    """The critical guarantee: customer_language must NOT be reset by the
    customers-table reseed that runs on every TelecomServices() construction
    (i.e. every service restart/deploy)."""
    db = tmp_path / "test.db"
    TelecomServices(db).set_preferred_language("300012345678", "hi")
    # Simulate a service restart: a brand new TelecomServices against the SAME db.
    svc2 = TelecomServices(db)
    res = svc2.verify_customer(account_no="300012345678")
    assert res["preferred_language"] == "hi"


# ── ConversationManager: seed on recognize, persist on teardown ─────────────

@pytest.fixture
def manager(tmp_path):
    settings = get_settings()
    svc = TelecomServices(tmp_path / "test.db")
    tools = ToolRegistry(settings, svc)
    return ConversationManager(settings, MagicMock(), tools, "sess_test")


def test_recognize_caller_seeds_language_from_stored_preference(manager):
    manager.tools.svc.set_preferred_language("300012345678", "hi")
    first_name = manager.recognize_caller("9820012345")   # Ramesh Patil's mobile
    assert first_name == "Ramesh"
    assert manager.lang.language == "hi"
    assert manager.lang.pinned is False        # still adaptable, not locked
    assert manager.memory.language == "hi"


def test_recognize_caller_with_no_stored_preference_leaves_language_und(manager):
    first_name = manager.recognize_caller("9820012345")
    assert first_name == "Ramesh"
    assert manager.lang.language == "und"


def test_recognize_unknown_number_does_not_touch_language(manager):
    manager.lang.language = "en"
    result = manager.recognize_caller("0000000000")
    assert result is None
    assert manager.lang.language == "en"       # untouched


def test_persist_writes_resolved_language_for_verified_customer(manager):
    manager.recognize_caller("9820012345")
    manager.memory.language = "hi"
    manager.persist_language_preference()
    res = manager.tools.svc.verify_customer(account_no="300012345678")
    assert res["preferred_language"] == "hi"


def test_persist_does_not_write_when_language_never_resolved(manager):
    """The short-call case that motivated this fix: 'und' must never
    overwrite a good, previously-learned preference with nothing."""
    manager.tools.svc.set_preferred_language("300012345678", "hi")
    manager.recognize_caller("9820012345")
    manager.memory.language = "und"    # this call's STT never resolved anything
    manager.persist_language_preference()
    res = manager.tools.svc.verify_customer(account_no="300012345678")
    assert res["preferred_language"] == "hi"   # unchanged, not wiped


def test_persist_does_not_write_for_unverified_caller(manager):
    manager.memory.language = "hi"
    manager.memory.verified = False
    manager.persist_language_preference()
    # No account_no on file for an unverified caller — nothing to persist to;
    # just confirm this never raises.
    assert manager.memory.account_no in (None, "")


def test_persist_never_raises_on_storage_failure(manager):
    manager.recognize_caller("9820012345")
    manager.memory.language = "hi"
    manager.tools.svc.set_preferred_language = MagicMock(side_effect=RuntimeError("db down"))
    manager.persist_language_preference()   # must not raise


# ── the opening greeting itself follows the seeded preference ───────────────
#
# Production evidence (session 929a148d33af): a recognized, verified caller
# with an established Hindi preference (from earlier calls) was greeted in
# Marathi anyway — "it used marathi in starting without any reason". Root
# cause: recognize_caller() seeded self.lang.language correctly, but
# greeting() never looked at it — persona.greeting was a single hardcoded
# Marathi string with nothing else to choose from. Fixed by making
# persona.greeting/greeting_personal per-language tables and having
# greeting() pick the seeded language, falling back to Marathi exactly as
# before for anyone recognize_caller() didn't seed.

def test_greeting_uses_the_seeded_preference_for_a_recognized_caller(manager):
    manager.tools.svc.set_preferred_language("300012345678", "hi")
    first_name = manager.recognize_caller("9820012345")   # Ramesh Patil's mobile
    chunk = manager.greeting(first_name)
    assert chunk.language == "hi"
    assert "कर सकता हूँ" in chunk.text or "कर सकती हूँ" in chunk.text


def test_greeting_still_defaults_to_marathi_with_no_stored_preference(manager):
    first_name = manager.recognize_caller("9820012345")
    chunk = manager.greeting(first_name)
    assert chunk.language == "mr"


def test_greeting_defaults_to_marathi_for_a_totally_unrecognized_caller(manager):
    chunk = manager.greeting()
    assert chunk.language == "mr"
