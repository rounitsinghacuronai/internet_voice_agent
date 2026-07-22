"""Central configuration. Every knob is an env var; .env is loaded automatically."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]  # repo root


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    # ── persona (single source of identity — see backend/app/persona.py) ──
    # Changing ONLY these transforms the whole assistant: name, greeting,
    # first-person grammar in Marathi/Hindi, prompts, and default voice.
    agent_name: str = "Ratan"
    agent_gender: str = "male"         # male | female
    agent_role: str = "customer care executive"

    # ── server ──
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    log_json: bool = False

    # ── Sarvam ──
    sarvam_api_key: str = ""
    sarvam_base: str = "https://api.sarvam.ai"
    stt_model: str = "saarika:v2.5"
    stt_mode: str = "codemix"          # transcribe|codemix|translit|translate
    stt_language: str = "unknown"      # per-utterance auto-detect
    tts_model: str = "bulbul:v3"
    # Empty = auto-select by AGENT_GENDER (male→advait, female→ritu, see persona.py).
    # Set explicitly to pin any Bulbul v3 speaker.
    tts_speaker: str = ""
    tts_pace: float = 1.0             # natural human speaking rate (matches the reference agent). Per-style pace multiplies this (clamped by speech_pace_max); >1.0 sounds fast/robotic.
    tts_sample_rate: int = 24000
    # ── OUTPUT loudness leveling ──────────────────────────────────────────────
    # Sarvam Bulbul returns each sentence at a slightly different loudness; sent
    # out raw, the agent's volume drifts up and down between sentences — an
    # obvious "this is a bot" tell. This leveler pulls every sentence toward the
    # call's own running-average level with ONE constant gain per sentence, so
    # there is no intra-sentence pumping and no added latency. Reference-free
    # (no fixed target to mis-tune) and deliberately gentle — it only squashes
    # outliers, it does not flatten natural prosody.
    tts_loudness_normalize: bool = True
    tts_loudness_max_gain: float = 2.0     # loudest boost for a too-quiet passage (+6 dB)
    tts_loudness_min_gain: float = 0.5     # deepest cut for a too-loud passage (−6 dB)
    tts_loudness_silence_rms: float = 0.005  # below this a window is silence — gain HELD, never boosted
    tts_loudness_limiter_ceiling: float = 0.98  # soft-clip ceiling to prevent hard clipping
    # Continuous (within-sentence) leveling time constants. Slow enough to smooth
    # phrase-level "drop then rise" swings WITHOUT chasing syllables (no pumping).
    tts_loudness_attack_ms: float = 120.0  # gain DROPS this fast when a passage is too loud
    tts_loudness_release_ms: float = 350.0 # gain RISES this slowly when a passage is quiet
    tts_loudness_avg_ms: float = 2500.0    # time constant of the running-average target level
    tts_loudness_window_ms: float = 10.0   # analysis window for the level detector
    # NUMBER CAPTURE: read the digits captured so far back to the caller after
    # each pause ("72678… got it, please continue"), the way a human executive
    # notes a number. Off → silent buffering (old behaviour).
    number_capture_ack_enabled: bool = True
    # ── DTMF (keypad) capture — DUAL INPUT ──
    # Callers may key in any numeric identifier instead of speaking it. The agent
    # offers both methods whenever it asks for a number. Control keys and the
    # inter-digit timeout below are all overridable per deployment.
    dtmf_enabled: bool = True
    dtmf_submit_key: str = "#"          # caller presses this to say "done"
    dtmf_backspace_key: str = "*"       # delete last digit; twice on empty = restart
    # When keypad digits arrive but the agent hasn't explicitly named a field,
    # capture them as this identifier type (10-digit mobile is the common case).
    dtmf_default_field: str = "mobile"
    # After this much keypad silence with a VALID number buffered, treat it as
    # submitted (caller finished keying and didn't press #).
    dtmf_inter_digit_timeout_ms: int = 4000
    # ── streaming providers (sub-1.1s latency; needs `pip install sarvamai`) ──
    # STT over WebSocket: transcribes WHILE the caller speaks — final transcript
    # ~150ms after end-of-speech instead of a 350-700ms REST round trip. Falls
    # back to REST automatically on any error. NOTE: streams silence too, so
    # STT billing rises (~₹0.5/min vs ~₹0.21/min).
    stt_streaming_enabled: bool = False
    # TTS over WebSocket: first audio chunk in ~200ms instead of waiting for
    # full-sentence synthesis. Cache misses only; falls back to REST on error.
    tts_streaming_enabled: bool = False
    # EARLY FLUSH (streaming STT only): once the caller has been silent this many
    # ms (but before the full vad_end_silence hangover), send Sarvam the flush
    # signal so the final transcript is already waiting when the endpointer
    # fires UTTERANCE. If speech resumes, the extra segment simply appends —
    # nothing is lost. Cuts the post-endpoint STT wait to near zero.
    stt_early_flush_silence_ms: int = 200

    # ── Gemini ──
    gemini_api_key: str = ""
    gemini_base: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    gemini_model: str = "gemini-2.5-flash"
    gemini_reasoning_effort: str = "none"   # thinking OFF for voice latency
    gemini_ingest_model: str = "gemini-2.5-pro"
    embed_model: str = "gemini-embedding-001"
    embed_dim: int = 768

    # ── audio pipeline ──
    input_sample_rate: int = 16000
    vad_threshold: float = 0.4
    vad_min_speech_ms: int = 150       # speech shorter than this = noise blip
    # Hangover before end-of-speech fires. This silence sits at the FRONT of every
    # single response (caller stops → we wait this long → STT even starts), so it is
    # the largest fixed contributor to perceived latency. 450 ms keeps turn-taking
    # snappy for normal sentences. Raise via VAD_END_SILENCE_MS if slow speakers
    # get cut mid-sentence.
    vad_end_silence_ms: int = 450
    # ADAPTIVE endpointing: while the agent is collecting a number (consumer no,
    # mobile, OTP, meter no) callers pause between digit groups — use this longer
    # hangover there so groups aren't chopped into too many fragments, while
    # normal turns keep the fast cutoff above.
    vad_end_silence_number_ms: int = 900
    vad_max_utterance_s: int = 25
    # Utterance-level noise gate: drop an utterance before STT unless a frame reached
    # this speech probability. Real close-mic/phone speech peaks >0.8; TV, background
    # chatter and line noise usually stay below 0.5 — gating at 0.5 stops the LLM
    # reacting to background sound (and stops noise resetting the no-response
    # disconnect). Set 0.0 to disable; raise toward 0.65 in very noisy deployments.
    speech_confirm_peak_prob: float = 0.5
    # noisereduce is slow (~200-400 ms for 3-4 s audio) and blocks the event
    # loop, making the mic appear to freeze mid-call.  The SpectralNoiseGate
    # covers stationary noise, so noisereduce is disabled by default.
    # Re-enable with DENOISE_ENABLED=true only for telephony with heavy noise.
    denoise_enabled: bool = False

    # ── barge-in ──
    # Master switch. On a speaker+mic setup (no headphones) the agent's own voice
    # echoes into the mic and the VAD mistakes it for the caller interrupting —
    # so the agent cancels its own speech a split second in ("text appears, no
    # voice"). Set BARGEIN_ENABLED=false to let the agent always finish speaking.
    # Real telephony (Exotel) and headset setups have proper echo cancellation, so
    # barge-in can stay on there.
    bargein_enabled: bool = True
    bargein_cooldown_ms: int = 800     # minimum gap between consecutive barge-ins (debounce)
    bargein_false_positive_guard: bool = True  # extra VAD confirmation before firing barge-in
    # Continuous speech needed to interrupt the agent. 450 ms ≈ a real word or two,
    # so a cough, a short echo burst, or background noise won't cut the agent off,
    # but a caller who genuinely talks over the agent is still honoured quickly.
    bargein_min_speech_ms: int = 450
    # VAD threshold adjustment applied ONLY while the agent is speaking (added to
    # the base vad_threshold in ws_voice._on_audio). POSITIVE raises the bar during
    # agent speech so echo/line noise / faint sounds can't trigger a barge-in:
    # base 0.4 + 0.45 = 0.85. Only a clear, deliberate talk-over (~0.85-0.95) now
    # interrupts; low-volume voice, echo, and background murmur (≤0.8) do not.
    # Raise toward 0.5 (→0.9) if it still trips on faint sound; lower if genuine
    # interruptions stop registering.
    bargein_vad_threshold_boost: float = 0.45
    # Grace window (ms) after TTS starts during which barge-in is suppressed, to
    # reject the echo spike at playback onset. Real interruptions arrive a beat
    # later. Applies to SPEAKING only.
    bargein_tts_grace_ms: int = 500
    smart_resume_enabled: bool = True  # detect topic/intent change on interruption

    # ── AGC (Automatic Gain Control) ──
    agc_enabled: bool = True
    agc_target_rms: float = 0.08       # target RMS ≈ –22 dBFS
    agc_attack_ms: float = 10.0        # fast attack prevents clipping
    agc_release_ms: float = 300.0      # slow release avoids pumping
    agc_max_gain: float = 12.0         # upper gain cap (12× ≈ +22 dB)

    # ── Spectral Noise Gate ──
    spectral_gate_enabled: bool = True
    spectral_gate_over_subtraction: float = 2.5   # aggressiveness (1=mild, 4=heavy)
    spectral_gate_floor: float = 0.002             # minimum gain per bin

    # ── AEC (Acoustic Echo Cancellation) ──
    aec_enabled: bool = True
    aec_suppression_db: float = 18.0   # max echo attenuation in dB
    aec_gate_db: float = -6.0          # broadband gate during TTS (dB)

    # ── Speaker Verification ──
    # Disabled by default: MFCC cosine-similarity is sensitive to mic/room variation
    # and causes legitimate customer utterances to be silently rejected after the
    # 3-utterance enrollment window, making the agent appear unresponsive.
    # Enable only in controlled telephony environments (Exotel, fixed headset).
    speaker_verify_enabled: bool = False
    # NOTE: telephony sessions (Exotel) force-enable verification regardless of
    # the flag above — a phone leg is the stable environment it was built for.
    # Threshold 0.50: with the analysis band capped at the telephone band
    # (speaker_verifier.F_MAX) same-voice scores are stable, but 0.60 still
    # rejected real callers on unusual phonetics. Hard suppression additionally
    # requires TWO consecutive scores below threshold×ratio.
    speaker_verify_threshold: float = 0.50         # cosine similarity minimum
    speaker_verify_rejection_ratio: float = 0.60   # hard-reject if sim < threshold×ratio
    speaker_verify_enrollment_utterances: int = 3  # clean utterances needed to enrol

    # ── Exotel telephony (bidirectional Voicebot streaming) ──
    exotel_enabled: bool = True
    # Sample rate for the Exotel leg. Must match the `?sample-rate=` query param
    # on the Voicebot applet URL in App Bazaar. 16000 recommended (matches STT
    # input rate → no inbound resampling). Overridden per-call by the start
    # message's media_format.sample_rate when present. 8000/16000/24000.
    exotel_sample_rate: int = 16000
    # Optional HTTP Basic auth. Set BOTH to require Exotel to authenticate via a
    # `wss://<key>:<token>@host/ws/exotel` URL. Leave empty to rely on Exotel IP
    # whitelisting instead (and to keep local testing open).
    exotel_api_key: str = ""
    exotel_api_token: str = ""

    # ── AI → human escalation / call transfer ────────────────────────────────
    # When the AI decides a call needs a senior executive it calls the
    # transfer_to_senior_executive tool; the actual leg transfer is done by
    # backend/app/telephony/transfer_service.py. Everything below is optional:
    # with credentials absent the service runs in SIMULATION mode (full flow,
    # summary, WhatsApp handoff and UI stages all work — only the real Exotel
    # dial is skipped), so nothing is hardcoded and it is safe to ship now.
    transfer_enabled: bool = True             # master switch for the escalation feature
    exotel_transfer_enabled: bool = False     # perform the REAL Exotel dial (needs creds below)
    # Transfer mechanism:
    #   "flow" (recommended) — SEAMLESS: the bot speaks the hand-off line and ends
    #     its stream; the Exotel call flow's next applet (a Connect applet dialing
    #     the executive) bridges the SAME live caller — no second call, no re-dial.
    #     Requires a Connect applet after the Voicebot applet in App Bazaar.
    #   "api" — uses the Calls/connect REST API. NOTE this is click-to-call: it
    #     places NEW calls to both parties (they get re-dialled from the ExoPhone),
    #     so it is NOT a true live-call transfer. Kept for accounts without a flow.
    exotel_transfer_mode: str = "flow"        # flow | api
    exotel_sid: str = ""                      # Exotel account SID (subdomain)
    exotel_subdomain: str = "api.exotel.com"  # api.exotel.com | api.in.exotel.com …
    exotel_transfer_number: str = ""          # executive / hunt-group number to dial
    exotel_caller_id: str = ""                # your ExoPhone (CallerId) for the transfer leg
    exotel_transfer_callback_url: str = ""    # Exotel posts transfer status here (optional)
    # Credentials for the transfer API ONLY. Kept SEPARATE from exotel_api_key/
    # token (which gate the inbound /ws/exotel socket) so enabling transfers can
    # never accidentally start rejecting incoming calls. Falls back to the inbound
    # creds if these are blank.
    exotel_transfer_api_key: str = ""
    exotel_transfer_api_token: str = ""
    transfer_retry_max: int = 2               # transfer attempts before offering a callback
    transfer_retry_base_s: float = 1.5
    # Human-readable executive/queue label shown to the caller & ops (placeholder
    # until a real routing/ACD integration supplies the assigned agent name).
    transfer_executive_label: str = "Senior Executive"
    # Escalate to a human after this many failed troubleshooting attempts on the
    # same unresolved issue (the decision engine's failed-attempts trigger).
    escalation_failed_attempts: int = 3

    # ── Human Speech Generation Engine + Voice Director ──
    # Deterministic layer that turns raw LLM text into natural spoken dialogue
    # (thought-groups, pauses, pacing, number pronunciation, de-AI cleanup)
    # before it reaches Sarvam TTS. See backend/app/speech/. Reversible: set
    # SPEECH_ENABLED=false to fall back to raw sentence → TTS.
    speech_enabled: bool = True
    # Optional micro-LLM restructuring pass (higher human-feel, adds latency and
    # another failure mode). OFF by default to protect the speech-to-speech
    # budget; the deterministic path already handles the real-time turn.
    speech_llm_restructure: bool = False
    # Per-utterance pace bounds (absolute Sarvam pace). The Voice Director's
    # style pace multiplies Settings.tts_pace and is clamped into this range.
    speech_pace_min: float = 0.7
    speech_pace_max: float = 1.15

    # ── conversation ──
    max_tool_rounds: int = 4
    verify_ttl_s: int = 1800           # hard verify-gate window
    llm_timeout_s: float = 30.0
    # History cap (messages, not turns — sentences commit individually). 20
    # messages of context. 20 was too tight — because each assistant SENTENCE is
    # its own message, 20 messages is only ~4-6 real exchanges, so a paused or
    # long call (silence prompts + a few garbled STT turns) evicted the caller's
    # original request and any details already collected ("forgets context on a
    # pause"). 32 retains a full new-connection/number-capture flow across pauses
    # at a modest token cost. Was 40→24→20→32.
    history_max_turns: int = 32
    # FIRST-AUDIO FLUSH: while nothing has been spoken yet this turn, a long
    # run-on first sentence is split at a comma once the buffer reaches this
    # many chars (instead of the normal 160) so TTS starts sooner. Only the
    # first segment of a turn pays the prosody cost — later segments keep the
    # whole-sentence rule that protects intonation.
    llm_first_flush_chars: int = 80

    # ── silence / no-response handling ──
    # When the agent has finished speaking and is waiting for the caller, but the
    # caller stays silent, re-prompt them instead of sitting silent forever. This
    # also recovers from a wedged endpointer that has stopped flushing utterances.
    # Mirrors the training manual's "NO RESPONSE" flow: prompt, repeat up to N
    # times, then disconnect with the official closing line.
    silence_prompt_seconds: float = 10.0   # idle wait before a re-prompt fires
    silence_max_prompts: int = 3           # re-prompts before the call is disconnected

    # ── RAG ──
    qdrant_url: str = ""               # empty → in-memory store
    qdrant_collection: str = "telecom_kb"
    kb_dir: Path = ROOT / "knowledge" / "articles"
    index_dir: Path = ROOT / "knowledge" / "index"
    retrieval_top_k: int = 4
    rrf_k: int = 60
    low_confidence: float = 0.25

    # ── WhatsApp ops notifications (see backend/app/notification_service/) ──
    # POC: personal account via the whatsapp_bridge sidecar. Swap provider to
    # meta/twilio/exotel later — only whatsapp_sender.py changes.
    whatsapp_enabled: bool = False
    whatsapp_provider: str = "personal"       # personal | meta | twilio | exotel
    whatsapp_group_name: str = "Operations"
    whatsapp_bridge_url: str = "http://127.0.0.1:3001"
    whatsapp_retry_max: int = 3
    whatsapp_retry_base_s: float = 2.0
    whatsapp_dedup_window_min: int = 60       # same customer+category window
    whatsapp_llm_summary: bool = False        # background Gemini polish of summary
    # Meta Cloud API (WHATSAPP_PROVIDER=meta)
    whatsapp_meta_token: str = ""
    whatsapp_meta_phone_id: str = ""
    whatsapp_meta_to: str = ""                # comma-separated E.164 numbers
    # optional S3 mirror of the notification audit trail
    notify_s3_bucket: str = ""

    # ── data ──
    db_path: Path = ROOT / "backend" / "telecom.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
