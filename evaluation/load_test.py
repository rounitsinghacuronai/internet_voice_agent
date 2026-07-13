"""Concurrency benchmark for the conversation pipeline.

Measures the SERVER-SIDE overhead the pipeline adds around the external
providers (STT/LLM/TTS are mocked with realistic synthetic delays), at 1, 5,
20 and 50 simultaneous calls. This isolates what WE control: event-loop
contention, speech-engine CPU, tool dispatch, sentence streaming, and shows
whether added concurrency degrades per-call latency.

Run:  python3 evaluation/load_test.py
"""
from __future__ import annotations

import asyncio
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.config import Settings
from backend.app.conversation.manager import ConversationManager
from backend.app.providers.base import LLMDelta

# Synthetic provider delays (mid-range of production observations)
STT_MS, LLM_TTFT_MS, LLM_TOKEN_MS, TTS_MS = 400, 400, 12, 350

REPLY_TOKENS = ("जी, तुमचं बिल दोन हजार तीनशे चाळीस रुपये आहे, "
                "पंधरा तारखेपर्यंत भरायचं आहे.").split(" ")


class MockLLM:
    async def stream_chat(self, messages, tools=None, temperature=0.6):
        await asyncio.sleep(LLM_TTFT_MS / 1000)
        for tok in REPLY_TOKENS:
            await asyncio.sleep(LLM_TOKEN_MS / 1000)
            yield LLMDelta(text=tok + " ")
        yield LLMDelta(tool_calls=[], finish="stop")

    async def complete(self, messages, temperature=0.2):
        await asyncio.sleep(LLM_TTFT_MS / 1000)
        return "ok"


class MockTools:
    schemas: list = []
    async def dispatch(self, name, args, memory):
        await asyncio.sleep(0.05)
        return {"ok": True}


async def one_call(settings: Settings, turns: int = 4) -> list[float]:
    """Simulate one call: STT delay → manager turn → TTS delay per sentence.
    Returns per-turn end-to-end ms (caller stops → first TTS audio ready)."""
    mgr = ConversationManager(settings, MockLLM(), MockTools(), "bench")
    lat = []
    for t in range(turns):
        t0 = time.perf_counter()
        await asyncio.sleep(STT_MS / 1000)                    # STT
        first_audio = None
        async for chunk in mgr.run_turn("माझं बिल किती आहे", "mr-IN"):
            if chunk.kind == "sentence" and first_audio is None:
                await asyncio.sleep(TTS_MS / 1000)            # TTS first chunk
                first_audio = (time.perf_counter() - t0) * 1000
        lat.append(first_audio or 0.0)
    return lat


async def bench(concurrency: int) -> None:
    settings = Settings(speech_enabled=True, tts_pace=1.0)
    t0 = time.perf_counter()
    results = await asyncio.gather(*(one_call(settings) for _ in range(concurrency)))
    wall = time.perf_counter() - t0
    per_turn = [ms for call in results for ms in call]
    ideal = STT_MS + LLM_TTFT_MS + TTS_MS  # + first-sentence token time
    overhead = statistics.mean(per_turn) - ideal
    print(f"{concurrency:>3} calls | turn e2e avg {statistics.mean(per_turn):6.0f} ms "
          f"| p95 {sorted(per_turn)[int(len(per_turn)*0.95)-1]:6.0f} ms "
          f"| pipeline overhead vs ideal {overhead:+5.0f} ms | wall {wall:4.1f}s")


async def main() -> None:
    print(f"ideal floor = STT {STT_MS} + LLM-TTFT {LLM_TTFT_MS} + TTS {TTS_MS} "
          f"≈ {STT_MS+LLM_TTFT_MS+TTS_MS} ms + first-sentence tokens")
    for c in (1, 5, 20, 50):
        await bench(c)


if __name__ == "__main__":
    asyncio.run(main())
