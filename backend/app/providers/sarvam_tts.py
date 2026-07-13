"""Sarvam Bulbul TTS. Sentence-level streaming: the manager feeds sentences as Gemini
produces them; each sentence returns PCM16 which we chunk onto the WS. An LRU cache
keeps repeated lines (greetings, confirmations, closings) at ~0 ms.

LATENCY: `prefetch()` + in-flight de-duplication let the WS layer start synthesizing
sentence N+1 while sentence N is still playing, so consecutive sentences flow with no
audible HTTP-round-trip gap between them (the biggest per-turn latency after TTFT).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
from collections import OrderedDict
from typing import AsyncIterator

import httpx

from ..config import Settings
from ..persona import get_persona
from .base import ProviderError

log = logging.getLogger(__name__)

# Sarvam target_language_code per engine language. Marathi is the fallback —
# this deployment serves Maharashtra (Mahavitaran) only.
_LANG_CODE = {"mr": "mr-IN", "hi": "hi-IN", "en": "en-IN"}


def _strip_wav_header(data: bytes) -> bytes:
    """Return raw PCM bytes from a WAV/RIFF blob.

    Walks the RIFF chunk tree to find the 'data' sub-chunk rather than
    hard-coding a fixed 44-byte offset.  Some TTS providers return WAV files
    with 'fmt ' extensions or extra 'LIST' chunks that push the PCM data
    beyond byte 44, causing silence or distortion when stripped incorrectly.
    """
    if data[:4] != b"RIFF":
        return data  # already raw PCM — pass through
    pos = 12  # skip RIFF(4) + file-size(4) + WAVE(4)
    while pos + 8 <= len(data):
        chunk_id = data[pos : pos + 4]
        chunk_size = int.from_bytes(data[pos + 4 : pos + 8], "little")
        if chunk_id == b"data":
            return data[pos + 8 : pos + 8 + chunk_size]
        pos += 8 + chunk_size
        if chunk_size % 2:          # RIFF chunks are word-aligned
            pos += 1
    # Fallback: assume the minimal 44-byte header (should never reach here)
    log.warning("sarvam_tts: could not find WAV 'data' chunk — falling back to 44-byte strip")
    return data[44:]


_CHUNK = 4800 * 2  # 200 ms of 24 kHz PCM16


class SarvamTTS:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.s = settings
        self.client = client
        # Voice resolution: explicit TTS_SPEAKER wins; otherwise the persona's
        # gender-matched default (male→advait, female→ritu — see persona.py).
        self.speaker = settings.tts_speaker or get_persona(settings).voice
        self._cache: OrderedDict[str, bytes] = OrderedDict()
        # streaming TTS state (Settings.tts_streaming_enabled)
        self._stream_client = None
        self._stream_disabled = False   # tripped on pre-first-chunk failure
        # key → in-flight synthesis task, so prefetch() and synthesize() of the
        # same line share ONE network call instead of racing duplicates.
        self._inflight: dict[str, asyncio.Task] = {}

    def _key(self, text: str, lang: str, pace: float) -> str:
        raw = f"{text}|{lang}|{self.speaker}|{pace}|{self.s.tts_sample_rate}"
        return hashlib.sha1(raw.encode()).hexdigest()

    async def _synthesize_full(self, text: str, lang: str, pace: float) -> bytes:
        key = self._key(text, lang, pace)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(self._fetch(key, text, lang, pace))
            self._inflight[key] = task
        # shield: a barge-in cancelling the speaker must not kill a synthesis
        # another waiter (or the cache) can still use.
        return await asyncio.shield(task)

    async def _fetch(self, key: str, text: str, lang: str, pace: float) -> bytes:
        payload = {
            "model": self.s.tts_model,
            "text": text,
            "target_language_code": _LANG_CODE.get(lang, "mr-IN"),
            "speaker": self.speaker,
            "pace": pace,
            "speech_sample_rate": self.s.tts_sample_rate,
            "enable_preprocessing": True,
        }
        try:
            try:
                r = await self.client.post(
                    f"{self.s.sarvam_base}/text-to-speech",
                    headers={"api-subscription-key": self.s.sarvam_api_key},
                    json=payload,
                    timeout=20.0,
                )
                r.raise_for_status()
            except httpx.HTTPStatusError as e:
                raise ProviderError("sarvam_tts",
                                    f"HTTP {e.response.status_code}: {e.response.text[:300]}") from e
            except httpx.HTTPError as e:
                raise ProviderError("sarvam_tts", e) from e
            audios = r.json().get("audios") or []
            if not audios:
                raise ProviderError("sarvam_tts", "empty audio")
            wav = base64.b64decode(audios[0])
            pcm = _strip_wav_header(wav)
            if not pcm:
                raise ProviderError("sarvam_tts", "WAV contained no PCM data")
            self._cache[key] = pcm
            while len(self._cache) > 256:
                self._cache.popitem(last=False)
            return pcm
        finally:
            self._inflight.pop(key, None)

    def prefetch(self, text: str, language: str, pace: float | None = None) -> None:
        """Fire-and-forget: start synthesizing a line NOW so the later
        synthesize() call for the same line is a cache hit. Errors are logged,
        never raised — the real synthesize() will surface them if they matter."""
        text = (text or "").strip()
        if not text:
            return
        p = pace or self.s.tts_pace
        key = self._key(text, language, p)
        if key in self._cache or key in self._inflight:
            return

        async def _warm() -> None:
            try:
                await self._synthesize_full(text, language, p)
            except Exception as e:
                log.debug("tts prefetch failed (will retry live): %s", e)

        asyncio.create_task(_warm(), name="tts_prefetch")

    async def synthesize(self, text: str, language: str,
                         pace: float | None = None) -> AsyncIterator[bytes]:
        """Synthesize one line. `pace` is the per-utterance pace planned by the
        Human Speech Engine (Voice Director style pace, dropped for long
        numbers); falls back to the global Settings.tts_pace when None.

        When Settings.tts_streaming_enabled is on and the line isn't cached,
        chunks are yielded PROGRESSIVELY from Sarvam's TTS WebSocket — first
        audio in ~200 ms instead of waiting for full synthesis (~350–500 ms).
        Any streaming failure falls back to the REST path transparently."""
        text = text.strip()
        if not text:
            return
        p = pace or self.s.tts_pace
        key = self._key(text, language, p)
        if (getattr(self.s, "tts_streaming_enabled", False)
                and key not in self._cache and not self._stream_disabled):
            got_any = False
            collected = bytearray()
            try:
                async for pcm in self._synthesize_ws(text, language, p):
                    got_any = True
                    collected.extend(pcm)
                    yield pcm
                if collected:                       # feed the cache for repeats
                    self._cache[key] = bytes(collected)
                    while len(self._cache) > 256:
                        self._cache.popitem(last=False)
                return
            except Exception as e:
                if got_any:
                    log.warning("tts-stream: failed mid-stream (%s)", e)
                    return                          # partial audio already sent
                log.warning("tts-stream: failed before first chunk (%s) — REST "
                            "fallback for this session", e)
                self._stream_disabled = True
        pcm = await self._synthesize_full(text, language, p)
        for i in range(0, len(pcm), _CHUNK):
            yield pcm[i : i + _CHUNK]

    async def _synthesize_ws(self, text: str, lang: str,
                             pace: float) -> AsyncIterator[bytes]:
        """One-shot streaming synthesis over Sarvam's TTS WebSocket. Yields raw
        PCM16 chunks at Settings.tts_sample_rate as they are generated."""
        from sarvamai import AsyncSarvamAI, AudioOutput   # optional dependency
        if self._stream_client is None:
            self._stream_client = AsyncSarvamAI(
                api_subscription_key=self.s.sarvam_api_key)
        async with self._stream_client.text_to_speech_streaming.connect(
                model=self.s.tts_model, send_completion_event=True) as ws:
            await ws.configure(
                target_language_code=_LANG_CODE.get(lang, "mr-IN"),
                speaker=self.speaker,
                pace=pace,
                speech_sample_rate=self.s.tts_sample_rate,
                output_audio_codec="pcm",           # LINEAR16 — no decode step
            )
            await ws.convert(text)
            await ws.flush()
            async for message in ws:
                if isinstance(message, AudioOutput):
                    chunk = base64.b64decode(message.data.audio)
                    pcm = _strip_wav_header(chunk)
                    if pcm:
                        yield pcm
                else:                                # completion / event message
                    ev = getattr(getattr(message, "data", None), "event_type", "")
                    if ev == "final":
                        break
