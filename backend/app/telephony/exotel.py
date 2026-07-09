"""Exotel Voicebot (bidirectional Voice Streaming) gateway.

Exotel's Voicebot applet opens a WebSocket to us and exchanges JSON messages
carrying base64-encoded raw/slin PCM (16-bit, mono, little-endian). The
conversation stack (VoiceSession) already speaks a WebSocket dialect — binary
PCM16 frames in, binary PCM16 out, plus JSON control text. So instead of forking
VoiceSession, we wrap the raw carrier socket in ExotelTransport, which exposes the
*same* interface VoiceSession calls on `self.ws` (accept / receive / send_text /
send_bytes / close) and translates in both directions:

  Exotel → us :  {"event":"media","media":{"payload": b64}}  →  {"bytes": pcm16@16k}
  us → Exotel :  send_bytes(pcm16@24k TTS)  →  {"event":"media","media":{"payload": b64}}

The whole barge-in / VAD / STT / LLM / TTS pipeline is reused unchanged.

────────────────────────────────────────────────────────────────────────────
PROTOCOL (from Exotel "Working with the Stream and Voicebot Applet")
────────────────────────────────────────────────────────────────────────────
Inbound events : connected · start · media · dtmf · mark · stop · clear
  start.media_format.sample_rate  → the negotiated leg rate (8000/16000/24000).
    Set it in App Bazaar via the Voicebot URL query param, e.g.
        wss://your-host/ws/exotel?sample-rate=16000
    16 kHz is recommended: it matches our STT input rate, so inbound audio needs
    NO resampling and quality on the phone leg is better than 8 kHz PSTN.
Outbound events: media · mark · clear
  media.payload  → base64 PCM at the negotiated leg rate. Chunks must be a
    multiple of 320 bytes; min 3.2 kB, max 100 kB (see _MIN_SEND/_MAX_SEND).
  clear          → drop audio we already sent but Exotel hasn't played yet. We
    emit this on BARGE-IN so an interrupted agent goes silent on the phone
    immediately, not after the buffered sentence finishes.

Audio-rate map:
  inbound  Exotel(leg) → 16 kHz  (passthrough when leg == 16 kHz)
  outbound 24 kHz TTS  → Exotel(leg)
"""
from __future__ import annotations

import base64
import binascii
import json
import logging

from fastapi import APIRouter, WebSocket

from ..api.ws_voice import VoiceSession
from ..config import Settings
from .resample import resample_pcm16

log = logging.getLogger(__name__)
router = APIRouter()

# Outbound chunking to Exotel. Must be multiples of 320 bytes; spec floor 3.2 kB,
# ceiling 100 kB. We aim a little under the ceiling on a 320-byte boundary.
_MULTIPLE = 320
_MIN_SEND = 3200      # ~100 ms @ 16 kHz — don't send jittery sub-100 ms packets
_MAX_SEND = 96000     # < 100 kB, 320-aligned


