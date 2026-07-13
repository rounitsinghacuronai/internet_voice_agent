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
    tts_pace: float = 1.0             # calm, unhurried customer-care delivery
    tts_sample_rate: int = 24000
    # ── streaming providers (sub-1.1s latency; needs `pip install sarvamai`) ──
    # STT over WebSocket: transcribes WHILE the caller speaks — final transcript
    # ~150ms after end-of-speech instead of a 350-700ms REST round trip. Falls
    # back to REST automatically on any error. NOTE: streams silence too, so
    # STT billing rises (~₹0.5/min vs ~₹0.21/min).
    stt_streaming_enabled: bool = False
    # TTS over WebSocket: first audio chunk in ~200ms instead of waiting for
    # full-sentence synthesis. Cache misses only; falls back to REST on error.
    tts_streaming_enabled: bool = False

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
    # History cap (messages, not turns — sentences commit individually). 24
    # messages ≈ 6–9 conversational turns of context: ample for a utility call,
    # and every message beyond that adds input tokens → LLM TTFT. Was 40.
    history_max_turns: int = 24

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
    qdrant_collection: str = "msedcl_kb"
    kb_dir: Path = ROOT / "knowledge" / "articles"
    index_dir: Path = ROOT / "knowledge" / "index"
    retrieval_top_k: int = 4
    rrf_k: int = 60
    low_confidence: float = 0.25

    # ── data ──
    db_path: Path = ROOT / "backend" / "msedcl.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
