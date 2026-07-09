# Mahavitaran Voice — AI Customer Care for MSEDCL

Production-grade AI voice platform replacing first-level MSEDCL customer care.
Marathi / Hindi / English + natural code-mix, live account tools, grounded knowledge,
safety fast-path, barge-in. Single FastAPI backend + single-file web client.

See **ARCHITECTURE.md** for design decisions, **docs/** for demo scenarios and the
production checklist.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                  # paste SARVAM_API_KEY + GEMINI_API_KEY
python -m backend.app.main
```

Open http://localhost:8000 → **Start Call** → speak (any language, mix freely).

No keys? The server still boots: knowledge search runs on BM25, `/chat` works if you
point `GEMINI_API_KEY` at any OpenAI-compatible endpoint, and all unit tests pass offline.

## Try it

| Say | What happens |
|---|---|
| "लाईट गेली आहे" | Marathi outage flow → asks consumer number → verifies → outage/complaint |
| "bill bahut zyada aaya hai" | Hindi high-bill diagnosis (average? meter? usage?) before any complaint |
| "Can you talk in English?" | Instant, complete switch — stays English |
| "रोड पर तार गिर गया, चिंगारी निकल रही है!" | Safety line first, emergency logged, human transfer — no verification |
| "Online payment pe kitna discount milta hai?" | Grounded answer from KB (0.25%, cap ₹500, manual p.67) |

Demo consumer numbers (seeded): `170012345678` (Ramesh Patil, Kothrud — has area outage),
`170023456789` (Sunita Deshmukh — previous bill was average), `210034567890`
(Abdul Sheikh — stuck meter, area outage). OTPs print in the server log.

## Tests & evals

```bash
pytest backend/tests -q                     # offline unit tests (gates, language, VAD, RAG)
python evaluation/run_eval.py               # behavioural scenarios against a running server
```

## Knowledge base

`knowledge/articles/*.yaml` — structured articles authored from the CCCC Training Manual
and safety documents (source page cited per article). To ingest new documents:

```bash
python knowledge/ingestion/extract_pdfs.py <pdf_dir>
python knowledge/ingestion/structure_with_llm.py extracted/<doc>.txt   # → _proposed/, review, move in
curl -X POST localhost:8000/kb/reload
```

## Production

- `QDRANT_URL=http://localhost:6333` → hybrid retrieval on Qdrant (`docker run -p 6333:6333 qdrant/qdrant`)
- `LOG_JSON=1` → structured logs
- Telephony: implement an Exotel media-stream adapter against the same `VoiceSession`
  (WS protocol already transport-agnostic; 8 kHz resample at the edge)
- Checklist: `docs/PRODUCTION_CHECKLIST.md`
