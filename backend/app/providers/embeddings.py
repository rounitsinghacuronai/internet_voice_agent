"""Dense embeddings for RAG. Production: Gemini gemini-embedding-001 (multilingual,
no local model). Offline/dev fallback: deterministic hashing embedder so tests and
BM25-only retrieval run with zero keys."""
from __future__ import annotations

import hashlib
import logging
import math
import re

import httpx

from ..config import Settings
from .base import ProviderError

log = logging.getLogger(__name__)


class GeminiEmbedder:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.s = settings
        self.client = client
        self.dim = settings.embed_dim
        self.url = f"{settings.gemini_base}/embeddings"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), 64):  # batch politely
            batch = texts[i : i + 64]
            try:
                r = await self.client.post(
                    self.url,
                    headers={"Authorization": f"Bearer {self.s.gemini_api_key}"},
                    json={"model": self.s.embed_model, "input": batch,
                          "dimensions": self.dim, "encoding_format": "float"},
                    timeout=30.0,
                )
                r.raise_for_status()
            except httpx.HTTPError as e:
                raise ProviderError("gemini_embed", e) from e
            items = r.json().get("data", [])
            data = sorted(enumerate(items), key=lambda t: t[1].get("index", t[0]))
            out.extend(d["embedding"] for _, d in data)
        return out


class HashingEmbedder:
    """Char-ngram hashing → fixed dense vector. Not semantic, but deterministic and
    keyless — keeps the hybrid pipeline exercisable offline (BM25 does the real work)."""

    def __init__(self, dim: int = 768):
        self.dim = dim

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        toks = re.findall(r"\w+", text.lower())
        grams = toks + [t[i : i + 3] for t in toks for i in range(max(1, len(t) - 2))]
        for g in grams:
            h = int(hashlib.md5(g.encode()).hexdigest()[:8], 16)
            v[h % self.dim] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]


def make_embedder(settings: Settings, client: httpx.AsyncClient):
    if settings.gemini_api_key:
        return GeminiEmbedder(settings, client)
    log.warning("No GEMINI_API_KEY — using offline HashingEmbedder (BM25 carries retrieval)")
    return HashingEmbedder(settings.embed_dim)
