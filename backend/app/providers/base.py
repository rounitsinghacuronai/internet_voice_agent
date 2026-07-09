"""Provider interfaces. Everything above this layer is provider-agnostic —
swap Sarvam/Gemini for anything that satisfies these Protocols (incl. offline stubs)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol, runtime_checkable


@dataclass
class Transcript:
    text: str
    language: str = "unknown"      # BCP-47-ish hint from STT, e.g. "hi-IN"
    raw: dict = field(default_factory=dict)


@dataclass
class LLMDelta:
    """One streamed chunk from the LLM: text and/or tool-call fragments."""
    text: str = ""
    tool_calls: list[dict] = field(default_factory=list)   # accumulated, complete calls
    finish: str | None = None                              # "stop" | "tool_calls" | None


@runtime_checkable
class STTProvider(Protocol):
    async def transcribe(self, pcm16: bytes, sample_rate: int) -> Transcript: ...


@runtime_checkable
class TTSProvider(Protocol):
    def synthesize(self, text: str, language: str) -> AsyncIterator[bytes]:
        """Yield PCM16 chunks at the configured output sample rate."""
        ...


@runtime_checkable
class LLMProvider(Protocol):
    def stream_chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.4,
    ) -> AsyncIterator[LLMDelta]: ...

    async def complete(self, messages: list[dict], temperature: float = 0.2) -> str: ...


@runtime_checkable
class Embedder(Protocol):
    dim: int
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class ProviderError(RuntimeError):
    """Raised on non-retryable provider failure; the manager converts it to a spoken apology."""
    def __init__(self, provider: str, detail: Any):
        self.provider = provider
        super().__init__(f"{provider}: {detail}")
