"""Knowledge schemas. Articles are YAML files in knowledge/articles/ — structured
knowledge authored FROM the PDFs (never raw PDF text). Each article yields one or more
chunks; a chunk is the retrieval unit and carries full citation metadata."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Chunk:
    id: str
    article_id: str
    title: str
    text: str                    # what gets embedded + matched
    category: str = "general"    # billing|safety|complaints|connections|general
    kind: str = "article"        # article|sop|faq|decision_tree|policy|script
    source: str = ""             # e.g. "Training Manual p.34"
    version: str = "1"
    language: str = "en"
    metadata: dict = field(default_factory=dict)


@dataclass
class SearchHit:
    chunk: Chunk
    score: float


@dataclass
class SearchResult:
    hits: list[SearchHit]
    confidence: float
    low_confidence: bool

    def to_tool_payload(self) -> dict:
        """What search_knowledge returns to the LLM."""
        blocks = []
        for h in self.hits:
            blocks.append(f"[{h.chunk.title} — source: {h.chunk.source}]\n{h.chunk.text}")
        return {
            "context": "\n\n".join(blocks),
            "citations": [
                {"article": h.chunk.article_id, "source": h.chunk.source, "score": round(h.score, 3)}
                for h in self.hits
            ],
            "confidence": round(self.confidence, 3),
            "low_confidence": self.low_confidence,
        }
