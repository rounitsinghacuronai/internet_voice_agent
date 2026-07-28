"""Regression tests for India-specific number handling requested directly by
the customer: mobile numbers are always 10 digits starting 6/7/8/9, a caller
often reads the +91 country code along with the number and it must be
stripped rather than mangling the capture, and new-connection requests must
also collect a verified 6-digit address PIN code (not just free-text
address).

Covers three layers:
  1. conversation/numbers.py — strip_mobile_country_code() and the NumberType
     catalogue entries (mobile prefix rule, new "pincode" type).
  2. conversation/numbers.py — NumberBuffer.feed() actually applying the
     strip during live multi-turn capture (both single-utterance and
     split-across-turns country code).
  3. tools/registry.py — the number-format hard gate refusing a bad mobile
     prefix / malformed pincode on register_new_connection, and tools/
     telecom.py persisting the pincode once accepted.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.conversation.numbers import (
    NumberBuffer, number_type, strip_mobile_country_code,
)
from backend.app.config import get_settings
from backend.app.conversation.memory import CallMemory
from backend.app.tools.registry import ToolRegistry
from backend.app.tools.telecom import TelecomServices


# ── NumberType catalogue ─────────────────────────────────────────────────────

def test_mobile_type_rejects_prefix_0_to_5():
    t = number_type("mobile")
    for bad_start in "012345":
        assert not t.valid(bad_start + "876543210"), bad_start
    for good_start in "6789":
        assert t.valid(good_start + "876543210"), good_start


def test_pincode_type_is_six_digits_never_leading_zero():
    t = number_type("pincode")
    assert t.valid("411001")
    assert not t.valid("011001")   # no Indian PIN code starts with 0
    assert not t.valid("41100")    # 5 digits
    assert not t.valid("4110011")  # 7 digits


# ── strip_mobile_country_code() ──────────────────────────────────────────────

def test_strips_91_prefix_from_a_full_12_digit_mobile():
    assert strip_mobile_country_code("mobile", "919876543210") == "9876543210"


def test_strips_0091_and_091_isd_trunk_variants():
    assert strip_mobile_country_code("mobile", "00919876543210") == "9876543210"
    assert strip_mobile_country_code("mobile", "0919876543210") == "9876543210"


def test_does_not_strip_from_a_non_mobile_field():
    """An account number may legitimately start with 91 — must never be
    shortened just because it shares the same leading digits."""
    digits = "911234567890"  # 12-digit account number starting 91
    assert strip_mobile_country_code("account_no", digits) == digits


def test_does_not_strip_mid_capture_before_length_is_known():
    """Only 5 digits in so far ('91' + '987') — stripping now would be a
    guess; wait until the full number has arrived."""
    assert strip_mobile_country_code("mobile", "91987") == "91987"


def test_does_not_strip_when_remainder_has_bad_prefix():
    """'91' + a 10-digit remainder starting with 5 isn't a real country-code
    case — a genuine 12-digit garbled capture, left alone so the normal
    prefix-rejection path catches it instead of silently 'fixing' it wrong."""
    digits = "915123456789"
    assert strip_mobile_country_code("mobile", digits) == digits


def test_bare_10_digit_mobile_untouched():
    assert strip_mobile_country_code("mobile", "9876543210") == "9876543210"


# ── NumberBuffer.feed() end-to-end ───────────────────────────────────────────

def test_feed_single_utterance_with_country_code():
    """Caller reads '+91 98765 43210' in one breath — STT drops the '+',
    leaving a 12-digit run. Must land on the real 10-digit number, not be
    truncated from the front (which would keep '91' and drop the true last
    two digits)."""
    nb = NumberBuffer()
    nb.start("mobile")
    digits, complete = nb.feed("nine one nine eight seven six five four three two one zero")
    assert digits == "9876543210"
    assert complete


def test_feed_country_code_arrives_in_its_own_earlier_turn():
    """'+91' spoken first, pause, then the ten digits separately."""
    nb = NumberBuffer()
    nb.start("mobile")
    nb.feed("nine one")                                    # just "91"
    digits, complete = nb.feed("nine eight seven six five four three two one zero")
    assert digits == "9876543210"
    assert complete


def test_feed_plain_mobile_without_country_code_still_works():
    nb = NumberBuffer()
    nb.start("mobile")
    digits, complete = nb.feed("nine eight seven six five four three two one zero")
    assert digits == "9876543210"
    assert complete


def test_feed_pincode_captures_six_digits():
    nb = NumberBuffer()
    nb.start("pincode")
    digits, complete = nb.feed("four one one zero zero one")
    assert digits == "411001"
    assert complete


# ── registry.py number-format gate on register_new_connection ───────────────

@pytest.fixture
def registry(tmp_path):
    svc = TelecomServices(tmp_path / "test.db")
    return ToolRegistry(get_settings(), svc)


def _base_args(**overrides) -> dict:
    args = {
        "name": "Rounit Deshmukh",
        "address": "Flat 4B, Kunal Icon, Baner",
        "pincode": "411045",
        "service_type": "fiber",
        "plan": "Fiber 300",
        "contact_mobile": "9876543210",
        "preferred_slot": "tomorrow evening",
    }
    args.update(overrides)
    return args


def test_register_rejects_mobile_with_bad_prefix(registry):
    m = CallMemory()
    r = asyncio.run(registry.dispatch(
        "register_new_connection", _base_args(contact_mobile="5876543210"), m))
    assert r["error"] == "invalid_number_format"


def test_register_rejects_short_pincode(registry):
    m = CallMemory()
    r = asyncio.run(registry.dispatch(
        "register_new_connection", _base_args(pincode="4110"), m))
    assert r["error"] == "invalid_number_format"


def test_register_rejects_pincode_starting_with_zero(registry):
    m = CallMemory()
    r = asyncio.run(registry.dispatch(
        "register_new_connection", _base_args(pincode="011045"), m))
    assert r["error"] == "invalid_number_format"


def test_register_accepts_mobile_with_country_code_and_strips_it(registry):
    """Even if the model passes the raw digits straight through (not via the
    buffered capture path) with a +91 still attached, the gate itself must
    tolerate it — AND normalize it to bare digits before it's stored, so a
    later lookup by this number (a plain 10-digit string in the DB) isn't
    broken by a lingering country code."""
    m = CallMemory()
    r = asyncio.run(registry.dispatch(
        "register_new_connection", _base_args(contact_mobile="+91 98765 43210"), m))
    assert r.get("registered") is True
    assert r["contact_mobile"] == "9876543210"


def test_register_new_connection_persists_pincode(registry, tmp_path):
    m = CallMemory()
    r = asyncio.run(registry.dispatch("register_new_connection", _base_args(), m))
    assert r["registered"] is True
    assert r["pincode"] == "411045"
    import sqlite3
    conn = sqlite3.connect(tmp_path / "test.db")
    row = conn.execute("SELECT pincode FROM new_connections WHERE application_no=?",
                       (r["application_no"],)).fetchone()
    assert row[0] == "411045"
