"""Sarvam Bulbul TTS. Sentence-level streaming: the manager feeds sentences as Gemini
produces them; each sentence returns PCM16 which we chunk onto the WS. An LRU cache
keeps repeated lines (greetings, confirmations, closings) at ~0 ms."""
from __future__ import annotations

import base64
import hashlib
import logging
from collections import OrderedDict
from typing import AsyncIterator

import httpx

from ..config import Settings
from .base import ProviderError

log = logging.getLogger(__name__)

# Sarvam target_language_code per engine language.
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
        self._cache: OrderedDict[str, bytes] = OrderedDict()

    def _key(self, text: str, lang: str, pace: float) -> str:
        raw = f"{text}|{lang}|{self.s.tts_speaker}|{pace}|{self.s.tts_sample_rate}"
        return hashlib.sha1(raw.encode()).hexdigest()

    async def _synthesize_full(self, text: str, lang: str, pace: float) -> bytes:
        key = self._key(text, lang, pace)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        payload = {
            "model": self.s.tts_model,
            "text": text,
            "target_language_code": _LANG_CODE.get(lang, "hi-IN"),
            "speaker": self.s.tts_speaker,
            "pace": pace,
            "speech_sample_rate": self.s.tts_sample_rate,
            "enable_preprocessing": True,
        }
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

    async def synthesize(self, text: str, language: str,
                         pace: float | None = None) -> AsyncIterator[bytes]:
        """Synthesize one line. `pace` is the per-utterance pace planned by the
        Human Speech Engine (Voice Director style pace, dropped for long
        numbers); falls back to the global Settings.tts_pace when None."""
        text = text.strip()
        if not text:
            return
        pcm = await self._synthesize_full(text, language, pace or self.s.tts_pace)
        for i in range(0, len(pcm), _CHUNK):
            yield pcm[i : i + _CHUNK]
