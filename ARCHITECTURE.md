# Mahavitaran Voice — Architecture

Production AI voice customer-care platform for MSEDCL. Replaces the Dograh build with a
single, self-owned stack: one FastAPI backend, one browser client, no proxy shims.

## Why the old build died (lessons applied)

| Old weakness | Root cause | Fix here |
|---|---|---|
| 2,200-line `sarvam-proxy` monolith | Fighting Dograh's pipecat stream parser (reasoning deltas, empty content, tool-call suppression) | We own the conversation loop → no impedance mismatch, no proxy |
| `LANG_LOCK` regex hack in the proxy | Language behaviour lived in prompts only; model drifted | Deterministic **Language Engine** in code + prompt module; code decides, prompt persuades |
| Prompt duplication (workflow.json vs paste files) | Two sources of truth | Prompt modules are files in `backend/app/prompts/modules/`; composed at runtime; git is the only source |
| Invented CMP numbers (run 192) | Prompt-only guardrail | Hard **verify-gate in the tool registry** (kept from v7, now in-process) |
| No real barge-in, batch STT | Dograh node graph, HTTP tools | WS full-duplex; server-side VAD interrupts TTS mid-stream |
| 11-node graph drift vs 4-node deploy | Graph-based flow | **No node graph.** One conversation manager + deterministic orchestrator |

## Key decisions (and rejected alternatives)

**Transport: WebSocket, not server-side WebRTC.**
Browser already gives AEC + AGC + noise suppression free via `getUserMedia`
constraints — that is the "WebRTC audio processing" stage, client-side, zero server cost.
Server-side WebRTC (aiortc) adds ICE/STUN/TURN/DTLS/SRTP, hard debugging, and a
poorly-maintained Python stack — for ~20–30 ms RTT gain that Sarvam/Gemini latency dwarfs.
WS binary PCM frames are trivially debuggable, work on every browser, and **Exotel's
voice-streaming API is also a WebSocket** — the same gateway serves telephony later with a
thin codec adapter (8 kHz μ-law ↔ 16 kHz PCM).

**LLM: `gemini-2.5-flash` with thinking disabled** (`reasoning_effort: none`) for live turns —
TTFT ~300–500 ms, proven in the previous build. `gemini-2.5-pro` for offline knowledge
structuring where quality beats latency. Model strings are env-config; swap when Google
ships newer. Accessed via the OpenAI-compatible endpoint: battle-tested in your old proxy,
keeps provider interfaces uniform, streaming + function calling both work.

**Vector DB: Qdrant.** Native hybrid (dense + sparse in one collection), payload filtering
for metadata (doc version, language, category), runs as a single Docker container or
embedded. The code ships with an **in-memory store fallback** so the repo runs with zero
infra; flip `QDRANT_URL` to go production.

**Embeddings: Gemini `gemini-embedding-001` (dense) + BM25 (sparse).** No 2 GB local model
download; multilingual (hi/mr/en) out of the box; BM25 gives exact-term recall for codes
like "A-1 form", "Supply Failed - Phase out". Offline hash-embedder fallback keeps tests
runnable without keys.

**Tools: in-process services, not a second HTTP server.** The old mock backend was a
separate FastAPI app because Dograh needed HTTP. We don't. Same 14 tools + `search_knowledge`,
same SQLite seed data, same hard verify-gate — now a `ToolRegistry` the orchestrator calls
directly. Real MSEDCL APIs later = swap the service implementation behind the same schema.

**STT: Sarvam Saaras, utterance-scoped.** Endpointing is ours (Silero VAD state machine),
so STT sees one clean utterance per request — `mode=codemix`, `language=unknown`
per-utterance auto-detect (both proven settings). Provider interface exposes
`transcribe(utterance)`; a true streaming implementation can slot in behind it when
Sarvam's streaming WS is enabled for your account.

## Runtime flow

