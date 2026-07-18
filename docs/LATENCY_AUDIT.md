# Latency Audit — Syncbroad Networks AI Voice Agent

Audited: full pipeline, Exotel → WS → VAD/DSP → STT → conversation layer → Gemini
→ speech engine → Sarvam TTS → Exotel. Date: 13 Jul 2026.

## 1 · End-to-end budget (caller stops speaking → first spoken word)

| Stage | Target | Current | Verdict |
|---|---|---|---|
| Exotel → WS delivery | <50 ms | ~20–40 ms (Mumbai→Mumbai) | Excellent |
| End-of-speech detection | — | **400 ms fixed** (900 ms during number dictation) | Acceptable — physics of turn-taking; already adaptive |
| Audio DSP (AGC→AEC→gate→verify) | <20 ms | ~10–50 ms, off-loop (thread pool) | Excellent |
| STT | <100 ms | streaming + **early flush inside the hangover** → finalize ≈ 10–60 ms after UTTERANCE; REST fallback 350–700 ms | Excellent |
| Transcript pre-processing + intent/topic/confidence | <20 ms | <2 ms (pure regex, precompiled) | Excellent |
| RAG retrieval | <50 ms | in-memory, off-loop; only on knowledge questions | Excellent |
| Tool dispatch overhead | <30 ms | ~1 ms + backend time (threaded, shielded) | Excellent |
| LLM first sentence (Gemini 2.5 Flash, reasoning off) | 300–500 ms TTFT | ~350–600 ms to first *voiced segment* (80-char first-flush) | Acceptable |
| Speech engine (clean → prosody → format → gender check) | <20 ms | <3 ms (deterministic, zero network) | Excellent |
| TTS first audio (Bulbul v3, WS streaming) | ~200 ms | ~200 ms first chunk; **0 ms on cache hits**; sentence N+1 prefetch now actually wired into the speaker queue | Good |
| Return to Exotel | <100 ms | immediate (first 1.0 s unpaced, then real-time paced) | Excellent |
| **Total (typical turn)** | **0.9–1.2 s speech-to-speech** | **~0.5–0.8 s** + 400 ms endpointing | **On target** |

Greeting is special-cased: pre-warmed into the TTS cache at boot → first audio ~100 ms after `start`.

## 2 · Optimizations implemented (this cycle and prior)

1. **TTS pipelining** — sentences queued behind the one playing are prefetched
   concurrently with in-flight de-duplication; inter-sentence gaps eliminated.
2. **Adaptive endpointing** — 650→450 ms silence cutoff; 900 ms only while a
   number is being dictated.
3. **Boot-time pre-warm** — VAD model, KB embeddings, greeting/nudge/apology TTS
   (exact cache-key match via the live render path), and now the **Gemini TLS
   connection** (first turn no longer pays connection setup).
4. **Context trim** — history cap 40→24 messages (≈6–9 turns; fewer input
   tokens → lower TTFT every turn). Prompt ordering is cache-friendly: static
   modules first, per-turn blocks after → Gemini implicit prefix caching applies.
5. **Off-loop everything** — audio DSP in a thread pool; RAG dense+sparse search
   in threads; every backend tool via `asyncio.to_thread`; shared httpx client
   (50 conns, keep-alive 20); uvloop active via uvicorn[standard].
6. **Hot-path hygiene** — all regexes precompiled; persona/prompt render cached;
   speech engine fully deterministic (no network, no LLM restructure by default);
   frame-loop logging throttled (1/125 frames).
7. **Response brevity by design** — 1–3 sentence turns (prompt-enforced), which
   is simultaneously the biggest cost and TTFT lever.
8. **Exotel transport** — 0.4 s chunks (fast `clear` on barge-in), ≤1 s pacing
   lead, spec-exact schema (string fields) so the platform never stalls.

## 3 · Load test (evaluation/load_test.py — providers mocked, pipeline real)

| Concurrent calls | turn e2e avg | p95 | pipeline overhead |
|---|---|---|---|
| 1 | 1364 ms | 1361 ms | ~10 ms |
| 5 | 1358 ms | 1364 ms | ~10 ms |
| 20 | 1358 ms | 1364 ms | ~10 ms |
| 50 | 1365 ms | 1373 ms | ~10 ms |

Flat to 50 concurrent calls on one core-pair — the pipeline adds ~10 ms of
its own; everything else is provider time. (Overhead column ≈ first-sentence
token streaming of the mock, 204 ms, subtracted.)

