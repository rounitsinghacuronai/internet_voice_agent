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
    # Sarvam's API does not return a per-word/utterance transcription confidence —
    # only `language_probability` (confidence in the DETECTED LANGUAGE, not the
    # text accuracy). This is that value, 0-1, or None if absent. Callers should
    # treat it as a proxy signal, not a true STT confidence score — see
    # conversation/robustness.py for how it's combined with VAD peak-probability
    # into a composite estimate.
    language_confidence: float | None = None


@dataclass
class LLMDelta:
    """One streamed chunk from the LLM: text and/or tool-call fragments."""
    text: str = ""
    tool_calls: list[dict] = field(default_factory=list)   # accumulated, complete calls
    finish: str | None = None                              # "stop" | "tool_calls" | None
    # Token usage for this request, present only on the final delta of a stream
    # (requires stream_options.include_usage — see providers/gemini_llm.py).
    # {"prompt_tokens": int, "completion_tokens": int, "total_tokens": int} or None.
    usage: dict | None = None


@runtime_checkable
class STTProvider(Protocol):
    async def transcribe(self, pcm16: bytes, sample_rate: int) -> Transcript: ...


@runtime_checkable
class TTSProvider(Protocol):
    def synthesize(self, text: str, language: str,
                   pace: float | None = None) -> AsyncIterator[bytes]:
        """Yield PCM16 chunks at the configured output sample rate. `pace` is an
        optional per-utterance pace from the Human Speech Engine; None → global."""
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
