"""WhatsApp senders — the ONLY module that knows how a message physically
leaves the building. Swapping personal-POC → WhatsApp Business API means
adding a class here and flipping WHATSAPP_PROVIDER; nothing upstream changes.

Contract: `async send(group, message) -> str` returns a provider message id,
raises on failure (the manager's retry handler owns retries — senders stay
dumb: no business logic, no formatting, no dedupe, ever).
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod

import httpx

log = logging.getLogger(__name__)


class WhatsAppSender(ABC):
    name = "abstract"

    @abstractmethod
    async def send(self, group: str, message: str) -> str: ...

    async def healthy(self) -> bool:      # optional; default optimistic
        return True

    async def close(self) -> None: ...


class BridgeSender(WhatsAppSender):
    """POC path: talks to the local whatsapp-web.js sidecar (whatsapp_bridge/),
    which holds a logged-in WhatsApp Web session for the personal account.
    The bridge owns session persistence, QR login and auto-reconnection —
    this class is just HTTP."""
    name = "personal"

    def __init__(self, base_url: str, timeout_s: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout_s, trust_env=False)

    async def healthy(self) -> bool:
        try:
            r = await self._client.get(f"{self.base_url}/health")
            return bool(r.status_code == 200 and r.json().get("ready"))
        except Exception:                                   # noqa: BLE001
            return False

    async def send(self, group: str, message: str) -> str:
        r = await self._client.post(f"{self.base_url}/send",
                                    json={"group": group, "message": message})
        # Read the bridge's error body BEFORE raising, so the delivery-status
        # row shows the real cause ("group not found", "not ready"), not a
        # bare HTTP 500.
        if r.status_code >= 400:
            try:
                detail = r.json().get("error", r.text[:200])
            except Exception:                               # noqa: BLE001
                detail = r.text[:200]
            raise RuntimeError(f"bridge {r.status_code}: {detail}")
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"bridge refused: {data.get('error', 'unknown')}")
        return str(data.get("id", ""))

    async def close(self) -> None:
        await self._client.aclose()


class MetaCloudSender(WhatsAppSender):
    """Official WhatsApp Business (Meta Cloud API). Production path — needs a
    registered WABA number + a pre-approved template or 24h session window.
    Group note: the Cloud API cannot post to consumer groups; ops teams use a
    broadcast list / individual numbers or a Community announcement channel."""
    name = "meta"

    def __init__(self, token: str, phone_number_id: str, to_numbers: list[str]):
        self.token = token
        self.phone_number_id = phone_number_id
        self.to_numbers = to_numbers
        self._client = httpx.AsyncClient(timeout=15.0)

    async def send(self, group: str, message: str) -> str:
        ids = []
        for to in self.to_numbers:
            r = await self._client.post(
                f"https://graph.facebook.com/v20.0/{self.phone_number_id}/messages",
                headers={"Authorization": f"Bearer {self.token}"},
                json={"messaging_product": "whatsapp", "to": to,
                      "type": "text", "text": {"body": message}})
            r.raise_for_status()
            ids.append(r.json()["messages"][0]["id"])
        return ",".join(ids)

    async def close(self) -> None:
        await self._client.aclose()


class ConsoleSender(WhatsAppSender):
    """Used when WHATSAPP_ENABLED=false or in tests: full pipeline runs
    (ticket, dedupe, status, audit) but the message only hits the log."""
    name = "console"

    async def send(self, group: str, message: str) -> str:
        log.info("WHATSAPP (console) → %s\n%s", group, message)
        return "console"


def make_sender(settings) -> WhatsAppSender:
    provider = (getattr(settings, "whatsapp_provider", "personal") or "personal").lower()
    if not getattr(settings, "whatsapp_enabled", False):
        return ConsoleSender()
    if provider == "personal":
        return BridgeSender(settings.whatsapp_bridge_url)
    if provider == "meta":
        return MetaCloudSender(
            settings.whatsapp_meta_token, settings.whatsapp_meta_phone_id,
            [n.strip() for n in settings.whatsapp_meta_to.split(",") if n.strip()])
    # twilio / exotel: same shape — add a class above when credentials exist
    log.warning("WHATSAPP_PROVIDER=%r not implemented — console fallback", provider)
    return ConsoleSender()