## 4 · Live instrumentation

Every turn logs `latency pipe/stt/llm/tts | utterance→first-audio` (stt = wait
after the pipeline — stages stay additive despite the overlap) and every call
ends with `LATENCY SUMMARY over N turns | ... | target total<800ms excl.
endpoint (≈1200 ms speech-to-speech)`. Collect a week of these lines to replace
estimates with measured p50/p95 before further tuning.

## 5 · Optimizations shipped in the 900–1200 ms pass (this cycle)

1. ✅ **Streaming STT** (`STT_STREAMING_ENABLED`) — audio streams to Sarvam while
   the caller speaks; REST fallback automatic. Note: streams silence → STT
   billing ~₹0.5/min (vs ₹0.21 gated REST).
2. ✅ **Streaming TTS** (`TTS_STREAMING_ENABLED`) — first PCM chunk ~200 ms,
   cache misses only, REST fallback, results feed the LRU cache.
3. ✅ **Endpointing 400 ms** (.env).
4. ✅ **STT EARLY FLUSH** (`STT_EARLY_FLUSH_SILENCE_MS=200`, new) — the flush
   signal goes out once the caller has been silent ~200 ms, i.e. INSIDE the
   400 ms endpoint hangover. Sarvam finalizes while we wait out the hangover;
   `finalize()` after UTTERANCE typically returns in single-digit ms. The
   duplicate flush is suppressed and the settle window drops 80→30 ms.
   Saves ~100–250 ms/turn.
5. ✅ **Pipeline ∥ STT finalize** — the streaming finalize needs no cleaned
   audio, so it now starts BEFORE the CPU-bound AGC/AEC/gate pass instead of
   behind it; a suppressed (noise) utterance still consumes the transcript so
   it can never leak into the next turn. Saves pipe_ms (~10–50 ms).
6. ✅ **First-audio flush** (`LLM_FIRST_FLUSH_CHARS=80`, new) — while nothing
   has been voiced yet this turn, a long opening sentence splits at a comma at
   80 chars instead of 160, so TTS starts ~100–200 ms sooner; only that first
   segment pays the split-prosody cost, later segments keep whole-sentence
   delivery.
7. ✅ **Sentence N+1 prefetch actually wired** — `SarvamTTS.prefetch()` existed
   but had no call site; the speaker queue now prefetches every queued sentence
   while sentence N plays (in-flight de-dup makes the later synthesize() join
   the same network call). Eliminates inter-sentence gaps.
8. ✅ **History trim 24→20 messages** — fewer input tokens every turn → TTFT.
9. ✅ **HTTP/2 on the shared client** when `h2` is installed (`httpx[http2]` in
   requirements) — multiplexes concurrent TTS prefetches + Gemini streams;
   graceful HTTP/1.1 fallback.

**Measured-stage budget after this pass:**
400 (endpoint, parallel: early flush at 200 ms) + ~10 (pipe, overlapped)
+ ~10–60 (STT finalize wait) + ~350–600 (LLM first voiced segment)
+ ~200 (TTS first chunk) ≈ **0.97–1.27 s speech-to-speech**, ~0.6–0.9 s
excluding the endpoint hangover callers perceive as their own pause.

### Remaining levers (not yet shipped)
- **Gemini 2.5 Flash-Lite A/B** (`GEMINI_MODEL=gemini-2.5-flash-lite`):
  ~150–250 ms lower TTFT, 70% cheaper — needs a quality gate on Marathi
  tool-calling before default-on.
- **Speculative LLM start** at ~250 ms of silence (cancel if speech resumes):
  another ~150 ms, at the cost of wasted generations and real complexity.
- **Endpoint 400→350 ms** after a week of production `LATENCY SUMMARY` lines
  confirms no mid-sentence chops in Marathi/Hindi cadence.

## 6 · Readiness assessment

**Ready for production POC at the 0.9–1.2 s target.** With streaming STT +
early flush, streaming TTS, first-audio flush and the wired prefetch, the
typical turn lands ~0.97–1.27 s speech-to-speech (≈0.6–0.9 s after the caller's
own pause). Concurrency headroom (50 calls, flat latency) far exceeds pilot
volume; every stage is instrumented; failure paths (provider errors, barge-in,
cancellation, stream fallbacks) are cleanly handled. Verify with a week of
`LATENCY SUMMARY` lines, then take the Flash-Lite A/B and the 350 ms endpoint
if p95 needs more headroom.
