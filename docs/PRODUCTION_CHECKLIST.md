# Production Readiness Checklist

## Security & compliance
- [ ] TLS everywhere (wss://); terminate at a reverse proxy (nginx/caddy)
- [ ] Rotate SARVAM/GEMINI keys into a secret manager (not .env on disk)
- [ ] Real OTP delivery via SMS gateway; remove `mock_otp_for_demo` from send_otp
- [ ] PII handling: mask consumer/mobile numbers in logs; define transcript retention policy
- [ ] Rate limiting per IP/session on /ws/call and /chat
- [ ] Auth for /kb/reload and debug endpoints
- [ ] Call recording consent line if calls are recorded (regulatory)

## Reliability
- [ ] Replace in-memory /chat session dict with Redis (multi-worker safe)
- [ ] Run uvicorn with multiple workers behind a load balancer; sticky sessions for WS
- [ ] Health checks + auto-restart (systemd/k8s liveness on /health)
- [ ] Circuit breakers + retry with backoff on Sarvam/Gemini calls
- [ ] Graceful degradation drill: STT down → typed fallback message; TTS down → text still flows
- [ ] SQLite → Postgres when moving past mock data

## Latency & quality
- [ ] Measure per-stage latency in production logs (STT, TTFT, TTS) — targets in ARCHITECTURE.md
- [ ] Vertex AI regional endpoint (asia-south1) for Gemini to cut India RTT
- [ ] Pre-warm TTS cache with greetings/closings at startup
- [ ] Tune VAD_END_SILENCE_MS per real call data (rural callers pause longer)
- [ ] A/B test TTS speakers/pace with real consumers

## Knowledge & model governance
- [ ] OCR the scanned safety manuals (tesseract mar+hin) and ingest via structure_with_llm.py
- [ ] Human review workflow for _proposed/ articles enforced (no auto-publish)
- [ ] KB version field bumped per tariff order; stale-version alert
- [ ] Eval suite in CI: `python evaluation/run_eval.py` gates every deploy
- [ ] Weekly transcript review loop → new eval scenarios from real failures
- [ ] Hallucination audit: sample 50 calls/week, verify every number quoted traces to a tool/KB result

## Telephony (Exotel)
- [ ] Exotel Voice Streaming (WS) adapter: 8 kHz PCM/μ-law ↔ 16 kHz resample into VoiceSession
- [ ] DTMF fallback for consumer-number entry when ASR confidence is low
- [ ] Transfer_to_human wired to the real ACD queue with context summary passed as call metadata
- [ ] Load test: 100 concurrent calls (WS fan-out, Sarvam/Gemini quota headroom)

## Monitoring
- [ ] Dashboards: calls/hour, resolution rate, transfer rate, per-language distribution,
      barge-in rate, tool error rate, P50/P95 speech-to-speech latency
- [ ] Alerting on provider error spikes and verify-gate refusal spikes (fraud signal)
