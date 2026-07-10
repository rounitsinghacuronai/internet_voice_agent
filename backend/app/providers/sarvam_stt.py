"""Sarvam Saaras STT. Utterance-scoped: our VAD endpointing hands over one clean
utterance; Saaras auto-detects the language per utterance (language=unknown), which is
what makes the language engine work for code-mixed callers. mode=codemix keeps native
script + Latin English words — best for Hinglish/Marathi-English."""
from __future__ import annotations

import io
import logging
import wave

import httpx

from ..config import Settings
from .base import ProviderError, Transcript

log = logging.getLogger(__name__)


def _pcm16_to_wav(pcm16: bytes, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm16)
    return buf.getvalue()


class SarvamSTT:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.s = settings
        self.client = client

    async def transcribe(self, pcm16: bytes, sample_rate: int) -> Transcript:
        wav = _pcm16_to_wav(pcm16, sample_rate)
        data = {"model": self.s.stt_model}
        if self.s.stt_language:
            data["language_code"] = self.s.stt_language   # "unknown" = auto-detect
        try:
            r = await self.client.post(
                f"{self.s.sarvam_base}/speech-to-text",
                headers={"api-subscription-key": self.s.sarvam_api_key},
                data=data,
                files={"file": ("utt.wav", wav, "audio/wav")},
                timeout=15.0,
            )
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise ProviderError("sarvam_stt",
                                f"HTTP {e.response.status_code}: {e.response.text[:300]}") from e
        except httpx.HTTPError as e:
            raise ProviderError("sarvam_stt", e) from e
        body = r.json()
        return Transcript(
            text=(body.get("transcript") or "").strip(),
            language=body.get("language_code") or "unknown",
            raw=body,
            language_confidence=body.get("language_probability"),
        )
