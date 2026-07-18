"""Notification Service — async, decoupled ops alerts (WhatsApp for the POC).

Public surface:
    from backend.app.notification_service import build_notification_service
    service = build_notification_service(settings, llm=deps.llm)
    service.start()
    service.notify_event(tool, args, result, memory_snapshot, call_id)  # sync
    service.send_ticket(ticket)                                          # sync

The voice pipeline never awaits this package.
"""
from __future__ import annotations

from pathlib import Path

from .notification_manager import NotificationService
from .notification_logger import NotificationStore
from .ticket_formatter import Ticket, format_message, new_ticket_id
from .whatsapp_sender import make_sender

__all__ = ["NotificationService", "Ticket", "format_message", "new_ticket_id",
           "build_notification_service"]


def build_notification_service(settings, llm=None) -> NotificationService:
    store = NotificationStore(
        db_path=settings.db_path,
        audit_dir=Path(settings.db_path).parent / "logs",
        s3_bucket=getattr(settings, "notify_s3_bucket", ""),
    )
    return NotificationService(settings, make_sender(settings), store, llm=llm)
