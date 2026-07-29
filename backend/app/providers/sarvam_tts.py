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
# this deployment serves the Maharashtra circle only.
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


def _parse_wav(data: bytes) -> tuple[bytes, int | None]:
    """Return (pcm, sample_rate|None). Raw PCM passes through with rate None;
    a RIFF blob has its fmt chunk parsed so the true rate is self-describing.

    STREAMING WAV SIZE FIELDS. A WAV written to a stream cannot know its own
    length in advance, so the 'data' chunk size is a placeholder — commonly 0,
    sometimes 0xFFFFFFFF, sometimes the declared total rather than what is in
    THIS message. Slicing data[pos+8 : pos+8+size] therefore returned b"" when
    the placeholder was 0, silently discarding the first streamed chunk of the
    sentence — the one carrying the WAV header, i.e. the audio for the opening
    ~200 ms. The caller heard the first syllable of the greeting (and of every
    sentence) clipped off, which reads as a disturbed/glitchy start rather
    than as missing audio. Treat the size as an upper bound only when it is
    both non-zero and actually present in this buffer; otherwise take
    everything after the header.
    """
    if data[:4] != b"RIFF":
        return data, None
    rate = None
    pos = 12
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        size = int.from_bytes(data[pos + 4:pos + 8], "little")
        if cid == b"fmt " and size >= 8:
            rate = int.from_bytes(data[pos + 12:pos + 16], "little")
        elif cid == b"data":
            payload = data[pos + 8:]
            if size and size <= len(payload):
                payload = payload[:size]      # trustworthy, complete-file size
            return payload, rate
        if size <= 0:                          # placeholder — no more chunks
            break                              #   can be walked past it
        pos += 8 + size + (size % 2)
    return _strip_wav_header(data), rate


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
        self._ws_cfg_known: list | None = None   # config probe result (cached)
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
            # Parse the header instead of blindly stripping it: Sarvam does not
            # always honour the requested `speech_sample_rate`, and the REST path
            # (unlike _synthesize_ws) used to ASSUME the PCM was already at
            # tts_sample_rate. When the returned rate differed, the audio played
            # back at the wrong SPEED (too fast/chipmunked or too slow/dragging)
            # with no resample — a direct cause of the "sometimes too fast,
            # sometimes too slow" complaint. Resample to our canonical rate so
            # everything downstream (loudness, Exotel leg resampler) is correct.
            pcm, hdr_rate = _parse_wav(wav)
            if not pcm:
                raise ProviderError("sarvam_tts", "WAV contained no PCM data")
            if hdr_rate and hdr_rate != self.s.tts_sample_rate:
                from ..telephony.resample import resample_pcm16
                log.warning("sarvam_tts: REST returned %d Hz but pipeline expects "
                            "%d Hz — resampling (else playback speed is wrong)",
                            hdr_rate, self.s.tts_sample_rate)
                pcm = resample_pcm16(pcm, hdr_rate, self.s.tts_sample_rate)
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
        log.info("sarvam tts usage: %d chars (lang=%s) — %s", len(text), language,
                 "CACHE HIT, 0 billed" if key in self._cache else "live synthesis")
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
                    # Partial audio already sent — re-synthesizing and sending
                    # the FULL text now would repeat/overlap the part the
                    # caller already heard, which is worse than the truncation.
                    # But leaving stream mode ON risks the SAME silent
                    # mid-sentence cutoff on every following sentence too if
                    # the connection is genuinely degraded (not a one-off) —
                    # so disable streaming for the rest of THIS call, same as
                    # the "failed before first chunk" branch below, and let
                    # every subsequent sentence use the more reliable REST path.
                    log.warning("tts-stream: failed mid-stream (%s) — this "
                                "sentence was truncated; disabling stream mode "
                                "for the rest of this session (REST fallback)", e)
                    self._stream_disabled = True
                    return
                log.warning("tts-stream: failed before first chunk (%s) — REST "
                            "fallback for this session", e)
                self._stream_disabled = True
        pcm = await self._synthesize_full(text, language, p)
        for i in range(0, len(pcm), _CHUNK):
            yield pcm[i : i + _CHUNK]

    async def _synthesize_ws(self, text: str, lang: str,
                             pace: float) -> AsyncIterator[bytes]:
        """One-shot streaming synthesis over Sarvam's TTS WebSocket. Yields raw
        PCM16 chunks at Settings.tts_sample_rate as they are generated.

        HARD RULES learned in production:
        • A config the server dislikes closes the socket CLEANLY → the stream
          ends with zero chunks and no exception. Zero chunks = FAILURE (raise
          → REST fallback), never success.
        • Because we cannot know which config field their WS rejects, we PROBE
          a sequence of config shapes on first use and remember the winner.
          WAV output is self-describing (sample rate parsed from the header),
          so mismatched rates are resampled instead of playing chipmunked."""
        from sarvamai import AsyncSarvamAI, AudioOutput   # optional dependency
        from ..telephony.resample import StreamResampler
        if self._stream_client is None:
            self._stream_client = AsyncSarvamAI(
                api_subscription_key=self.s.sarvam_api_key)

        base = dict(target_language_code=_LANG_CODE.get(lang, "mr-IN"),
                    speaker=self.speaker, pace=pace)
        # (config-extras, assumed source sample rate when not self-describing)
        #
        # ORDER MATTERS FOR QUALITY, and this is the reference deployment's
        # order. Asking for raw PCM already at tts_sample_rate is the CLEANEST
        # possible path: no RIFF parsing, and — because the audio arrives at
        # exactly the rate the pipeline expects — no resample here at all. The
        # only conversion the samples then undergo in the whole outbound path
        # is the single 24k->leg_rate step in ExotelTransport.send_bytes.
        #
        # This had been reordered to try WAV first, on the belief that the pcm
        # shape "silently yields zero audio". That put every sentence through
        # RIFF parsing plus TWO resampling stages (22050->24000 here, then
        # 24000->16000 for the leg), and exposed it to the streaming-WAV size
        # placeholder bug fixed in _parse_wav above. Reverted to the reference
        # order. The fallback chain makes this safe rather than a gamble: if the
        # pcm config genuinely produces no audio, the loop logs it and drops
        # through to WAV exactly as before, and _ws_cfg_known then caches
        # whichever shape actually worked so later sentences skip the probe.
        attempts = self._ws_cfg_known or [
            ({"output_audio_codec": "pcm",
              "speech_sample_rate": self.s.tts_sample_rate}, self.s.tts_sample_rate),
            ({"output_audio_codec": "wav"}, None),        # rate read from header
            ({"output_audio_codec": "pcm"}, 22050),       # Bulbul REST default
        ]

        last_err: Exception | None = None
        for extras, assumed_rate in attempts:
            got_audio = False
            src_rate = assumed_rate
            # Stateful — see StreamResampler — so the many small WAV chunks
            # Sarvam streams for ONE sentence resample as one continuous
            # signal instead of each independently restarting the
            # interpolation reference (a click at every chunk boundary,
            # compounding with the second independent resample this PCM
            # gets in ExotelTransport.send_bytes downstream).
            resampler: StreamResampler | None = None
            try:
                async with self._stream_client.text_to_speech_streaming.connect(
                        model=self.s.tts_model, send_completion_event=True) as ws:
                    try:
                        await ws.configure(**base, **extras)
                    except Exception as e:
                        import inspect
                        try:
                            sig = str(inspect.signature(ws.configure))
                        except Exception:
                            sig = "?"
                        log.warning("tts-stream: configure(%s) rejected client-side "
                                    "(%s); SDK signature: %s", extras, e, sig)
                        last_err = e
                        continue
                    await ws.convert(text)
                    await ws.flush()
                    async for message in ws:
                        if isinstance(message, AudioOutput):
                            chunk = base64.b64decode(message.data.audio)
                            pcm, hdr_rate = _parse_wav(chunk)
                            if hdr_rate:
                                src_rate = hdr_rate
                            if pcm:
                                got_audio = True
                                if src_rate and src_rate != self.s.tts_sample_rate:
                                    if resampler is None or resampler.src_rate != src_rate:
                                        resampler = StreamResampler(
                                            src_rate, self.s.tts_sample_rate)
                                    pcm = resampler.process(pcm)
                                yield pcm
                        else:
                            ev = getattr(getattr(message, "data", None),
                                         "event_type", "")
                            if ev == "final":
                                break
            except Exception as e:
                last_err = e
                if got_audio:
                    raise                              # mid-stream failure: stop
                log.warning("tts-stream: attempt %s failed (%s)", extras, e)
                continue
            if got_audio:
                if not self._ws_cfg_known:
                    self._ws_cfg_known = [(extras, assumed_rate)]
                    log.info("tts-stream: working config found: %s (src rate %s)",
                             extras, src_rate)
                return
            log.warning("tts-stream: config %s accepted but produced no audio",
                        extras)
        raise ProviderError(
            "sarvam_tts",
            f"no streaming config produced audio (last error: {last_err}) — "
            "falling back to REST")
