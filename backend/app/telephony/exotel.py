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

import asyncio
import base64
import binascii
import json
import logging
import re
import time

from fastapi import APIRouter, WebSocket

from ..api.ws_voice import VoiceSession
from ..config import Settings
from .resample import resample_pcm16

log = logging.getLogger(__name__)
router = APIRouter()

# How long to wait for Exotel's `start` message after the WS handshake.
_START_TIMEOUT_S = 10.0


def _parse_sample_rate(value) -> int | None:
    """Parse a sample rate that may arrive as 8000, "8000", "8k", "8khz"…
    Returns a validated rate or None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        rate = int(value)
    else:
        m = re.match(r"\s*(\d+)\s*(k(?:hz)?)?\s*$", str(value), re.IGNORECASE)
        if not m:
            return None
        rate = int(m.group(1)) * (1000 if m.group(2) else 1)
    return rate if rate in (8000, 16000, 24000) else None

# Outbound chunking to Exotel. Must be multiples of 320 bytes; spec floor 3.2 kB,
# ceiling 100 kB. IMPORTANT: the ceiling applies to the transmitted payload, and
# base64 inflates raw PCM by 4/3 — the old 96 kB raw ceiling produced 128 kB
# frames, over Exotel's limit, and the stream was cancelled the moment the
# greeting burst hit it. 32 kB raw (~1 s @ 16 kHz, ~43 kB base64) stays far
# inside the limit, and smaller chunks also make Exotel's `clear` (barge-in)
# take effect almost immediately instead of after a long buffered chunk.
_MULTIPLE = 320
_MIN_SEND = 3200      # spec floor — don't send jittery sub-100 ms packets
_MAX_SEND = 32000     # raw bytes; ~43 kB after base64, well under the 100 kB cap

# PACING — the critical fix for "call connects then Exotel cancels instantly".
# Exotel's platform expects audio roughly in real time (their own echobot paces
# chunks with explicit delays). Our TTS produces a whole sentence at once, so
# without pacing the 6-second greeting hit Exotel as one instant burst and the
# platform cancelled the stream within the same second. We therefore never run
# more than _LEAD_S seconds ahead of real-time playback: enough headroom that
# the caller never hears a gap, small enough that Exotel is never flooded —
# and barge-in `clear` only ever has ≲1 s of buffered audio to discard.
_LEAD_S = 1.0
# Per-message audio duration. Small messages also make `clear` act instantly.
_CHUNK_S = 0.4


class ExotelTransport:
    """Adapts a raw Exotel Voicebot WebSocket to the VoiceSession `ws` interface."""

    #: TTS PCM handed to send_bytes is at this rate (Sarvam Bulbul default 24 kHz).
    def __init__(self, ws: WebSocket, settings: Settings):
        self.ws = ws
        self.s = settings
        self._tts_rate = settings.tts_sample_rate

        # Negotiated Exotel leg rate. Priority: start message (authoritative)
        # > ?sample-rate= query param on the applet URL > configured default.
        # NOTE: Exotel defaults the leg to 8 kHz when the applet URL has no
        # ?sample-rate= param — assuming 16 kHz then garbles audio both ways.
        qp = getattr(ws, "query_params", None) or {}
        self.leg_rate = (
            _parse_sample_rate(qp.get("sample-rate"))
            or settings.exotel_sample_rate
        )

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

        # outbound pacing state (see _LEAD_S above)
        self._pace_t0: float | None = None   # monotonic anchor of current burst
        self._sent_s = 0.0                   # seconds of audio sent since anchor

        # media timestamp anchor (ms since stream start, per Exotel's schema)
        self._stream_t0: float = time.monotonic()
        self._out_chunk = 0

        # diagnostics: counts of every inbound event type, logged at stop —
        # tells us definitively whether Exotel ever streamed caller audio.
        self._rx_events: dict[str, int] = {}

    # ── VoiceSession-facing interface ──────────────────────────────────────────

    async def accept(self) -> None:
        """Accept the socket, then block until the `start` message arrives.

        VoiceSession sends the greeting immediately after accept(), but we cannot
        emit media to Exotel without a stream_sid — which only the start message
        carries. Consuming connected+start here guarantees stream_sid is set
        before the greeting is spoken.
        """
        await self.ws.accept()
        deadline = asyncio.get_event_loop().time() + _START_TIMEOUT_S
        while self.stream_sid is None and not self._closed:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                log.error("exotel: no start message within %.0fs — closing", _START_TIMEOUT_S)
                await self.close()
                return
            try:
                msg = await asyncio.wait_for(self.ws.receive(), timeout=remaining)
            except asyncio.TimeoutError:
                continue
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
                self._stream_t0 = time.monotonic()

    def _on_start(self, data: dict) -> None:
        start = data.get("start") or {}
        self.stream_sid = data.get("stream_sid") or start.get("stream_sid")
        self.call_sid = start.get("call_sid")
        self.account_sid = start.get("account_sid")
        self.from_number = start.get("from")
        self.to_number = start.get("to")
        self.custom_parameters = start.get("custom_parameters") or {}
        mf = start.get("media_format") or {}
        rate = _parse_sample_rate(mf.get("sample_rate"))
        if rate:
            self.leg_rate = rate
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
            # Never touch the underlying socket after it's gone — Starlette raises
            # RuntimeError on receive-after-disconnect, which would crash the
            # session instead of ending it cleanly.
            if self._closed:
                return {"type": "websocket.disconnect"}
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
            self._rx_events[event or "?"] = self._rx_events.get(event or "?", 0) + 1
            if event == "media":
                pcm = self._decode_media(data)
                if pcm is None:
                    continue
                # Upsample the phone leg to our 16 kHz STT/VAD rate (passthrough
                # when the leg is already 16 kHz).
                pcm16 = resample_pcm16(pcm, self.leg_rate, self.s.input_sample_rate)
                return {"type": "websocket.receive", "bytes": pcm16}
            elif event == "stop":
                log.info("exotel stop: reason=%r | rx events=%s | tx media msgs=%d",
                         (data.get("stop") or {}).get("reason"),
                         self._rx_events, self._out_seq)
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
        # time-based message size: ~_CHUNK_S of audio per message, 320-aligned,
        # never above the spec ceiling.
        per_msg = min(_MAX_SEND, int(_CHUNK_S * self.leg_rate * 2))
        per_msg -= per_msg % _MULTIPLE
        per_msg = max(per_msg, _MIN_SEND)
        while len(buf) >= max(_MIN_SEND, per_msg):
            chunk = bytes(buf[:per_msg])
            del buf[:per_msg]
            await self._send_media(chunk)
        if force and buf:
            n = len(buf)
            pad = (-n) % _MULTIPLE           # zero-pad up to a 320-byte boundary
            chunk = bytes(buf) + b"\x00" * pad
            buf.clear()
            await self._send_media(chunk)

    async def _pace(self, chunk_s: float) -> None:
        """Sleep just enough that we never run more than _LEAD_S seconds of audio
        ahead of real-time playback. Cancellable — a barge-in cancels the speaker
        task mid-sleep and the un-sent tail is simply dropped."""
        now = time.monotonic()
        if self._pace_t0 is None or self._sent_s - (now - self._pace_t0) < 0:
            # fresh burst, or playback already caught up — re-anchor
            self._pace_t0 = now
            self._sent_s = 0.0
        ahead = self._sent_s - (now - self._pace_t0)
        if ahead > _LEAD_S:
            await asyncio.sleep(ahead - _LEAD_S)
        self._sent_s += chunk_s

    async def _send_media(self, pcm: bytes) -> None:
        if self._closed or self.stream_sid is None:
            return
        await self._pace(len(pcm) / 2.0 / self.leg_rate)
        if self._closed:
            return
        self._out_seq += 1
        self._out_chunk += 1
        # EVERY numeric field goes as a STRING. Confirmed by Exotel platform
        # engineering (11 Jul 2026): their Go ingest struct declares chunk,
        # sequence_number and timestamp as string; an int in ANY of them fails
        # json.Unmarshal ("cannot unmarshal number into Go struct field
        # Media.media.chunk of type string") and the platform closes the stream
        # — which surfaced as every call dropping ~1 s after pickup. Their
        # support-center doc shows `"chunk": 2` as a number; the doc is wrong.
        msg = {
            "event": "media",
            "stream_sid": self.stream_sid,
            "sequence_number": str(self._out_seq),
            "media": {
                "chunk": str(self._out_chunk),
                "timestamp": str(int((time.monotonic() - self._stream_t0) * 1000)),
                "payload": base64.b64encode(pcm).decode("ascii"),
            },
        }
        try:
            await self.ws.send_text(json.dumps(msg))
        except Exception:
            self._closed = True

    async def _send_clear(self) -> None:
        """Barge-in: drop our buffered tail AND tell Exotel to stop playing."""
        self._out_buf.clear()
        self._pace_t0 = None                 # reset pacing for the next reply
        self._sent_s = 0.0
        if self._closed or self.stream_sid is None:
            return
        try:
            await self.ws.send_text(
                json.dumps({"event": "clear", "stream_sid": self.stream_sid})
            )
        except Exception:
            self._closed = True


def _authorized(ws: WebSocket, settings: Settings) -> bool:
    """Optional auth, enforced only when BOTH EXOTEL_API_KEY and EXOTEL_API_TOKEN
    are configured; otherwise rely on Exotel IP whitelisting and accept all.

    Two accepted forms (Exotel supports credentials in the applet URL):
      1. Basic header — applet URL `wss://<key>:<token>@host/ws/exotel`;
         Exotel converts the userinfo to `Authorization: Basic base64(key:token)`.
      2. Query params — applet URL `wss://host/ws/exotel?key=<key>&token=<token>`
         (counts toward Exotel's 3-custom-param / 256-char limit).

    WARNING logged on rejection with the exact reason — a mismatch here is
    otherwise invisible and shows up only as calls hanging up after ~1 second.
    """
    if not (settings.exotel_api_key and settings.exotel_api_token):
        return True
    # form 2: query params
    qp = getattr(ws, "query_params", None) or {}
    if (qp.get("key") == settings.exotel_api_key
            and qp.get("token") == settings.exotel_api_token):
        return True
    # form 1: Basic header
    header = ws.headers.get("authorization", "")
    if not header.startswith("Basic "):
        log.error(
            "exotel AUTH REJECT: EXOTEL_API_KEY/TOKEN are set on this server but the "
            "connection carried no Authorization header and no ?key=&token= params. "
            "Fix ONE of: (a) set the App Bazaar Voicebot URL to "
            "wss://<key>:<token>@<host>/ws/exotel, (b) append ?key=<key>&token=<token>, "
            "or (c) unset EXOTEL_API_KEY/EXOTEL_API_TOKEN in .env and use Exotel IP "
            "whitelisting. Until then every call will ring and drop after ~1 second."
        )
        return False
    try:
        decoded = base64.b64decode(header[6:]).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        log.error("exotel AUTH REJECT: malformed Basic authorization header")
        return False
    if decoded != f"{settings.exotel_api_key}:{settings.exotel_api_token}":
        log.error("exotel AUTH REJECT: key/token mismatch — the credentials in the "
                  "App Bazaar URL do not match EXOTEL_API_KEY/EXOTEL_API_TOKEN in .env")
        return False
    return True


@router.websocket("/ws/exotel")
async def ws_exotel(ws: WebSocket):
    deps = ws.app.state.deps
    settings: Settings = deps.settings
    if not settings.exotel_enabled:
        await ws.close(code=1013)  # try again later / disabled
        return
    if not _authorized(ws, settings):
        await ws.close(code=1008)  # policy violation
        return
    transport = ExotelTransport(ws, settings)
    try:
        await VoiceSession(transport, deps).run()
    except Exception:
        # A crash here silently kills the phone call — make it loud in the logs
        # and close the leg cleanly instead of leaving Exotel hanging.
        log.exception("exotel: session crashed (call=%s stream=%s)",
                      transport.call_sid, transport.stream_sid)
        await transport.close()
