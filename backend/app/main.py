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
from .config import Settings, get_settings
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_json)
    http = httpx.AsyncClient(limits=httpx.Limits(max_connections=50, max_keepalive_connections=20))

    services = MsedclServices(settings.db_path)
    embedder = make_embedder(settings, http)
    retriever = HybridRetriever(settings, embedder)
    tools = ToolRegistry(settings, services, retriever=retriever)

    app.state.deps = Deps(
        settings=settings,
        stt=SarvamSTT(settings, http),
        tts=SarvamTTS(settings, http),
        llm=GeminiLLM(settings, http),
        tools=tools,
        retriever=retriever,
        http=http,
    )
    try:
        await retriever.build()
    except Exception as e:
        log.error("KB build failed (%s) — continuing without knowledge", e)

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
