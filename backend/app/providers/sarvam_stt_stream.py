"""Sarvam streaming STT (WebSocket) — transcribe WHILE the caller speaks.

LATENCY: the REST path waits for the caller to stop, then pays a full
transcription round-trip (~350–700 ms). This provider feeds audio to Sarvam's
`speech-to-text` WebSocket continuously as it arrives, so by the time our
endpointer fires UTTERANCE, most of the transcript already exists — `finalize()`
sends a flush and typically returns in ~100–250 ms. Net saving: 250–500 ms per
turn, the single biggest step toward sub-1.1 s conversations.

DESIGN — safety first (this integrates a live external protocol):
  • Feature-flagged: Settings.stt_streaming_enabled (default OFF).
  • Fire-and-forget feeding via an internal queue — never blocks the RX loop.
  • ANY error disables streaming for the session and the caller transparently
    falls back to the REST transcribe() path (the endpointer still buffers the
    full utterance, so nothing is ever lost).
  • Language: Sarvam's streaming endpoint wants a language hint; we follow the
    call's active language (LanguageEngine) and reconnect when it changes.
    Transcript language is re-verified downstream by LanguageEngine anyway.

COST NOTE: continuous feeding sends silence too, so streamed audio-hours bill
higher than VAD-gated REST (~₹0.5/min vs ~₹0.21/min of call). That is the
price of the latency win; disable the flag to revert.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import time

from ..config import Settings
from .base import Transcript

log = logging.getLogger(__name__)

_LANG_CODE = {"mr": "mr-IN", "hi": "hi-IN", "en": "en-IN", "und": "mr-IN"}
_FINALIZE_TIMEOUT_S = 1.5      # max wait after flush before REST fallback
_SEND_Q_MAX = 200              # ~queue of pending audio messages


class SarvamSTTStream:
    """One instance per call. feed() is non-blocking; finalize() returns the
    transcript accumulated for the current utterance window, or None → caller
    should fall back to REST."""

    def __init__(self, settings: Settings, lang_getter):
        self.s = settings
        self._lang_getter = lang_getter          # () -> "mr"|"hi"|"en"|"und"
        self._client = None                      # sarvamai AsyncSarvamAI
        self._cm = None                          # connect() context manager
        self._ws = None
        self._sender: asyncio.Task | None = None
        self._reader: asyncio.Task | None = None
        self._q: asyncio.Queue = asyncio.Queue(maxsize=_SEND_Q_MAX)
        self._parts: list[str] = []              # transcript pieces this window
        self._final_evt = asyncio.Event()
        self._lang = None                        # language the socket was opened with
        self.disabled = False                    # tripped on any error → REST only

    # ── lifecycle ────────────────────────────────────────────────────────────
    async def _ensure_connected(self) -> bool:
        if self.disabled:
            return False
        lang = _LANG_CODE.get(self._lang_getter() or "und", "mr-IN")
        if self._ws is not None and lang == self._lang:
            return True
        await self.close(reconnecting=True)
        try:
            from sarvamai import AsyncSarvamAI   # optional dependency
            self._client = self._client or AsyncSarvamAI(
                api_subscription_key=self.s.sarvam_api_key)
            self._cm = self._client.speech_to_text_streaming.connect(
                model="saaras:v3",
                mode="codemix",
                language_code=lang,
                sample_rate=self.s.input_sample_rate,
                input_audio_codec="pcm_s16le",
                high_vad_sensitivity=True,
                flush_signal=True,
            )
            self._ws = await asyncio.wait_for(self._cm.__aenter__(), timeout=3.0)
            self._lang = lang
            self._sender = asyncio.create_task(self._send_loop(), name="stt_stream_tx")
            self._reader = asyncio.create_task(self._read_loop(), name="stt_stream_rx")
            log.info("stt-stream: connected (lang=%s)", lang)
            return True
        except Exception as e:
            log.warning("stt-stream: connect failed (%s) — session falls back to REST", e)
            self.disabled = True
            return False

    async def _send_loop(self) -> None:
        try:
            while True:
                pcm = await self._q.get()
                if pcm is None:                          # flush marker
                    await self._ws.flush()
                    continue
                await self._ws.transcribe(
                    audio=base64.b64encode(pcm).decode("ascii"),
                    encoding="pcm_s16le",
                    sample_rate=self.s.input_sample_rate,
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("stt-stream: send failed (%s) — falling back to REST", e)
            self.disabled = True

    async def _read_loop(self) -> None:
        try:
            async for message in self._ws:
                mtype = getattr(message, "type", None)
                if mtype == "data":
                    text = getattr(getattr(message, "data", None), "transcript", "") or ""
                    if text.strip():
                        self._parts.append(text.strip())
                        self._final_evt.set()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("stt-stream: read failed (%s) — falling back to REST", e)
            self.disabled = True

    # ── hot path ─────────────────────────────────────────────────────────────
    def feed(self, pcm16: bytes) -> None:
        """Queue audio for the stream. Never blocks; drops (and disables) only
        if the queue is persistently full (connection stalled)."""
        if self.disabled or self._ws is None:
            return
        try:
            self._q.put_nowait(pcm16)
        except asyncio.QueueFull:
            log.warning("stt-stream: send queue full — disabling for this session")
            self.disabled = True

    async def start_if_needed(self) -> None:
        """Cheap idempotent connect used at call start / language change."""
        await self._ensure_connected()

    async def finalize(self) -> Transcript | None:
        """Caller stopped (our endpointer fired): flush and return everything
        transcribed for this window. None → REST fallback."""
        if self.disabled or self._ws is None:
            return None
        try:
            self._final_evt.clear()
            self._q.put_nowait(None)                     # flush marker
            deadline = time.monotonic() + _FINALIZE_TIMEOUT_S
            # wait for at least one (more) data message, then a short settle
            while not self._parts and time.monotonic() < deadline:
                try:
                    await asyncio.wait_for(self._final_evt.wait(), timeout=0.1)
                except asyncio.TimeoutError:
                    continue
            if not self._parts:
                log.info("stt-stream: no transcript by deadline — REST fallback")
                return None
            # brief settle window to catch a trailing segment
            await asyncio.sleep(0.08)
            text = " ".join(self._parts).strip()
            self._parts.clear()
            # Streaming endpoint doesn't return a reliable language id — let the
            # LanguageEngine detect from the words (hint=unknown).
            return Transcript(text=text, language="unknown", raw={},
                              language_confidence=None)
        except Exception as e:
            log.warning("stt-stream: finalize failed (%s) — REST fallback", e)
            self.disabled = True
            return None

    async def close(self, reconnecting: bool = False) -> None:
        for t in (self._sender, self._reader):
            if t and not t.done():
                t.cancel()
        self._sender = self._reader = None
        if self._cm is not None:
            try:
                await self._cm.__aexit__(None, None, None)
            except Exception:
                pass
        self._cm = self._ws = None
        self._parts.clear()
        if not reconnecting:
            log.info("stt-stream: closed")
