"""Notification Service — offline tests. No WhatsApp, no network: the sender is
a fake. Verifies the POC success criteria that can be proven deterministically:

  • notify_event is sync + instant (hot path never blocks on delivery)
  • only actionable tool successes notify; reads/FAQs/errors never do
  • structured message contains every required field
  • duplicate inside the window → follow-up (update + count), not a new ticket
  • retry → RETRYING → SENT; exhaustion → FAILED, no exception escapes
  • delivery status + audit rows persisted and searchable (dashboard feed)
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.config import get_settings
from backend.app.notification_service.notification_logger import NotificationStore
from backend.app.notification_service.notification_manager import NotificationService
from backend.app.notification_service.retry_handler import RetryExhausted, retry_async
from backend.app.notification_service.ticket_formatter import (
    Ticket, build_summary, derive_priority, format_follow_up, format_message,
    new_ticket_id)
from backend.app.notification_service.whatsapp_sender import WhatsAppSender


class FakeSender(WhatsAppSender):
    name = "fake"

    def __init__(self, fail_times: int = 0, delay_s: float = 0.0):
        self.fail_times = fail_times
        self.delay_s = delay_s
        self.sent: list[tuple[str, str]] = []

    async def send(self, group: str, message: str) -> str:
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("simulated bridge outage")
        self.sent.append((group, message))
        return f"fake-{len(self.sent)}"


def _settings(tmp_path, **over):
    s = get_settings().model_copy()
    s.db_path = tmp_path / "notify.db"
    s.whatsapp_enabled = True
    s.whatsapp_group_name = "Operations"
    s.whatsapp_retry_max = 3
    s.whatsapp_retry_base_s = 0.01          # fast tests
    s.whatsapp_dedup_window_min = 60
    s.whatsapp_llm_summary = False
    for k, v in over.items():
        setattr(s, k, v)
    return s


def _service(tmp_path, sender=None, **over):
    s = _settings(tmp_path, **over)
    store = NotificationStore(s.db_path, tmp_path / "logs")
    return NotificationService(s, sender or FakeSender(), store)


_MEMORY = {"name": "Rahul Sharma", "mobile": "9876543210",
           "account_no": "300012345678", "service_type": "fiber",
           "verified": True}


def _complaint_event(svc, call_id="call1", desc="Broadband down four hours"):
    svc.notify_event(
        "register_complaint",
        {"account_no": "300012345678", "category": "Broadband - No Internet",
         "description": desc},
        {"ticket_no": "TC2607ABC123", "category": "Broadband - No Internet",
         "sla_hours": 8, "note": "Ticket sent by SMS."},
        dict(_MEMORY), call_id)


# ── formatter ────────────────────────────────────────────────────────────────
def test_message_contains_all_required_fields():
    t = Ticket(ticket_id="TT-2026-1042", event_type="complaint_registered",
               category="Broadband - No Internet", priority="HIGH",
               summary="Customer reports broadband down four hours. Account "
                       "verified. Engineer visit recommended.",
               customer_name="Rahul Sharma", mobile="9876543210",
               account_no="300012345678", service_type="fiber",
               location="Pune", complaint_no="TC2607ABC123")
    msg = format_message(t)
    for token in ("NEW CUSTOMER TICKET", "TT-2026-1042", "Rahul Sharma",
                  "9876543210", "300012345678", "Broadband / Fiber",
                  "Broadband - No Internet", "Pune", "HIGH", "AI Summary",
                  "Generated automatically by AI Voice Agent"):
        assert token in msg, token
    assert "transcript" not in msg.lower()      # never raw transcripts


def test_follow_up_message_mentions_repeat_contact():
    t = Ticket(ticket_id="TT-2026-0001", event_type="complaint_registered",
               category="Broadband - No Internet", priority="HIGH",
               summary="s", customer_name="X", follow_up_count=2)
    msg = format_follow_up(t)
    assert "Customer has contacted us again regarding this issue." in msg
    assert "FOLLOW-UP #2" in msg and "TT-2026-0001" in msg


def test_priority_derivation():
    assert derive_priority("complaint_registered", "Broadband - No Internet") == "HIGH"
    assert derive_priority("priority_incident", "anything") == "HIGH"
    assert derive_priority("complaint_registered", "Mobile - Call Drops") == "MEDIUM"
    assert derive_priority("complaint_registered",
                           "Wi-Fi Configuration Help") == "LOW"


def test_summary_capped_at_150_words_and_factual():
    summary = build_summary(
        "complaint_registered",
        {"description": "net down " * 200}, {"note": "x"},
        {"verified": True}, ["Area outage checked", "Remote diagnostics run"])
    assert len(summary.split()) <= 150
    assert "Account verified." in summary


def test_ticket_id_format():
    tid = new_ticket_id()
    assert tid.startswith("TT-2026-") and len(tid) == 12


# ── event filtering ──────────────────────────────────────────────────────────
def test_reads_and_faqs_never_notify(tmp_path):
    async def run():
        svc = _service(tmp_path)
        for tool, result in (("get_bill", {"amount_rs": 942}),
                             ("search_knowledge", {"context": "..."}),
                             ("verify_customer", {"verified": True}),
                             ("get_usage", {"data_used_today_gb": 1.0})):
            svc.notify_event(tool, {}, result, dict(_MEMORY), "c1")
        assert svc._q.qsize() == 0              # nothing enqueued
    asyncio.run(run())


def test_failed_tool_never_notifies(tmp_path):
    async def run():
        svc = _service(tmp_path)
        svc.notify_event("register_complaint", {"category": "X"},
                         {"error": "verification_required"}, dict(_MEMORY), "c1")
        assert svc._q.qsize() == 0
    asyncio.run(run())


# ── async pipeline ───────────────────────────────────────────────────────────
def test_notify_event_is_instant_even_with_slow_sender(tmp_path):
    """Success criterion: the customer-facing path must never wait. A sender
    that takes 500 ms must not make notify_event take more than ~1 ms."""
    async def run():
        svc = _service(tmp_path, sender=FakeSender(delay_s=0.5))
        svc.start()
        t0 = time.perf_counter()
        _complaint_event(svc)
        assert (time.perf_counter() - t0) < 0.01   # sync enqueue only
        await svc._q.join()                        # worker finishes in background
        assert svc.sender.sent
    asyncio.run(run())


def test_end_to_end_ticket_sent_and_status_tracked(tmp_path):
    async def run():
        svc = _service(tmp_path)
        svc.start()
        _complaint_event(svc)
        await svc._q.join()
        group, msg = svc.sender.sent[0]
        assert group == "Operations"
        assert "Rahul Sharma" in msg and "TC2607ABC123" in msg
        rows = svc.store.search()
        assert len(rows) == 1
        assert rows[0]["status"] == "SENT" and rows[0]["delivered_at"]
        assert rows[0]["call_id"] == "call1"
    asyncio.run(run())


def test_troubleshooting_trace_lands_in_summary(tmp_path):
    async def run():
        svc = _service(tmp_path)
        svc.start()
        svc.notify_event("run_line_diagnostics", {"account_no": "1"},
                         {"result": "PASS"}, dict(_MEMORY), "call1")
        svc.notify_event("restart_ont", {"account_no": "1"},
                         {"restarted": True}, dict(_MEMORY), "call1")
        _complaint_event(svc)
        await svc._q.join()
        _, msg = svc.sender.sent[0]
        assert "Remote diagnostics run" in msg
        assert "Remote ONT restart attempted" in msg
    asyncio.run(run())


# ── duplicate detection ──────────────────────────────────────────────────────
def test_duplicate_within_window_sends_follow_up_not_new_ticket(tmp_path):
    async def run():
        svc = _service(tmp_path)
        svc.start()
        _complaint_event(svc, call_id="call1")
        await svc._q.join()
        _complaint_event(svc, call_id="call2")     # same customer+category
        await svc._q.join()
        assert len(svc.sender.sent) == 2
        assert "Customer has contacted us again" in svc.sender.sent[1][1]
        rows = svc.store.search()
        assert len(rows) == 1                       # updated, NOT a second row
        assert rows[0]["follow_up_count"] == 1
    asyncio.run(run())


# ── retry + failure ──────────────────────────────────────────────────────────
def test_retry_then_success_marks_sent(tmp_path):
    async def run():
        svc = _service(tmp_path, sender=FakeSender(fail_times=2))
        svc.start()
        _complaint_event(svc)
        await svc._q.join()
        row = svc.store.search()[0]
        assert row["status"] == "SENT" and row["attempts"] == 3
    asyncio.run(run())


def test_all_retries_fail_marks_failed_without_raising(tmp_path):
    async def run():
        svc = _service(tmp_path, sender=FakeSender(fail_times=99))
        svc.start()
        _complaint_event(svc)
        await svc._q.join()                        # must not raise
        row = svc.store.search()[0]
        assert row["status"] == "FAILED" and "simulated" in row["last_error"]
    asyncio.run(run())


def test_retry_async_exhaustion():
    async def run():
        async def always_fail():
            raise RuntimeError("nope")
        with pytest.raises(RetryExhausted):
            await retry_async(always_fail, max_attempts=2, base_delay_s=0.01)
    asyncio.run(run())


# ── dashboard search ─────────────────────────────────────────────────────────
def test_search_matches_customer_and_status(tmp_path):
    async def run():
        svc = _service(tmp_path)
        svc.start()
        _complaint_event(svc)
        await svc._q.join()
        assert svc.store.search("Rahul")
        assert svc.store.search("SENT")
        assert svc.store.search("Broadband")
        assert not svc.store.search("zzz-no-match")
    asyncio.run(run())