class ExotelTransport:
    """Adapts a raw Exotel Voicebot WebSocket to the VoiceSession `ws` interface."""

    #: TTS PCM handed to send_bytes is at this rate (Sarvam Bulbul default 24 kHz).
    def __init__(self, ws: WebSocket, settings: Settings):
        self.ws = ws
        self.s = settings
        self._tts_rate = settings.tts_sample_rate

        # Negotiated Exotel leg rate; overwritten from the start message.
        self.leg_rate = settings.exotel_sample_rate

        # Populated from the start message — useful for personalization / logs.
        self.stream_sid: str | None = None
        self.call_sid: str | None = None
        self.account_sid: str | None = None
        self.from_number: str | None = None
        self.to_number: str | None = None
        self.custom_parameters: dict = {}

        self._out_seq = 0
        self._out_buf = bytearray()   # pending outbound PCM at leg_rate
        self._closed = False

    # ── VoiceSession-facing interface ──────────────────────────────────────────

    async def accept(self) -> None:
        """Accept the socket, then block until the `start` message arrives.

        VoiceSession sends the greeting immediately after accept(), but we cannot
        emit media to Exotel without a stream_sid — which only the start message
        carries. Consuming connected+start here guarantees stream_sid is set
        before the greeting is spoken.
        """
        await self.ws.accept()
        while self.stream_sid is None and not self._closed:
            msg = await self.ws.receive()
            if msg.get("type") == "websocket.disconnect":
                self._closed = True
                return
            text = msg.get("text")
            if text is None:
                continue
            try:
                data = json.loads(text)
            except (json.JSONDecodeError, TypeError):
                continue
            if data.get("event") == "start":
                self._on_start(data)

    def _on_start(self, data: dict) -> None:
        start = data.get("start") or {}
        self.stream_sid = data.get("stream_sid") or start.get("stream_sid")
        self.call_sid = start.get("call_sid")
        self.account_sid = start.get("account_sid")
        self.from_number = start.get("from")
        self.to_number = start.get("to")
        self.custom_parameters = start.get("custom_parameters") or {}
        mf = start.get("media_format") or {}
        try:
            if mf.get("sample_rate"):
                self.leg_rate = int(mf["sample_rate"])
        except (ValueError, TypeError):
            pass
        log.info(
            "exotel start: stream=%s call=%s from=%s to=%s leg_rate=%dHz params=%s",
            self.stream_sid, self.call_sid, self.from_number, self.to_number,
            self.leg_rate, self.custom_parameters,
        )

    async def receive(self) -> dict:
        """Return a Starlette-shaped message dict VoiceSession understands.

        media → {"type":"websocket.receive","bytes": pcm16@16k}
        stop / disconnect → {"type":"websocket.disconnect"}
        connected / mark / dtmf → consumed internally; keep reading.
        """
        while True:
            msg = await self.ws.receive()
            if msg.get("type") == "websocket.disconnect":
                self._closed = True
                return {"type": "websocket.disconnect"}
            text = msg.get("text")
            if text is None:
                continue
            try:
                data = json.loads(text)
            except (json.JSONDecodeError, TypeError):
                continue

            event = data.get("event")
            if event == "media":
                pcm = self._decode_media(data)
                if pcm is None:
                    continue
                # Upsample the phone leg to our 16 kHz STT/VAD rate (passthrough
                # when the leg is already 16 kHz).
                pcm16 = resample_pcm16(pcm, self.leg_rate, self.s.input_sample_rate)
                return {"type": "websocket.receive", "bytes": pcm16}
            elif event == "stop":
                log.info("exotel stop: %s", (data.get("stop") or {}).get("reason"))
                self._closed = True
                return {"type": "websocket.disconnect"}
            elif event == "dtmf":
                digit = (data.get("dtmf") or {}).get("digit")
                log.info("exotel dtmf: %s", digit)
                continue
            elif event in ("connected", "mark", "clear"):
                continue
            else:
                log.debug("exotel: ignoring event %r", event)
                continue

    @staticmethod
    def _decode_media(data: dict) -> bytes | None:
        payload = (data.get("media") or {}).get("payload")
        if not payload:
            return None
        try:
            return base64.b64decode(payload)
        except (binascii.Error, ValueError):
            log.warning("exotel: bad base64 media payload")
            return None

    async def send_text(self, text: str) -> None:
        """Intercept VoiceSession control JSON. Most is browser-UI telemetry that
        Exotel does not understand and we drop; two events matter:

          interrupted → BARGE-IN: clear buffered playback on the phone leg.
          audio_end   → sentence finished: flush the outbound tail so short final
                        fragments (< _MIN_SEND) are not stranded in the buffer.
        """
        try:
            obj = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return
        t = obj.get("type")
        if t == "interrupted":
            await self._send_clear()
        elif t == "audio_end":
            await self._flush_out(force=True)

    async def send_bytes(self, pcm: bytes) -> None:
        """TTS PCM (24 kHz) → resample to the leg rate, buffer, emit full chunks."""
        if self._closed or self.stream_sid is None:
            return
        self._out_buf.extend(resample_pcm16(pcm, self._tts_rate, self.leg_rate))
        await self._flush_out(force=False)

    async def close(self) -> None:
        self._closed = True
        try:
            await self.ws.close()
        except Exception:
            pass

    # ── outbound helpers ───────────────────────────────────────────────────────

    async def _flush_out(self, force: bool) -> None:
        buf = self._out_buf
        while len(buf) >= _MIN_SEND:
            n = min(len(buf), _MAX_SEND)
            n -= n % _MULTIPLE
            if n == 0:
                break
            chunk = bytes(buf[:n])
            del buf[:n]
            await self._send_media(chunk)
        if force and buf:
            n = len(buf)
            pad = (-n) % _MULTIPLE           # zero-pad up to a 320-byte boundary
            chunk = bytes(buf) + b"\x00" * pad
            buf.clear()
            await self._send_media(chunk)

    async def _send_media(self, pcm: bytes) -> None:
        if self._closed or self.stream_sid is None:
            return
        self._out_seq += 1
        msg = {
            "event": "media",
            "stream_sid": self.stream_sid,
            "sequence_number": self._out_seq,
            "media": {"payload": base64.b64encode(pcm).decode("ascii")},
        }
        try:
            await self.ws.send_text(json.dumps(msg))
        except Exception:
            self._closed = True

    async def _send_clear(self) -> None:
        """Barge-in: drop our buffered tail AND tell Exotel to stop playing."""
        self._out_buf.clear()
        if self._closed or self.stream_sid is None:
            return
        try:
            await self.ws.send_text(
                json.dumps({"event": "clear", "stream_sid": self.stream_sid})
            )
        except Exception:
            self._closed = True


def _authorized(ws: WebSocket, settings: Settings) -> bool:
    """Optional HTTP Basic auth. Exotel sends `Authorization: Basic base64(key:token)`
    when the Voicebot URL is `wss://<key>:<token>@host/...`. Enforced only when both
    EXOTEL_API_KEY and EXOTEL_API_TOKEN are configured; otherwise rely on Exotel IP
    whitelisting and accept all (keeps local testing frictionless)."""
    if not (settings.exotel_api_key and settings.exotel_api_token):
        return True
    header = ws.headers.get("authorization", "")
    if not header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:]).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return False
    expected = f"{settings.exotel_api_key}:{settings.exotel_api_token}"
    return decoded == expected


@router.websocket("/ws/exotel")
async def ws_exotel(ws: WebSocket):
    deps = ws.app.state.deps
    settings: Settings = deps.settings
    if not settings.exotel_enabled:
        await ws.close(code=1013)  # try again later / disabled
        return
    if not _authorized(ws, settings):
        await ws.close(code=1008)  # policy violation
        log.warning("exotel: unauthorized connection rejected")
        return
    transport = ExotelTransport(ws, settings)
    await VoiceSession(transport, deps).run()
