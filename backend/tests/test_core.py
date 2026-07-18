"""Offline unit tests — no API keys, no network. Covers the deterministic layers:
language engine, security gate, memory slots, verify-gate, endpointing, BM25 retrieval."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.config import get_settings
from backend.app.conversation import safety
from backend.app.conversation.language import LanguageEngine
from backend.app.conversation.memory import CallMemory
from backend.app.tools.telecom import TelecomServices
from backend.app.tools.registry import ToolRegistry


# ── language engine ──────────────────────────────────────────────────────────
def test_language_command_switch():
    eng = LanguageEngine()
    eng.update("नेट चालत नाही आहे माझ्या घरचं", "mr-IN")
    assert eng.language == "mr"
    eng.update("can you talk in English please", "en-IN")
    assert eng.language == "en" and eng.pinned


def test_language_hysteresis_one_stray_word():
    eng = LanguageEngine()
    eng.update("मेरा बिल बहुत ज्यादा आया है", "hi-IN")
    assert eng.language == "hi"
    eng.update("ok", "en-IN")           # one stray token must NOT flip
    assert eng.language == "hi"


def test_language_devanagari_disambiguation():
    eng = LanguageEngine()
    eng.update("माझं बिल खूप जास्त आलंय, नेट पण स्लो चालतंय आहे", "unknown")
    assert eng.language == "mr"
    eng2 = LanguageEngine()
    eng2.update("मेरा बिल बहुत ज्यादा आया है क्या करें", "unknown")
    assert eng2.language == "hi"


# ── security gate ────────────────────────────────────────────────────────────
def test_security_trips_on_incident():
    assert safety.assess("kisi ne mera OTP maang ke paise nikal liye fraud ho gaya").emergency
    assert safety.assess("मेरा फोन चोरी हो गया है सिम ब्लॉक करो").emergency
    assert safety.assess("someone is giving me dhamki on calls, blackmail kar raha hai").emergency


def test_security_ignores_routine_issue():
    assert not safety.assess("नेट नहीं चल रहा है कल रात से").emergency
    assert not safety.assess("रिचार्ज झालं पण पॅक लागला नाही").emergency
    assert not safety.assess("internet nahi chal raha hai").emergency


# ── memory ───────────────────────────────────────────────────────────────────
def test_memory_slot_extraction():
    m = CallMemory()
    m.scan_user_text("मेरा नंबर 3000 1234 5678 है और mobile 98200 12345")
    assert m.account_no == "300012345678"
    assert m.mobile == "9820012345"


# ── tools + gates ────────────────────────────────────────────────────────────
@pytest.fixture
def registry(tmp_path):
    svc = TelecomServices(tmp_path / "test.db")
    return ToolRegistry(get_settings(), svc)


def test_verify_gate_blocks_unverified_write(registry):
    m = CallMemory()
    result = asyncio.run(registry.dispatch(
        "register_complaint",
        {"account_no": "999", "category": "Billing - High Bill", "description": "x"}, m))
    assert result["error"] == "verification_required"
    assert not m.complaints


def test_verified_write_succeeds_and_uses_verified_number(registry):
    m = CallMemory()
    v = asyncio.run(registry.dispatch("verify_customer", {"account_no": "300012345678"}, m))
    assert v["verified"] and m.verified
    r = asyncio.run(registry.dispatch(
        "register_complaint",
        {"account_no": "WRONG", "category": "Broadband - No Internet",
         "description": "Fiber connection down at premises"}, m))
    assert r.get("ticket_no") and m.complaints[0].ticket_no == r["ticket_no"]
    assert r["category"] == "Broadband - No Internet"


def test_otp_gate_blocks_plan_change(registry):
    m = CallMemory()
    asyncio.run(registry.dispatch("verify_customer", {"account_no": "300012345678"}, m))
    r = asyncio.run(registry.dispatch(
        "request_plan_change", {"account_no": "300012345678", "new_plan": "Fiber 300"}, m))
    assert r["error"] == "otp_required"


def test_sim_block_needs_verify_but_never_otp(registry):
    m = CallMemory()
    r = asyncio.run(registry.dispatch("block_sim", {"mobile": "9820012345"}, m))
    assert r["error"] == "verification_required"
    asyncio.run(registry.dispatch("verify_customer", {"account_no": "300012345678"}, m))
    r2 = asyncio.run(registry.dispatch("block_sim", {"mobile": "9820012345"}, m))
    assert r2.get("blocked") is True          # no OTP gate on a lost SIM


def test_invalid_category_rejected(registry):
    m = CallMemory()
    asyncio.run(registry.dispatch("verify_customer", {"account_no": "300012345678"}, m))
    r = asyncio.run(registry.dispatch(
        "register_complaint",
        {"account_no": "300012345678", "category": "Made Up Category", "description": "x"}, m))
    assert r["error"] == "invalid_category"


# ── endpointing ──────────────────────────────────────────────────────────────
def test_endpointer_emits_utterance():
    from backend.app.audio.endpointing import Endpointer, EventType
    from backend.app.audio.vad import SileroVAD

    s = get_settings()
    vad = SileroVAD(s.vad_threshold)
    vad._session = None  # force energy-gate for determinism
    ep = Endpointer(s, vad)
    rng = np.random.default_rng(0)
    speech = (rng.uniform(-0.4, 0.4, 16000) * 32767).astype(np.int16).tobytes()   # 1 s loud
    silence = np.zeros(16000, dtype=np.int16).tobytes()                            # 1 s quiet
    events = ep.feed(speech) + ep.feed(silence)
    types = [e.type for e in events]
    assert EventType.SPEECH_START in types and EventType.UTTERANCE in types


# ── retrieval (BM25 path, offline) ───────────────────────────────────────────
def test_bm25_retrieval_finds_billing_rule():
    from backend.app.providers.embeddings import HashingEmbedder
    from backend.app.rag.retriever import HybridRetriever

    s = get_settings()
    r = HybridRetriever(s, HashingEmbedder(64))
    asyncio.run(r.build())
    assert r.chunks, "knowledge/articles must not be empty"
    result = asyncio.run(r.search("recharge failed money debited refund how many days"))
    assert "5 to 7" in result["context"]
    result2 = asyncio.run(r.search("red LOS light on router fiber not working"))
    assert "los" in result2["context"].lower()
