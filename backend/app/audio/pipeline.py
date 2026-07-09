"""Unified Audio Processing Pipeline.

Chains every audio enhancement stage in the correct order and provides a
single clean interface to VoiceSession.  The pipeline runs in two modes:

  Frame mode  (called per 32 ms VAD frame)
  ─────────────────────────────────────────
    AGC → Spectral Gate (frame) → [AEC gate during TTS]
    → return cleaned frame for VAD

  Utterance mode  (called once per complete utterance before STT)
  ────────────────────────────────────────────────────────────────
    AGC → AEC (spectral suppression) → Spectral Gate (overlap-add)
    → noisereduce (stationary noise) → return cleaned audio for STT

Speaker verification runs in utterance mode and gates whether an utterance
is forwarded to STT at all.

Signal flow
-----------
         ┌───────────────────────────────────────────────────────┐
  Mic ──►│ AGC ► Spectral Gate ► AEC ► noisereduce ► SpeakerVfy │──► STT
         └───────────────────────────────────────────────────────┘
                                ▲
             TTS PCM ──► AEC reference buffer

Config knobs (all in Settings):
  agc_enabled, agc_target_rms, agc_attack_ms, agc_release_ms
  spectral_gate_enabled, spectral_gate_over_subtraction, spectral_gate_floor
  aec_enabled, aec_suppression_db, aec_gate_db
  speaker_verify_enabled, speaker_verify_threshold, speaker_verify_rejection_ratio
  speaker_verify_enrollment_utterances
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

from ..config import Settings
from .aec import AcousticEchoCanceller
from .agc import AGC
from .denoise import Denoiser
from .speaker_verifier import SpeakerVerifier, VerificationResult
from .spectral_gate import SpectralNoiseGate

log = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Outcome of processing one utterance through the full pipeline."""
    audio: np.ndarray               # cleaned float32 audio ready for STT
    speaker: Optional[VerificationResult]  # None if verifier not enrolled yet
    suppressed: bool                # True → utterance rejected (don't call STT)
    suppression_reason: str         # "speaker_rejected" | "too_short" | ""


