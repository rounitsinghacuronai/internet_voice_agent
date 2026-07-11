"""Gemini via the OpenAI-compatible endpoint (proven in the previous build).
Streaming SSE with incremental tool-call assembly; reasoning_effort=none keeps
TTFT at ~300-500 ms on gemini-2.5-flash."""
from __future__ import annotations

import json
import logging
from typing import AsyncIterator

import httpx

from ..config import Settings
from .base import LLMDelta, ProviderError

log = logging.getLogger(__name__)


class GeminiLLM:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.s = settings
        self.client = client
        self.url = f"{settings.gemini_base}/chat/completions"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.s.gemini_api_key}", "Content-Type": "application/json"}

    def _body(self, messages: list[dict], tools: list[dict] | None, temperature: float, stream: bool) -> dict:
        body: dict = {
            "model": self.s.gemini_model,
            "messages": messages,
            "temperature": temperature,
            "stream": stream,
        }
        if self.s.gemini_reasoning_effort:
            body["reasoning_effort"] = self.s.gemini_reasoning_effort
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        return body

    async def stream_chat(
        self, messages: list[dict], tools: list[dict] | None = None, temperature: float = 0.6
    ) -> AsyncIterator[LLMDelta]:
        # 0.6: enough sampling variety that ten callers with the same problem hear
        # ten differently-worded replies (a fixed low temperature made phrasing
        # converge on the same stock sentences — an instant "it's a bot" tell).
        # Facts/numbers come from tools and call memory, so this is safe.
        """Yields LLMDelta(text=...) for content; a final LLMDelta carries assembled
        tool_calls and finish reason."""
        body = self._body(messages, tools, temperature, stream=True)
        # tool-call fragments assembled by index
        calls: dict[int, dict] = {}
        finish: str | None = None
        try:
            async with self.client.stream(
                "POST", self.url, headers=self._headers(), json=body, timeout=self.s.llm_timeout_s
            ) as r:
                if r.status_code != 200:
                    detail = (await r.aread())[:500]
                    raise ProviderError("gemini", f"HTTP {r.status_code}: {detail!r}")
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    choice = (chunk.get("choices") or [{}])[0]
                    delta = choice.get("delta") or {}
                    if choice.get("finish_reason"):
                        finish = choice["finish_reason"]
                    for tc in delta.get("tool_calls") or []:
                        idx = tc.get("index", 0)
                        slot = calls.setdefault(idx, {"id": None, "name": "", "arguments": ""})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["name"] += fn["name"]
                        if fn.get("arguments"):
                            slot["arguments"] += fn["arguments"]
                    text = delta.get("content") or ""
                    if text:
                        yield LLMDelta(text=text)
        except httpx.HTTPError as e:
            raise ProviderError("gemini", e) from e

        assembled = []
        for idx in sorted(calls):
            c = calls[idx]
            try:
                args = json.loads(c["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            assembled.append(
                {"id": c["id"] or f"call_{idx}", "type": "function",
                 "function": {"name": c["name"], "arguments": json.dumps(args, ensure_ascii=False)}}
            )
        yield LLMDelta(tool_calls=assembled, finish=finish or ("tool_calls" if assembled else "stop"))

    async def complete(self, messages: list[dict], temperature: float = 0.2) -> str:
        body = self._body(messages, None, temperature, stream=False)
        try:
            r = await self.client.post(self.url, headers=self._headers(), json=body, timeout=self.s.llm_timeout_s)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise ProviderError("gemini", e) from e
        return ((r.json().get("choices") or [{}])[0].get("message") or {}).get("content") or ""
