"""Offline unit tests — no API keys, no network. Covers the deterministic layers:
language engine, safety gate, memory slots, verify-gate, endpointing, BM25 retrieval."""
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
from backend.app.tools.msedcl import MsedclServices
from backend.app.tools.registry import ToolRegistry


# ── language engine ──────────────────────────────────────────────────────────
def test_language_command_switch():
    eng = LanguageEngine()
    eng.update("लाईट गेली आहे माझ्या घरची", "mr-IN")
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
    eng.update("माझं बिल खूप जास्त आलंय, मीटर पण फास्ट चालतंय आहे", "unknown")
    assert eng.language == "mr"
    eng2 = LanguageEngine()
    eng2.update("मेरा बिल बहुत ज्यादा आया है क्या करें", "unknown")
    assert eng2.language == "hi"


# ── safety gate ──────────────────────────────────────────────────────────────
def test_safety_trips_on_hazard():
    assert safety.assess("रोड पर तार गिर गया है चिंगारी निकल रही है").emergency
    assert safety.assess("transformer me se dhua nikal raha hai aag lagi").emergency
    assert safety.assess("someone got an electric shock from the pole").emergency


def test_safety_ignores_routine_outage():
    assert not safety.assess("बिजली नहीं आ रही है कल रात से").emergency
    assert not safety.assess("लाईट गेली आहे").emergency
    assert not safety.assess("light nahi aa rahi hai").emergency


# ── memory ───────────────────────────────────────────────────────────────────
def test_memory_slot_extraction():
    m = CallMemory()
    m.scan_user_text("मेरा नंबर 1700 1234 5678 है और mobile 98200 12345")
    assert m.consumer_no == "170012345678"
    assert m.mobile == "9820012345"


# ── tools + gates ────────────────────────────────────────────────────────────
@pytest.fixture
def registry(tmp_path):
    svc = MsedclServices(tmp_path / "test.db")
    return ToolRegistry(get_settings(), svc)


def test_verify_gate_blocks_unverified_write(registry):
    m = CallMemory()
    result = asyncio.run(registry.dispatch(
        "register_complaint",
        {"consumer_no": "999", "category": "High Bill", "description": "x"}, m))
    assert result["error"] == "verification_required"
    assert not m.complaints


def test_verified_write_succeeds_and_uses_verified_number(registry):
    m = CallMemory()
    v = asyncio.run(registry.dispatch("verify_consumer", {"consumer_no": "170012345678"}, m))
    assert v["verified"] and m.verified
    r = asyncio.run(registry.dispatch(
        "register_complaint",
        {"consumer_no": "WRONG", "category": "Supply Failed - Phase out",
         "description": "Power off at premises"}, m))
    assert r.get("sr_no") and m.complaints[0].sr_no == r["sr_no"]
    assert r["category"] == "Supply Failed - Phase out"


def test_otp_gate_blocks_name_change(registry):
    m = CallMemory()
    asyncio.run(registry.dispatch("verify_consumer", {"consumer_no": "170012345678"}, m))
    r = asyncio.run(registry.dispatch(
        "request_name_change", {"consumer_no": "170012345678", "new_name": "X"}, m))
    assert r["error"] == "otp_required"


def test_invalid_category_rejected(registry):
    m = CallMemory()
    asyncio.run(registry.dispatch("verify_consumer", {"consumer_no": "170012345678"}, m))
    r = asyncio.run(registry.dispatch(
        "register_complaint",
        {"consumer_no": "170012345678", "category": "Made Up Category", "description": "x"}, m))
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
    result = asyncio.run(r.search("discount for paying bill online digital payment"))
    assert "0.25" in result["context"]
    result2 = asyncio.run(r.search("तार गिरा है क्या करें safety wire fallen"))
    assert "wooden" in result2["context"].lower() or "away" in result2["context"].lower()