```
Browser mic ──getUserMedia(AEC,AGC,NS)──► AudioWorklet ──PCM16 16k frames──► WS /ws/call
                                                                              │
                              ┌───────────────────────────────────────────────┘
                              ▼
                    VoiceSession (per call)
                    ├─ AudioPipeline: denoise → Silero VAD → endpointing (speech/silence FSM)
                    │     └─ barge-in: speech while TTS playing → cancel speak task
                    ├─ Sarvam STT (utterance → text + language hint)
                    ├─ ConversationManager
                    │   ├─ LanguageEngine  (deterministic detect/persist/switch)
                    │   ├─ SafetyGate      (hazard keywords → emergency path, skips everything)
                    │   ├─ CallMemory      (slots: consumer_no, mobile, name, lang, location,
                    │   │                   complaints, verified, open_issues)
                    │   ├─ PromptComposer  (modules + memory block + lang directive)
                    │   ├─ Gemini loop     (stream; tool_calls → Orchestrator, max 4 rounds)
                    │   └─ Orchestrator    (verify-gate, OTP-gate, RAG vs live-tool routing)
                    ├─ SentenceStreamer (first sentence → Speech Engine immediately)
                    ├─ Human Speech Engine + Voice Director  (per sentence, deterministic)
                    │     ResponseOptimizer → VoiceDirector (style per turn) → HumanSpeechEngine
                    │     → ProsodyPlanner → SarvamFormatter  ⇒ spoken text + per-utterance pace
                    └─ Sarvam TTS(text, pace) ──PCM16 24k──► WS ──► browser playback queue
```

### Human Speech Generation Engine (the "final quality layer")

Between the LLM's streamed sentences and Sarvam sits a deterministic layer that
turns *AI reading text* into *a human speaking*. The **Voice Director** assigns one
style profile per turn (greeting / verification / outage / billing /
complaint-registered / emergency / closing, plus caller-emotion adaptation); the
**Human Speech Engine** then adds an active-listening lead-in and genuine
hesitation (only when a lookup actually ran), groups long lines for breathing,
plans pauses by meaning, formats long numbers digit-by-digit, and emits one Sarvam
pace per utterance (slower for numbers, steadier for angry callers). Zero added
latency; `SPEECH_ENABLED=false` reverts to raw sentence → TTS. Full write-up in
`docs/HUMAN_SPEECH_ENGINE.md`.

## Latency budget (first audible response)

| Stage | Target |
|---|---|
| End-of-speech detect (VAD hangover) | 500–700 ms (tunable) |
| Sarvam STT (utterance) | 300–500 ms |
| Gemini TTFT (thinking off) | 300–500 ms |
| First sentence complete + TTS first chunk | 300–400 ms |
| **Total** | **~1.4–2.1 s speech-to-speech**, sub-1 s after end-of-speech for short replies |

## Repository

```
backend/app/
  main.py            FastAPI entry, DI wiring, lifespan
  config.py          env settings (pydantic-settings)
  logging_setup.py   structured JSON logs
  api/ws_voice.py    WS protocol + VoiceSession
  api/rest.py        health, KB debug search, session inspect
  audio/             vad.py (Silero), denoise.py, endpointing.py (FSM)
  providers/         base.py (interfaces), sarvam_stt.py, sarvam_tts.py,
                     gemini_llm.py, embeddings.py
  conversation/      manager.py, memory.py, language.py, safety.py, numbers.py,
                     robustness.py
  speech/            Human Speech Engine + Voice Director (deterministic):
                     pipeline.py (facade), director.py, profiles.py, engine.py,
                     prosody.py, formatter.py, optimizer.py, numbers_speech.py,
                     lexicon.py, variation.py, plan.py, evaluate.py
  prompts/           loader.py + modules/*.md (identity, style, language, tools,
                     memory, safety, escalation, closing, grounding)
  tools/             registry.py (schemas+gates), msedcl.py (14 services), seed.py
  rag/               schemas.py, store.py (Qdrant + memory), retriever.py (hybrid+rerank)
knowledge/
  articles/*.yaml    structured knowledge (authored from the manuals — no raw PDFs)
  ingestion/         extract_pdfs.py, build_index.py
frontend/index.html  single-file call-center UI (worklet mic, WS, barge-in)
evaluation/          run_eval.py + scenarios/*.yaml, speech_naturalness.py (before/after)
docs/                DEMO_SCENARIOS.md, PRODUCTION_CHECKLIST.md, HUMAN_SPEECH_ENGINE.md
```

## WS protocol (`/ws/call`)

Client→Server: `{"type":"start","sample_rate":16000}` · binary PCM16 frames ·
`{"type":"text","text":...}` (typed input, testing) · `{"type":"end"}`
Server→Client: `{"type":"ready"}` · `{"type":"state","value":"listening|thinking|speaking"}` ·
`{"type":"partial_user"}` `{"type":"user","text","lang"}` · `{"type":"assistant","text"}` ·
`{"type":"audio_start"}` binary PCM16 24k `{"type":"audio_end"}` ·
`{"type":"interrupted"}` · `{"type":"memory",...}` · `{"type":"ended"}`
