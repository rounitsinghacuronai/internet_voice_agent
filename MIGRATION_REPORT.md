# Migration Report — MSEDCL Electricity → Syncbroad Networks Telecom & Internet

Domain transformed; platform untouched. Every latency, speech, multilingual and
robustness mechanism is byte-identical or renamed-only. 172/172 offline tests pass.

## 1. What was preserved (zero changes)

| Layer | Files | Status |
|---|---|---|
| Call state machine | `conversation/state.py` | untouched |
| Barge-in engine + shielded tool dispatch | `barge_in/`, `manager.py` core | untouched logic |
| Audio pipeline (VAD, AEC, AGC, spectral gate, endpointing, speaker verify) | `audio/*` | untouched (1 comment) |
| Language Engine (hi/mr/en detect, hysteresis, command switch, pinning) | `conversation/language.py` | logic untouched; 4 marker words swapped to telecom-era vocabulary |
| Number Recognition Engine (fragment buffer, corrections, length gates) | `conversation/numbers.py` | logic untouched; slots renamed |
| Human Speech Engine (optimizer→director→engine→prosody→formatter) | `speech/*` | logic untouched; 1 style renamed, prompts/comments only |
| Persona Engine (gender grammar, enforce_gender, voice mapping) | `persona.py` | machinery untouched; fixed lines re-authored |
| Providers (Sarvam STT/TTS streaming, Gemini, embeddings) | `providers/*` | untouched (1 comment) |
| RAG (hybrid BM25+dense, RRF, Qdrant/in-memory) | `rag/*` | untouched; category whitelist extended |
| Exotel telephony adapter | `telephony/exotel.py` | untouched |
| WS protocol + VoiceSession | `api/ws_voice.py` | untouched (1 comment) |
| Latency knobs (VAD hangover, adaptive number endpointing, TTS pre-warm, LLM pre-warm, sentence streaming, perceived-latency filler) | `config.py`, `main.py`, `manager.py` | untouched |

## 2. Domain replacements

### Tools (`tools/msedcl.py` → `tools/telecom.py`)
Old 15 electricity services → 25 telecom services:

