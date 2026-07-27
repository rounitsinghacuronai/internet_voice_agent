"""Regression tests for the task-aware filler engine (speech/lexicon.py::
filler_bucket_name / task_filler) — the fix for "awkward silence" fillers being
a generic "hmm, let me check" regardless of what tool is actually running.
Pure function tests, no network/LLM — mirrors test_repeat_prevention.py's
offline style, plus one end-to-end wiring check against ConversationManager.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.speech.lexicon import (
    HESITATIONS, TASK_FILLERS, filler_bucket_name, task_filler, lang_table,
)


def test_empty_tool_list_falls_back_to_generic_bucket():
    assert filler_bucket_name([]) == "generic"
    assert task_filler([], "en") == lang_table(HESITATIONS, "en")


def test_unknown_tool_falls_back_to_generic_bucket():
    assert filler_bucket_name(["some_untracked_tool"]) == "generic"
    assert task_filler(["some_untracked_tool"], "en") == lang_table(HESITATIONS, "en")


def test_verification_tools_map_to_verification_bucket():
    assert filler_bucket_name(["verify_customer"]) == "verification"
    assert filler_bucket_name(["send_otp"]) == "verification"
    assert filler_bucket_name(["verify_otp"]) == "verification"


def test_lookup_tools_map_to_lookup_bucket():
    for tool in ("get_plan", "get_bill", "get_payment_status", "get_recharge_history",
                 "get_usage", "get_network_status", "get_broadband_status",
                 "run_line_diagnostics", "restart_ont", "get_plan_catalog",
                 "track_complaint", "get_new_connection_status"):
        assert filler_bucket_name([tool]) == "lookup", tool


def test_ticket_tools_map_to_ticket_bucket():
    assert filler_bucket_name(["register_complaint"]) == "ticket"
    assert filler_bucket_name(["escalate_complaint"]) == "ticket"
    assert filler_bucket_name(["log_priority_incident"]) == "ticket"


def test_knowledge_escalation_engineer_connection_buckets():
    assert filler_bucket_name(["search_knowledge"]) == "knowledge"
    assert filler_bucket_name(["transfer_to_senior_executive"]) == "escalation"
    assert filler_bucket_name(["schedule_engineer_visit"]) == "engineer"
    assert filler_bucket_name(["register_new_connection"]) == "connection"
    assert filler_bucket_name(["request_sim_swap"]) == "connection"
    assert filler_bucket_name(["request_plan_change"]) == "connection"
    assert filler_bucket_name(["block_sim"]) == "connection"


def test_first_mapped_tool_wins_when_multiple_tools_in_one_round():
    # get_network_status -> lookup, restart_ont -> lookup: same bucket, unambiguous.
    assert filler_bucket_name(["get_network_status", "restart_ont"]) == "lookup"
    # Unmapped tool first, mapped tool second: the mapped one should still be found.
    assert filler_bucket_name(["some_untracked_tool", "get_bill"]) == "lookup"


def test_every_bucket_has_all_three_languages_non_empty():
    for bucket, table in TASK_FILLERS.items():
        for lang in ("en", "hi", "mr"):
            fillers = lang_table(table, lang)
            assert fillers, f"bucket {bucket!r} lang {lang!r} has no fillers"


def test_task_filler_returns_bucket_specific_phrasing_not_generic_hesitation():
    billing_fillers = task_filler(["get_bill"], "en")
    ticket_fillers = task_filler(["register_complaint"], "en")
    assert billing_fillers == lang_table(TASK_FILLERS["lookup"], "en")
    assert ticket_fillers == lang_table(TASK_FILLERS["ticket"], "en")
    assert billing_fillers != ticket_fillers


def test_manager_wiring_uses_bucketed_variation_key():
    """End-to-end (no network): confirms the manager module imports the new
    task-aware helpers instead of the old generic HESITATIONS-only path."""
    import inspect
    from backend.app.conversation import manager as manager_mod

    src = inspect.getsource(manager_mod._llm_turn) if hasattr(manager_mod, "_llm_turn") \
        else inspect.getsource(manager_mod.ConversationManager._llm_turn)
    assert "task_filler" in src
    assert "filler_bucket_name" in src
