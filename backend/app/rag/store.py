"""Vector stores. Production: Qdrant (hybrid dense+sparse in one collection, payload
filters). Dev/CI: in-memory store with identical API — repo runs with zero infra."""
from __future__ import annotations

import logging
import math
import re
from collections import Counter

from .schemas import Chunk

log = logging.getLogger(__name__)

_TOKEN = re.compile(r"[\wऀ-ॿ]+")


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN.findall(text)]


class BM25:
    """Self-contained BM25 (k1=1.5, b=0.75) — exact-term recall for codes like
    'A-1 form', 'Supply Failed - Phase out', '1912'."""

    def __init__(self, docs: list[list[str]]):
        self.docs = docs
        self.N = len(docs)
        self.avgdl = sum(len(d) for d in docs) / max(1, self.N)
        self.df: Counter = Counter()
        for d in docs:
            self.df.update(set(d))
        self.tfs = [Counter(d) for d in docs]

    def scores(self, query: list[str]) -> list[float]:
        out = [0.0] * self.N
        for term in query:
            df = self.df.get(term)
            if not df:
                continue
            idf = math.log((self.N - df + 0.5) / (df + 0.5) + 1)
            for i, tf in enumerate(self.tfs):
                f = tf.get(term, 0)
                if f:
                    dl = len(self.docs[i])
                    out[i] += idf * f * 2.5 / (f + 1.5 * (0.25 + 0.75 * dl / self.avgdl))
        return out


class InMemoryStore:
    def __init__(self, chunks: list[Chunk], dense_vectors: list[list[float]]):
        self.chunks = chunks
        self.dense = dense_vectors
        self.bm25 = BM25([tokenize(c.title + " " + c.text) for c in chunks])

    def dense_search(self, qv: list[float], k: int, category: str | None = None) -> list[tuple[int, float]]:
        scored = []
        for i, v in enumerate(self.dense):
            if category and self.chunks[i].category != category:
                continue
            dot = sum(a * b for a, b in zip(qv, v))
            scored.append((i, dot))
        scored.sort(key=lambda t: -t[1])
        return scored[:k]

    def sparse_search(self, query: str, k: int, category: str | None = None) -> list[tuple[int, float]]:
        scores = self.bm25.scores(tokenize(query))
        idx = [(i, s) for i, s in enumerate(scores)
               if s > 0 and (not category or self.chunks[i].category == category)]
        idx.sort(key=lambda t: -t[1])
        return idx[:k]


class QdrantStore:
    """Same API backed by Qdrant. BM25 stays local (cheap, exact); dense goes to Qdrant."""

    def __init__(self, url: str, collection: str, chunks: list[Chunk],
                 dense_vectors: list[list[float]], dim: int):
        from qdrant_client import QdrantClient
        from qdrant_client.http import models as qm

        self.qm = qm
        self.chunks = chunks
        self.collection = collection
        self.client = QdrantClient(url=url)
        self.bm25 = BM25([tokenize(c.title + " " + c.text) for c in chunks])
        self._index(dense_vectors, dim)

    def _index(self, vectors: list[list[float]], dim: int) -> None:
        qm = self.qm
        self.client.recreate_collection(
            self.collection,
            vectors_config=qm.VectorParams(size=dim, distance=qm.Distance.COSINE),
        )
        points = [
            qm.PointStruct(id=i, vector=vectors[i],
                           payload={"chunk_id": c.id, "category": c.category,
                                    "title": c.title, "source": c.source, "version": c.version})
            for i, c in enumerate(self.chunks)
        ]
        self.client.upsert(self.collection, points=points)
        log.info("Qdrant indexed %d chunks → %s", len(points), self.collection)

    def dense_search(self, qv: list[float], k: int, category: str | None = None) -> list[tuple[int, float]]:
        qm = self.qm
        flt = (qm.Filter(must=[qm.FieldCondition(key="category", match=qm.MatchValue(value=category))])
               if category else None)
        res = self.client.search(self.collection, query_vector=qv, limit=k, query_filter=flt)
        return [(int(p.id), float(p.score)) for p in res]

    def sparse_search(self, query: str, k: int, category: str | None = None) -> list[tuple[int, float]]:
        scores = self.bm25.scores(tokenize(query))
        idx = [(i, s) for i, s in enumerate(scores)
               if s > 0 and (not category or self.chunks[i].category == category)]
        idx.sort(key=lambda t: -t[1])
        return idx[:k]
