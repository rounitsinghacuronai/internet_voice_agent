"""NotificationService — the one public face of this package.

    service.notify_event(event)   # sync, returns instantly (hot path safe)
    service.send_ticket(ticket)   # sync enqueue of a pre-built Ticket

Everything else — ticket building, AI summary, duplicate detection, retries,
delivery status, audit — happens on a single background worker task. The
customer-facing turn NEVER awaits anything in this package: enqueue is a
put_nowait and the queue is bounded (overflow drops the notification with an
audit record, never blocks the call).

Decoupling: the ConversationManager knows nothing about this service. The
ToolRegistry fires a plain-dict event through an injected callback (main.py
wiring); this module decides whether it deserves the ops group's attention.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from .notification_logger import FAILED, PENDING, RETRYING, SENT, NotificationStore
from .retry_handler import RetryExhausted, retry_async
from .ticket_formatter import (Ticket, build_summary, derive_priority,
                               format_follow_up, format_message, new_ticket_id)
from .whatsapp_sender import WhatsAppSender

log = logging.getLogger(__name__)

# tool-success → event type. Anything not listed never notifies (greetings,
# FAQs, reads, resolved-in-call simple queries produce no write tools at all).
_TOOL_EVENTS = {
    "register_complaint": "complaint_registered",
    "escalate_complaint": "ticket_escalated",
    "schedule_engineer_visit": "engineer_visit",
    "block_sim": "sim_blocked",
    "request_sim_swap": "esim_request",
    "log_priority_incident": "priority_incident",
    "transfer_to_human": "human_escalation",
    "register_new_connection": "new_connection_request",
}

# result keys that prove the tool actually succeeded
_SUCCESS_KEYS = {
    "complaint_registered": "ticket_no",
    "ticket_escalated": "ticket_no",
    "engineer_visit": "scheduled",
    "sim_blocked": "blocked",
    "esim_request": "submitted",
    "priority_incident": "logged",
    "human_escalation": "transferred",
    "new_connection_request": "registered",
}

# tool traces that read as troubleshooting steps in the AI summary
_TROUBLESHOOT_LABEL = {
    "get_network_status": "Area outage checked",
    "get_broadband_status": "ONT/line status checked",
    "run_line_diagnostics": "Remote diagnostics run",
    "restart_ont": "Remote ONT restart attempted",
    "get_bill": "Billing status checked",
    "get_payment_status": "Payment status checked",
    "get_usage": "Usage checked",
    "get_recharge_history": "Recharge history checked",
}


class NotificationService:
    def __init__(self, settings, sender: WhatsAppSender, store: NotificationStore,
                 llm=None):
        self.s = settings
        self.sender = sender
        self.store = store
        self.llm = llm                       # optional — background AI summary
        self._q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._worker: asyncio.Task | None = None
        # per-call troubleshooting trace: call_id → [labels]
        self._trace: dict[str, list[str]] = {}

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name="notify_worker")
            log.info("NotificationService worker up (provider=%s, group=%r)",
                     self.sender.name, self.s.whatsapp_group_name)

    async def close(self) -> None:
        if self._worker and not self._worker.done():
            await self._q.join()
            self._worker.cancel()
        await self.sender.close()

    # ── hot path (called from ToolRegistry callback) — must never block ─────
    def notify_event(self, tool: str, args: dict, result: dict,
                     memory_snapshot: dict, call_id: str = "") -> None:
        """Sync + instant. Decides relevance, records troubleshooting trace,
        enqueues. All heavy work happens on the worker."""
        if isinstance(result, dict) and not result.get("error"):
            if tool in _TROUBLESHOOT_LABEL and call_id:
                self._trace.setdefault(call_id, []).append(_TROUBLESHOOT_LABEL[tool])
                del self._trace[call_id][:-6]        # keep last 6 steps max
        event_type = _TOOL_EVENTS.get(tool)
        if event_type is None or not isinstance(result, dict) or result.get("error"):
            return
        if not result.get(_SUCCESS_KEYS[event_type]):
            return
        try:
            self._q.put_nowait(("event", event_type, tool, args, result,
                                memory_snapshot, call_id))
        except asyncio.QueueFull:            # never block the call — drop + audit
            log.error("notification queue full — dropping %s for call %s",
                      event_type, call_id)
            self.store.audit("-", call_id, "dropped_queue_full",
                             event_type=event_type)

    def send_ticket(self, ticket: Ticket) -> None:
        """Public simple interface: enqueue a pre-built ticket."""
        try:
            self._q.put_nowait(("ticket", ticket))
        except asyncio.QueueFull:
            log.error("notification queue full — dropping ticket %s", ticket.ticket_id)
            self.store.audit(ticket.ticket_id, ticket.call_id, "dropped_queue_full")

    # ── worker ───────────────────────────────────────────────────────────────
    async def _run(self) -> None:
        while True:
            item = await self._q.get()
            try:
                if item[0] == "ticket":
                    await self._deliver(item[1], format_message(item[1]))
                else:
                    await self._process_event(*item[1:])
            except asyncio.CancelledError:
                raise
            except Exception:                # worker must survive anything
                log.exception("notification worker error")
            finally:
                self._q.task_done()

    async def _process_event(self, event_type: str, tool: str, args: dict,
                             result: dict, memory: dict, call_id: str) -> None:
        category = (args.get("category") or result.get("category")
                    or {"sim_blocked": "SIM - Lost / Block Request",
                        "esim_request": "SIM - Replacement / eSIM",
                        "priority_incident": f"Security - {args.get('type', 'incident')}",
                        "human_escalation": f"Escalation - {args.get('reason', 'requested')}",
                        "engineer_visit": "Engineer Visit Required",
                        "new_connection_request": "New Connection - Request",
                        }.get(event_type, event_type))

        # New-connection callers are prospects — not in the subscriber DB — so the
        # customer fields come from what they told us on THIS call (the tool args),
        # not from verified call memory.
        is_new_conn = event_type == "new_connection_request"

        # ── duplicate detection: update, don't spam ──────────────────────────
        # For a new connection the "contact" is the number to reach them on.
        mobile = (memory.get("mobile")
                  or args.get("contact_mobile") or args.get("mobile") or "")
        account = memory.get("account_no") or ""
        caller_number = memory.get("caller_number") or args.get("caller_number") or ""
        prev = self.store.find_recent(mobile, account, category,
                                      self.s.whatsapp_dedup_window_min)
        if prev is not None:
            count = self.store.bump_follow_up(prev["ticket_id"])
            t = Ticket(ticket_id=prev["ticket_id"], event_type=event_type,
                       category=category, priority=prev["priority"],
                       summary=prev["summary"],
                       customer_name=prev["customer_name"], mobile=mobile,
                       caller_number=caller_number,
                       account_no=account, service_type=prev["service_type"],
                       location=prev["location"], call_id=call_id,
                       complaint_no=prev["complaint_no"],
                       follow_up_count=count)
            self.store.audit(t.ticket_id, call_id, "follow_up_detected",
                             count=count, category=category)
            await self._deliver(t, format_follow_up(t), follow_up=True)
            return

        # ── new ticket ───────────────────────────────────────────────────────
        summary = build_summary(event_type, args, result, memory,
                                self._trace.get(call_id, []))
        t = Ticket(
            ticket_id=new_ticket_id(),
            event_type=event_type,
            category=category,
            priority=derive_priority(event_type, category),
            summary=summary,
            customer_name=(args.get("name") if is_new_conn else memory.get("name")) or "",
            mobile=mobile,
            caller_number=caller_number,
            account_no=account,
            service_type=(args.get("service_type") if is_new_conn
                          else memory.get("service_type")) or "",
            location=(args.get("address") if is_new_conn
                      else memory.get("location")) or "",
            verified=bool(memory.get("verified")),
            call_id=call_id,
            complaint_no=(result.get("ticket_no") or result.get("visit_id")
                          or result.get("reference") or result.get("incident_id")
                          or ""),
        )
        self.store.audit(t.ticket_id, call_id, "created",
                         event_type=event_type, category=category,
                         priority=t.priority)
        # optional LLM polish of the summary — background only, deterministic
        # fallback already in place, hard 5s cap so the worker never wedges
        if self.llm is not None and getattr(self.s, "whatsapp_llm_summary", False):
            try:
                polished = await asyncio.wait_for(
                    self._llm_summary(t, args, result, memory), timeout=5.0)
                if polished:
                    t.summary = polished
            except Exception as e:           # noqa: BLE001
                log.info("LLM summary skipped (%s) — deterministic kept", e)
        await self._deliver(t, format_message(t))

    async def _llm_summary(self, t: Ticket, args: dict, result: dict,
                           memory: dict) -> str:
        prompt = (
            "Write an operations-ticket summary in at most 150 words, plain "
            "English, short declarative sentences, one per line. Include: main "
            "issue, verification status, troubleshooting already done, current "
            "status, recommended action. No greetings, no transcript.\n\n"
            f"Facts: category={t.category}; verified={t.verified}; "
            f"description={args.get('description') or args.get('details') or ''}; "
            f"backend_note={result.get('note', '')}; "
            f"steps={'; '.join(self._trace.get(t.call_id, []))}")
        out = await self.llm.complete(
            [{"role": "user", "content": prompt}], temperature=0.2)
        text = (out or "").strip()
        return " ".join(text.split()[:150]) if text else ""

    # ── delivery with retries + status tracking ─────────────────────────────
    async def _deliver(self, t: Ticket, message: str,
                       follow_up: bool = False) -> None:
        self.store.upsert(t, PENDING)
        attempts = {"n": 0}

        def on_retry(attempt: int, err: Exception) -> None:
            attempts["n"] = attempt
            self.store.upsert(t, RETRYING, attempts=attempt, last_error=str(err))
            self.store.audit(t.ticket_id, t.call_id, "retrying",
                             attempt=attempt, error=str(err)[:200])

        async def _send():
            attempts["n"] += 0
            return await self.sender.send(self.s.whatsapp_group_name, message)

        t_start = datetime.now()
        try:
            msg_id = await retry_async(
                _send,
                max_attempts=self.s.whatsapp_retry_max,
                base_delay_s=self.s.whatsapp_retry_base_s,
                on_retry=on_retry,
            )
            latency_ms = (datetime.now() - t_start).total_seconds() * 1000
            self.store.upsert(t, SENT, attempts=attempts["n"] + 1,
                              delivered_at=datetime.now().isoformat())
            self.store.audit(t.ticket_id, t.call_id, "sent",
                             provider=self.sender.name, provider_id=msg_id,
                             latency_ms=round(latency_ms), follow_up=follow_up)
        except RetryExhausted as e:
            self.store.upsert(t, FAILED, attempts=e.attempts,
                              last_error=str(e.last)[:300])
            self.store.audit(t.ticket_id, t.call_id, "failed",
                             attempts=e.attempts, error=str(e.last)[:200])
            log.error("notification %s FAILED after %d attempts — customer "
                      "conversation unaffected", t.ticket_id, e.attempts)