class AudioPipeline:
    """All-in-one audio enhancement chain for one call session.

    Instantiated once per VoiceSession. All state (noise floor, speaker model,
    AEC reference buffer) is scoped to the session and reset on disconnect.
    """

    def __init__(self, settings: Settings, session_id: str = "") -> None:
        self.s = settings
        self.session_id = session_id
        self._tts_active = False

        # ── AGC ──
        self.agc = AGC(
            target_rms=settings.agc_target_rms,
            attack_ms=settings.agc_attack_ms,
            release_ms=settings.agc_release_ms,
            max_gain=settings.agc_max_gain,
            sample_rate=settings.input_sample_rate,
        ) if settings.agc_enabled else None

        # ── Spectral noise gate ──
        self.gate = SpectralNoiseGate(
            sample_rate=settings.input_sample_rate,
            over_subtraction=settings.spectral_gate_over_subtraction,
            spectral_floor=settings.spectral_gate_floor,
        ) if settings.spectral_gate_enabled else None

        # ── AEC ──
        self.aec = AcousticEchoCanceller(
            sample_rate=settings.input_sample_rate,
            echo_suppression_db=settings.aec_suppression_db,
            gate_gain_db=settings.aec_gate_db,
        ) if settings.aec_enabled else None

        # ── noisereduce (existing denoiser, per-utterance) ──
        self.denoiser = Denoiser(settings.denoise_enabled, settings.input_sample_rate)

        # ── Speaker verifier ──
        self.verifier = SpeakerVerifier(
            sample_rate=settings.input_sample_rate,
            n_enrollment_utterances=settings.speaker_verify_enrollment_utterances,
            similarity_threshold=settings.speaker_verify_threshold,
            rejection_ratio=settings.speaker_verify_rejection_ratio,
            enabled=settings.speaker_verify_enabled,
        ) if settings.speaker_verify_enabled else None

    # ── TTS reference (called every time a TTS PCM chunk is sent) ───────────

    def feed_tts_reference(self, pcm_bytes: bytes, tts_sample_rate: int = 24000) -> None:
        """Forward TTS audio to the AEC reference buffer.

        Must be called immediately before or as each TTS PCM chunk is written
        to the WebSocket so the buffer stays synchronised.
        """
        if self.aec is not None:
            self.aec.feed_reference(pcm_bytes, sample_rate=tts_sample_rate)

    def notify_tts_started(self) -> None:
        self._tts_active = True
        if self.aec:
            self.aec.notify_tts_active(True)
        # Raise VAD threshold boost is done in ws_voice via sm.is_speaking()

    def notify_tts_ended(self) -> None:
        self._tts_active = False
        if self.aec:
            self.aec.notify_tts_active(False)

    # ── Frame-level processing (real-time, called inside Endpointer.feed) ────

    def process_frame(self, frame: np.ndarray) -> np.ndarray:
        """Apply AGC + lightweight spectral gate to one 32 ms VAD frame.

        This must be fast (<< 1 ms) since it runs on every incoming audio
        frame regardless of whether speech is detected.
        """
        audio = frame.astype(np.float32)

        if self.agc is not None:
            audio = self.agc.process_frame(audio)

        if self.gate is not None:
            # During TTS, treat all frames as non-speech for noise floor update
            # (the customer's mic likely only has echo + background right now)
            is_speech_hint = not self._tts_active
            audio = self.gate.process_frame(audio, is_speech=is_speech_hint)

        return audio

    # ── Utterance-level processing (called per complete utterance) ────────────

    def process_utterance(self, pcm16: bytes) -> PipelineResult:
        """Full pipeline: AGC → AEC → Spectral Gate → noisereduce → verify.

        Args:
            pcm16  Raw PCM16 bytes from the Endpointer UTTERANCE event.
        Returns:
            PipelineResult with cleaned audio and speaker verification outcome.
        """
        # Decode to float32
        audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0

        if len(audio) < 1600:  # < 100 ms — too short to process
            return PipelineResult(
                audio=audio, speaker=None,
                suppressed=True, suppression_reason="too_short",
            )

        # 1. AGC
        if self.agc is not None:
            audio = self.agc.process(audio)

        # 2. AEC — run only when TTS was recently active
        if self.aec is not None and self._tts_active:
            audio = self.aec.process(audio)

        # 3. Spectral noise gate (overlap-add across full utterance)
        if self.gate is not None:
            audio = self.gate.process_utterance(audio)

        # 4. noisereduce (stationary noise, existing denoiser)
        audio = self.denoiser.process(audio)

        # 5. Speaker verification
        speaker_result: Optional[VerificationResult] = None
        if self.verifier is not None:
            # Only enroll from clean speech (not during TTS — echo would corrupt model)
            allow_enroll = not self._tts_active
            speaker_result = self.verifier.verify(audio, allow_enrollment=allow_enroll)

            if speaker_result.rejected:
                log.info(
                    "session %s: utterance rejected by speaker verifier "
                    "(sim=%.3f threshold=%.2f) — background speaker or TV",
                    self.session_id, speaker_result.similarity, self.verifier.threshold,
                )
                return PipelineResult(
                    audio=audio, speaker=speaker_result,
                    suppressed=True, suppression_reason="speaker_rejected",
                )

        # Re-encode to PCM16
        return PipelineResult(
            audio=audio, speaker=speaker_result,
            suppressed=False, suppression_reason="",
        )

    def audio_to_pcm16(self, audio: np.ndarray) -> bytes:
        """Convert float32 [-1,1] back to PCM16 bytes for STT."""
        return (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes()

    # ── Reset ─────────────────────────────────────────────────────────────────

    def reset(self) -> None:
        """Reset all stateful components for a fresh call."""
        if self.agc:
            self.agc.reset()
        if self.gate:
            self.gate.reset()
        if self.aec:
            self.aec.reset()
        if self.verifier:
            self.verifier.reset()
        self._tts_active = False
