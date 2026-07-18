"""Hybrid retriever: BM25 + dense, fused with Reciprocal Rank Fusion, lightweight
lexical-overlap rerank, confidence scoring, citations. Loads articles from YAML at
startup; hot-reloadable via /kb/reload."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import yaml

from ..config import Settings
from ..providers.base import Embedder
from .schemas import Chunk, SearchHit, SearchResult
from .store import InMemoryStore, QdrantStore, tokenize

log = logging.getLogger(__name__)


def load_articles(kb_dir: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for f in sorted(kb_dir.glob("*.yaml")):
        try:
            doc = yaml.safe_load(f.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            log.error("bad article %s: %s", f.name, e)
            continue
        art_id = doc.get("id") or f.stem
        base = dict(article_id=art_id, category=doc.get("category", "general"),
                    kind=doc.get("kind", "article"), source=doc.get("source", ""),
                    version=str(doc.get("version", "1")), language=doc.get("language", "en"))
        # semantic chunking = one chunk per authored section (a section is one concept)
        for i, sec in enumerate(doc.get("sections", [])):
            chunks.append(Chunk(
                id=f"{art_id}#{i}",
                title=sec.get("title", doc.get("title", art_id)),
                text=sec["text"].strip(),
                metadata={"keywords": sec.get("keywords", [])},
                **base,
            ))
    log.info("KB loaded: %d chunks from %s", len(chunks), kb_dir)
    return chunks


class HybridRetriever:
    def __init__(self, settings: Settings, embedder: Embedder):
        self.s = settings
        self.embedder = embedder
        self.store = None
        self.chunks: list[Chunk] = []

    async def build(self) -> None:
        self.chunks = load_articles(self.s.kb_dir)
        if not self.chunks:
            log.warning("KB empty — search_knowledge will return nothing")
            return
        texts = [c.title + "\n" + c.text for c in self.chunks]
        vectors = await self.embedder.embed(texts)
        qdrant_url = self.s.qdrant_url.strip()
        if qdrant_url and not qdrant_url.startswith("http"):
            log.warning("QDRANT_URL %r invalid — using in-memory store", qdrant_url)
            qdrant_url = ""
        if qdrant_url:
            self.store = QdrantStore(qdrant_url, self.s.qdrant_collection,
                                     self.chunks, vectors, self.embedder.dim)
        else:
            self.store = InMemoryStore(self.chunks, vectors)
            log.info("In-memory store: %d chunks (set QDRANT_URL for production)", len(self.chunks))

    async def search(self, query: str, category: str | None = None) -> dict:
        if not query.strip() or self.store is None:
            return SearchResult([], 0.0, True).to_tool_payload()
        k = self.s.retrieval_top_k
        cat = category if category in ("mobile", "broadband", "billing", "sim", "network", "account", "enterprise", "complaints", "safety", "connections", "general") else None

        qv = (await self.embedder.embed([query]))[0]
        # PERFORMANCE: store.dense_search is a BLOCKING call when backed by QdrantStore
        # (qdrant_client is synchronous — a real network round-trip inside this async
        # function would otherwise stall the whole event loop, i.e. every other
        # concurrent call's audio/WebSocket handling, for the RTT of this one lookup).
        # sparse_search (BM25, pure Python) is also offloaded for the same reason under
        # concurrent load. asyncio.to_thread keeps both stores' identical API working
        # unchanged while running them off the event loop thread.
        dense, sparse = await asyncio.gather(
            asyncio.to_thread(self.store.dense_search, qv, k * 3, cat),
            asyncio.to_thread(self.store.sparse_search, query, k * 3, cat),
        )

        # Reciprocal Rank Fusion
        fused: dict[int, float] = {}
        for rank, (i, _) in enumerate(dense):
            fused[i] = fused.get(i, 0.0) + 1.0 / (self.s.rrf_k + rank + 1)
        for rank, (i, _) in enumerate(sparse):
            fused[i] = fused.get(i, 0.0) + 1.0 / (self.s.rrf_k + rank + 1)

        ranked = sorted(fused.items(), key=lambda t: -t[1])[: k * 2]

        # rerank: lexical overlap with the query (cheap cross-check; a cross-encoder
        # slots in here for production if needed)
        q_tokens = set(tokenize(query))
        rescored: list[tuple[int, float]] = []
        for i, rrf in ranked:
            c = self.chunks[i]
            doc_tokens = set(tokenize(c.title + " " + c.text)) | {str(kw).lower() for kw in c.metadata.get("keywords", [])}
            overlap = len(q_tokens & doc_tokens) / max(1, len(q_tokens))
            rescored.append((i, 0.7 * rrf * self.s.rrf_k + 0.3 * overlap))
        rescored.sort(key=lambda t: -t[1])

        hits = [SearchHit(self.chunks[i], s) for i, s in rescored[:k]]
        confidence = hits[0].score if hits else 0.0
        result = SearchResult(hits, confidence, confidence < self.s.low_confidence)
        log.info("kb search %r → %d hits (conf %.2f)", query[:60], len(hits), confidence)
        return result.to_tool_payload()