| Electricity | Telecom |
|---|---|
| verify_consumer | verify_customer (12-digit account OR 10-digit mobile) |
| get_bill (units/kWh) | get_bill (rent+add-ons+GST), get_plan, get_recharge_history, get_usage |
| get_outage | get_network_status (tower/fiber-cut outages with ETA) |
| get_meter_details | get_broadband_status (ONT/LOS/line/sync) + run_line_diagnostics |
| — | restart_ont (remote reboot; refuses on LOS — a reboot can't fix a fiber break) |
| register/track_complaint (SR…) | register/track/escalate/close_complaint (TC…) |
| request_load_change / name_change | request_plan_change / request_sim_swap (OTP-gated) |
| — | block_sim (verify-gated, NEVER OTP — lost SIM can't receive one) |
| — | schedule_engineer_visit, record_feedback, get_plan_catalog |
| log_safety_incident | log_priority_incident (fraud/SIM-swap/stolen/harassment) |
| SOP_HOURS (MERC manual) | SLA_HOURS (38 telecom categories, TRAI-style tiers L1/L2/final) |

Hard gates preserved exactly: verify-gate on all writes, OTP-gate on plan/SIM changes,
number-format gate on verify/OTP/block tools, verified-account overwrite on writes,
priority tools never gated.

### Safety fast-path → Security fast-path (`conversation/safety.py`)
Deterministic keyword gate retained (code, not prompt). Live-wire/shock/transformer
triggers → fraud, OTP scams, SIM-swap fraud, unauthorised debits, stolen/lost phones,
threat/harassment calls (Devanagari + romanized). Same flow: fixed reviewed line first
→ shielded incident log → shielded human transfer. Routine faults never trip it.

### Memory & numbers
`consumer_no` → `account_no` (12 digits — regexes, buffer, field prompts unchanged);
`meter_no` slot removed; new derived slots `service_type`, `plan_name` filled from
verify. Ticket records: `sr_no` → `ticket_no`, `sop_hours` → `sla_hours`.

### Prompts (all 9 modules re-authored, same discipline)
Identity: Syncbroad Networks operator, telecom word-sense lock (net/balance/line/signal),
engineer-grade troubleshooting ladder. Tools: telecom decide-order, OTP matrix,
LOS rule, register-in-same-turn. Safety→Security module, cybercrime 1930 handoff.
Closing lines: सिंकब्रॉड नेटवर्क्स official closings. Style/emotion/language/memory/grounding
rules preserved verbatim where domain-free.

### Knowledge base (11 → 12 articles, same schema)
mobile_plans, recharge_payments, high_bill_diagnosis, broadband_troubleshooting
(fixed diagnostic ladder incl. red-LOS), sim_services (lost SIM/eSIM/MNP/KYC),
network_coverage (signal/call drops/data/roaming), account_services,
enterprise_services (leased line/MPLS/SLA), complaint_process (TC lifecycle, SLA
matrix, escalation levels), fraud_security, digital_services, call_scripts.

### Frontend (`frontend/index.html`)
Syncbroad Networks branding, telecom service chips (Network / Broadband / Wi-Fi / Billing &
Recharge / Plans / SIM / Account / Engineer Visits / Enterprise / Tickets), Call-Memory
panel adds Service + Plan, "Tickets This Call", telecom demo tips. Audio/WS client
code byte-identical.

### Robustness topics & Voice Director
Topics: outage/billing/… → network, internet, billing, sim, complaint_status,
new_connection (same 2-strike hysteresis). StyleName.OUTAGE → SERVICE_DOWN, same
profile parameters; sim/status topics route to DEFAULT.

### Config / infra
DB `msedcl.db` → `telecom.db` (auto-seeded on boot), Qdrant collection `msedcl_kb` →
`telecom_kb`, app title Syncbroad Networks Voice. AWS posture unchanged: same EC2 service,
S3 transcript/audio buckets, CloudWatch log groups, nginx/TLS deploy script (renamed
header only). Legacy MSEDCL-era PDFs moved to `docs/legacy/`.

### Tests & evals
All suites updated to telecom fixtures; +1 new gate test (block_sim verify-but-never-OTP).
Eval scenarios: broadband-down Hindi, language switch, verify-gate, high bill, fraud
priority, grounded knowledge. `run_eval.py` invariants updated (TC ticket regex,
verify_customer leak check).

## 3. Regression checklist

| Area | Evidence | Result |
|---|---|---|
| Offline unit suite | `pytest backend/tests -q` | **172 passed** (was 171; +1 new, all legacy equivalents kept) |
| Latency path | no changes to VAD hangover, adaptive number endpointing, sentence streaming, TTS/LLM pre-warm, filler shield, pace clamps | ✅ unchanged |
| Barge-in guarantees | eager commits, shielded dispatch, late absorber, language-update-before-generation | ✅ code untouched; tests pass |
| Multilingual | LanguageEngine tests (command switch, hysteresis, Devanagari disambiguation) | ✅ pass |
| Gender-aware grammar | persona pair tables + enforce_gender untouched; test_persona | ✅ pass |
| Number recognition | fragment merge, correction, truncation, length gates | ✅ pass (account_no=12 == old consumer_no=12) |
| Tool gates | verify-gate, OTP-gate, invalid category, number-format gate, SIM-block rule | ✅ pass |
| Speech engine | grouping 4-4-4/5-5/3-3, AI-pattern cleanup, style selection, emotion detect | ✅ pass |
| RAG | BM25 offline build over new KB; grounded answers (refund 5–7 days, LOS) | ✅ pass |
| Boot smoke | persona render, prompt compose (no MSEDCL strings), 27 tool schemas, seeded DB, LOS/outage behaviours | ✅ pass |
| Residual domain sweep | grep for msedcl/mahavitaran/electricity/meter/transformer/consumer/बिजली/वीज across repo | ✅ zero hits outside `docs/legacy/` |

## 4. Notes
- House default language remains Marathi (Maharashtra circle greeting) — matches the
  preserved `norm_lang`/`_lang_for` defaults; change `persona.py` greeting + defaults
  together if deploying another circle.
- Mock services are seeded and deterministic per account; real OSS/BSS APIs swap in
  behind identical signatures in `tools/telecom.py`.
- `.env` persona knobs unchanged (AGENT_NAME/AGENT_GENDER/AGENT_ROLE re-skin the whole
  agent with zero code edits) — now documented in `.env.example`.
