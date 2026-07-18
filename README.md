# Syncbroad Networks Voice — AI Customer Care for Telecom & Internet

Production-grade AI voice platform replacing first-level telecom customer care.
Marathi / Hindi / English + natural code-mix, live account tools (plan, bill, recharge,
usage, network, broadband diagnostics), grounded knowledge, fraud fast-path, barge-in.
Single FastAPI backend + single-file web client.

See **ARCHITECTURE.md** for design decisions, **docs/** for demo scenarios and the
production checklist, and **MIGRATION_REPORT.md** for the electricity→telecom migration.

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
| "नेट चालत नाही" | Marathi broadband flow → asks account number → verifies → outage/ONT check → diagnostics |
| "bill bahut zyada aaya hai" | Hindi high-bill diagnosis (add-ons? pro-rata? roaming?) before any complaint |
| "Can you talk in English?" | Instant, complete switch — stays English |
| "किसी ने OTP माँग के पैसे निकाल लिए!" | Protect-the-customer line first, priority incident logged, human transfer — no OTP flow |
| "Recharge fail hua, paise kat gaye?" | Grounded answer from KB (auto-reversal in 5–7 working days) |

Demo accounts (seeded): `300012345678` (Ramesh Patil, Kothrud — fiber 100 Mbps, mobile
tower outage in area), `300023456789` (Sunita Deshmukh — postpaid, bill due),
`210034567890` (Abdul Sheikh, Bhiwandi — fiber with red LOS, area fiber cut),
`330045678901` (Kavita Jadhav — prepaid), `410056789012` (Suresh Wagh — enterprise
leased line). OTPs print in the server log.

## Tests & evals

```bash
pytest backend/tests -q                     # offline unit tests (gates, language, VAD, RAG)
python evaluation/run_eval.py               # behavioural scenarios against a running server
```

## Knowledge base

`knowledge/articles/*.yaml` — structured telecom articles (plans, recharge/billing rules,
broadband troubleshooting ladder, SIM/eSIM/MNP, network, account, enterprise, fraud SOP).
To ingest new documents:

```bash
python knowledge/ingestion/extract_pdfs.py <pdf_dir>
python knowledge/ingestion/structure_with_llm.py extracted/<doc>.txt   # → _proposed/, review, move in
curl -X POST localhost:8000/kb/reload
```

## WhatsApp ops notifications (POC)

Actionable events (complaint registered, engineer visit, SIM block, fraud,
escalation) auto-post a structured ticket to an internal WhatsApp group —
async, deduplicated, retried, audited, zero added call latency. Ops dashboard
at `/ops`. Setup + trade-offs: `docs/WHATSAPP_NOTIFICATIONS.md`.

## Production

- `QDRANT_URL=http://localhost:6333` → hybrid retrieval on Qdrant (`docker run -p 6333:6333 qdrant/qdrant`)
- `LOG_JSON=1` → structured logs
- Telephony: the Exotel media-stream adapter runs against the same `VoiceSession`
  (WS protocol transport-agnostic; resample at the edge)
- Checklist: `docs/PRODUCTION_CHECKLIST.md`
