# Knowledge ingestion: TELQ6210 Facilitator Guide (2026-07-27)

## What this document actually is
The uploaded PDF (`FG_English_Telecom Technician - IoT Devices/Systems_TELQ6210, V5.0.pdf`,
128 pages) is a **government vocational-training facilitator guide** published by the Telecom
Sector Skill Council (India), NSQF Level 4, for certifying IoT installation technicians. It is
classroom lesson-plan material (Say / Ask / Activity / Do scripts, exercises, MCQ answer
keys) — not a company operations manual. It contains **zero** Syncbroad-specific content:
no pricing, plans, SLAs, billing, refunds, SIM/porting, fraud policy, or product catalogue.

Given that, the original brief's 30-module structure (billing, payments, refunds, mobile
services, SIM, porting, fraud, SLAs, etc.) can't be filled from this source without inventing
facts — which the brief itself prohibits. I extracted only what the document actually
supports and skipped the rest; see the gap list below for what's still needed from the
business.

## Why this uses the existing KB format, not a new 30-folder Markdown tree
This repo already has a production knowledge layer: `knowledge/articles/*.yaml` loaded by
`backend/app/rag/retriever.py` (`load_articles()`), hybrid BM25 + dense search, hot-reloadable
via `POST /api/kb/reload`. Categories and kinds are fixed enums in `backend/app/rag/schemas.py`.
Building a parallel Markdown taxonomy the retriever never reads would fragment the KB and
violate "the existing architecture should NOT be redesigned." So the real technical content in
this PDF was authored into **new YAML articles in the same schema**, following the documented
review workflow in `knowledge/ingestion/structure_with_llm.py`: proposed articles land in
`_proposed/` for human review before being moved into `articles/`.

## What was extracted (4 files, 22 knowledge chunks)
| File | Category | Kind | Chunks | Covers |
|---|---|---|---|---|
| `iot_devices_fundamentals.yaml` | general | article | 6 | sensor vs actuator, protocol choice (LoRaWAN/NB-IoT/Zigbee/BLE/Wi-Fi/MQTT/CoAP), microcontroller boards, edge devices, node→gateway→cloud flow, cloud platform basics |
| `cpe_field_diagnostics.yaml` | connections | article | 8 | ping/traceroute triage, speed test methodology, CPE config (VLAN/NAT/QoS/IPv6), signal loss/EMI, cable & connector repair, SLM/OTDR, firmware update/reset procedure, broadband fault taxonomy |
| `field_escalation_matrix.yaml` | complaints | decision_tree | 3 | Level 1 vs Level 2 escalation criteria, documentation discipline, customer communication during remote troubleshooting |
| `technician_field_safety.yaml` | safety | policy | 5 | RF/grounding safety, mounting/weatherproofing, crimping/soldering safety, e-waste disposal, energy-efficient install practices |

Every section cites the source PDF and its page range in the title (e.g. "Ref FG p.71-72") and
carries `source`/`version`/`language` at the article level, per the existing citation
convention (see `broadband_troubleshooting.yaml` for the pattern this follows). Hindi/Marathi
keyword synonyms were added per section, consistent with the rest of the KB, so mixed-language
voice queries still retrieve the right chunk.

**Verified**: all 4 files parse under `yaml.safe_load`, match the `category`/`kind` enums in
`schemas.py`, and load correctly through the actual `load_articles()` function imported from
`backend/app/rag/retriever.py` — 22/22 chunks loaded with no errors.

## How to promote to production
These are staged in `_proposed/` deliberately — the ingestion pipeline's own docs say KB
content reaching callers should always be human-approved first. Once you've skimmed them:

```bash
mv "knowledge/articles/_proposed/"*.yaml knowledge/articles/
git add knowledge/articles/ && git commit -m "Add TELQ6210 IoT/CPE technician knowledge"
git push origin main
# then on the server, after the usual git reset/restore-db dance:
curl -X POST https://internet.acuronai.com/api/kb/reload -H "Authorization: Bearer <admin token>"
```

The retriever picks these up on the next `/api/kb/reload` — no code changes needed.

## Missing information needed from the business (to actually cover the other 25 modules)
None of the following exist in any document provided so far, so they can't be built without
guessing:
- Current plans, pricing, and fair-use/speed-tier tables (the assistant already has some of
  this hardcoded from earlier conversations in `broadband_troubleshooting.yaml` etc. — worth
  confirming those figures are still current)
- Billing cycle rules, payment methods, refund/adjustment policy specifics
- SIM/porting SOPs, mobile plan catalogue, recharge rules
- Fraud/security incident policy, KYC verification rules
- Formal SLA commitments per fault type (some are already in the existing KB, e.g. "8 hours
  for red LOS" — worth confirming against an authoritative SLA document rather than only the
  NOC runbook)
- Router/ONT models actually deployed by Syncbroad and their specific LED/error-code meanings
  (this PDF only covers generic Arduino/ESP32/training hardware, not real field CPE models)
- A real escalation matrix with named tiers/contacts (this PDF gives Level 1/Level 2 criteria
  generically, with no Syncbroad-specific contact chain)

If any of these exist as documents, attach them and I'll run the same extraction-and-verify
process against them.
