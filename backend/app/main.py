"""FastAPI entry — dependency wiring in one place (poor-man's DI container: explicit,
inspectable, trivially replaced in tests)."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .api import rest, ws_voice
from .telephony import exotel
from .audio.vad import load_vad_session
from .config import Settings, get_settings
from .conversation.manager import GREETING
from .logging_setup import setup_logging
from .providers.embeddings import make_embedder
from .providers.gemini_llm import GeminiLLM
from .providers.sarvam_stt import SarvamSTT
from .providers.sarvam_tts import SarvamTTS
from .rag.retriever import HybridRetriever
from .tools.msedcl import MsedclServices
from .tools.registry import ToolRegistry

log = logging.getLogger(__name__)
FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "index.html"


@dataclass
class Deps:
    settings: Settings
    stt: SarvamSTT
    tts: SarvamTTS
    llm: GeminiLLM
    tools: ToolRegistry
    retriever: HybridRetriever
    http: httpx.AsyncClient
    # Shared, loaded-once Silero VAD ONNX session — every VoiceSession reuses
    # this instead of each call reloading the model from scratch (see
    # audio/vad.py). None if onnxruntime/model unavailable (energy-gate
    # fallback still works per-session in that case).
    vad_session: object | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_json)
    http = httpx.AsyncClient(limits=httpx.Limits(max_connections=50, max_keepalive_connections=20))

    services = MsedclServices(settings.db_path)
    embedder = make_embedder(settings, http)
    retriever = HybridRetriever(settings, embedder)
    tools = ToolRegistry(settings, services, retriever=retriever)
    tts = SarvamTTS(settings, http)

    # PERFORMANCE: load the VAD model once here instead of per-call (was adding
    # latency to every single call, even before the WebSocket finished accepting
    # — see audio/vad.py). ~1-2s one-time cost at boot, zero per-call cost after.
    vad_session = load_vad_session()

    app.state.deps = Deps(
        settings=settings,
        stt=SarvamSTT(settings, http),
        tts=tts,
        llm=GeminiLLM(settings, http),
        tools=tools,
        retriever=retriever,
        http=http,
        vad_session=vad_session,
    )
    try:
        await retriever.build()
    except Exception as e:
        log.error("KB build failed (%s) — continuing without knowledge", e)

    # PERFORMANCE: pre-warm fixed lines (greeting, silence nudge, apology) so no
    # call ever pays a live Sarvam round-trip for them. Rendered through the SAME
    # SpeechDirector path a live call uses, so the cache key (exact text + pace)
    # is guaranteed to match at runtime.
    try:
        from .conversation.manager import _APOLOGY, _SILENCE_NUDGE
        from .speech.pipeline import SpeechDirector
        from .speech.plan import StyleName

        fixed_lines = [(GREETING, "mr", StyleName.GREETING),
                       (_SILENCE_NUDGE["mr"], "mr", StyleName.DEFAULT),
                       (_APOLOGY["mr"], "mr", StyleName.DEFAULT)]
        if settings.speech_enabled:
            director = SpeechDirector(settings)
            to_warm = [(p.text, p.language, p.pace) for p in
                       (director.render_fixed(t, lang, st) for t, lang, st in fixed_lines)]
        else:
            to_warm = [(t, lang, None) for t, lang, _ in fixed_lines]
        for text, lang, pace in to_warm:
            async for _ in tts.synthesize(text, lang, pace):
                pass
        log.info("Fixed-line TTS pre-warmed (%d lines)", len(to_warm))
    except Exception as e:
        log.warning("TTS pre-warm failed (%s) — first call will synthesize live", e)

    log.info("Mahavitaran Voice up — model=%s stt=%s tts=%s kb_chunks=%d exotel=%s@%dHz",
             settings.gemini_model, settings.stt_model, settings.tts_model,
             len(retriever.chunks), settings.exotel_enabled, settings.exotel_sample_rate)
    yield
    await http.aclose()


app = FastAPI(title="Mahavitaran Voice", version="1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(ws_voice.router)
app.include_router(exotel.router)
app.include_router(rest.router)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(FRONTEND)


def run() -> None:
    import uvicorn

    s = get_settings()
    uvicorn.run("backend.app.main:app", host=s.host, port=s.port, log_level=s.log_level.lower())


if __name__ == "__main__":
    run()
