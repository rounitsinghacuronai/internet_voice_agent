"""End-of-speech state machine. Consumes 32 ms frames + VAD verdicts, emits events:

  SPEECH_START  — caller began talking (fires barge-in if agent is speaking)
  UTTERANCE     — speech ended (silence hangover elapsed) → payload = full PCM16 bytes
  (max-length utterances are force-flushed so a rambling caller still gets a response)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

import numpy as np

from ..config import Settings
from .vad import FRAME, SileroVAD

log = logging.getLogger(__name__)


class EventType(Enum):
    SPEECH_START = "speech_start"
    UTTERANCE = "utterance"


@dataclass
class AudioEvent:
    type: EventType
    pcm16: bytes = b""
    # Peak VAD speech-probability observed across this utterance. Used as a
    # proxy for audio clarity/confidence downstream (Sarvam has no per-word
    # transcription confidence) — see conversation/robustness.py.
    peak_prob: float = 0.0


class Endpointer:
    def __init__(self, settings: Settings, vad: SileroVAD):
        self.s = settings
        self.vad = vad
        self._buf = np.empty(0, dtype=np.float32)       # unconsumed samples
        self._utt: list[np.ndarray] = []                # frames of current utterance
        self._in_speech = False
        self._speech_ms = 0.0
        self._silence_ms = 0.0
        self._started_emitted = False
        self._peak_prob = 0.0                           # max VAD prob seen this utterance
        self.frame_ms = FRAME / settings.input_sample_rate * 1000  # 32 ms

    def reset(self) -> None:
        self._buf = np.empty(0, dtype=np.float32)
        self._utt.clear()
        self._in_speech = False
        self._speech_ms = self._silence_ms = 0.0
        self._started_emitted = False
        self._peak_prob = 0.0
        self.vad.reset()

    def feed(self, pcm16: bytes, speaking: bool = False) -> list[AudioEvent]:
        """Feed raw PCM16 bytes from the WS; returns zero or more events.

        speaking=True means the agent's TTS is currently on the wire — echo, room
        reflection and background noise are far likelier to trip the VAD in that
        window, so we require the longer, more conservative `bargein_min_speech_ms`
        of continuous speech before treating it as a real interruption. When the
        agent is silent, the shorter `vad_min_speech_ms` is used so ordinary
        turn-taking stays responsive."""
        samples = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        self._buf = np.concatenate([self._buf, samples])
        min_speech_ms = self.s.bargein_min_speech_ms if speaking else self.s.vad_min_speech_ms
        events: list[AudioEvent] = []
        while len(self._buf) >= FRAME:
            frame, self._buf = self._buf[:FRAME], self._buf[FRAME:]
            events.extend(self._on_frame(frame, min_speech_ms))
        return events

    def _on_frame(self, frame: np.ndarray, min_speech_ms: float) -> list[AudioEvent]:
        events: list[AudioEvent] = []
        speech = self.vad.is_speech(frame, self.s.input_sample_rate)

        if speech:
            if not self._in_speech:
                self._in_speech = True
                self._speech_ms = 0.0
                self._peak_prob = 0.0        # fresh utterance — reset speech-quality peak
            self._speech_ms += self.frame_ms
            self._peak_prob = max(self._peak_prob, self.vad.last_prob)
            self._silence_ms = 0.0
            self._utt.append(frame)
            if not self._started_emitted and self._speech_ms >= min_speech_ms:
                self._started_emitted = True
                events.append(AudioEvent(EventType.SPEECH_START))
        else:
            if self._in_speech:
                self._silence_ms += self.frame_ms
                self._utt.append(frame)  # keep trailing silence — helps STT
                if self._silence_ms >= self.s.vad_end_silence_ms:
                    events.extend(self._flush())
            # pure silence outside speech: drop

        # force-flush pathological monologues
        if self._utt and len(self._utt) * self.frame_ms / 1000 >= self.s.vad_max_utterance_s:
            events.extend(self._flush())
        return events

    def _flush(self) -> list[AudioEvent]:
        utt, self._utt = self._utt, []
        self._in_speech = False
        self._started_emitted = False
        speech_ms, self._speech_ms = self._speech_ms, 0.0
        peak_prob, self._peak_prob = self._peak_prob, 0.0
        self._silence_ms = 0.0
        if speech_ms < self.s.vad_min_speech_ms:   # noise blip, not speech
            return []
        # Noise-only gate: unless a frame reached the speech-confirm probability,
        # treat the whole utterance as background noise and don't send it to STT.
        # Skipped in the energy-gate fallback (no real probabilities available).
        if self.vad.has_model and peak_prob < self.s.speech_confirm_peak_prob:
            log.info("utterance dropped as noise (peak prob %.2f < %.2f)",
                     peak_prob, self.s.speech_confirm_peak_prob)
            return []
        audio = np.concatenate(utt)
        pcm16 = (np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes()
        log.info("utterance flushed: %.1fs (peak prob %.2f)",
                 len(audio) / self.s.input_sample_rate, peak_prob)
        return [AudioEvent(EventType.UTTERANCE, pcm16, peak_prob=peak_prob)]
