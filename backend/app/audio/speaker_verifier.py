"""Speaker Verifier — pure-NumPy MFCC + cosine-similarity speaker model.

Purpose
-------
Distinguish the enrolled customer from background noise sources that happen to
contain speech-like signals: a television, a radio, a conversation in an
adjacent room, or another person in the same room.

The verifier does NOT perform identity authentication — it only answers:
"Does this utterance sound like the same voice we heard at the start of this
call?"  This is sufficient to filter out the most common false-barge-in cause
(TV/radio in the background triggering VAD).

Algorithm
---------
  1. MFCC extraction (pure NumPy, no librosa / scipy required):
       a. Pre-emphasis  → enhance high frequencies
       b. Hamming-windowed 25 ms frames with 10 ms hop
       c. 512-point FFT magnitude spectrum
       d. 26-band Mel filterbank (80 – 7600 Hz)
       e. Log energy
       f. DCT → 13 MFCCs
       g. Per-utterance cepstral mean subtraction (CMS)

  2. Utterance embedding: mean MFCC vector across all voiced frames.

  3. Enrollment: the verifier accumulates embeddings from the first
     `enrollment_utterances` utterances that arrive while TTS is NOT playing
     (clean speech only).  After enrollment the speaker model is the centroid
     of those embeddings.

  4. Verification: cosine similarity between the new embedding and the model.
       similarity ≥ threshold  → verified (probably the customer)
       similarity <  threshold  → unverified (possibly background speaker)
       similarity < threshold * rejection_ratio  → rejected (likely TV/radio)

Tuning
------
  threshold       Default 0.60.  Lower → more permissive (miss fewer real
                  interruptions).  Higher → stricter (fewer false positives).
  rejection_ratio Default 0.70.  A score below threshold*0.70 is "hard
                  rejection" — the pipeline skips VAD entirely for that frame.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

_EPS = 1e-10


# ─────────────────────────────────────────────────────────────────────────────
# MFCC helpers (pure NumPy)
# ─────────────────────────────────────────────────────────────────────────────

def _mel_filterbank(
    sr: int,
    n_fft: int,
    n_mels: int = 26,
    f_min: float = 80.0,
    f_max: float = 7600.0,
) -> np.ndarray:
    """Return an (n_mels, n_fft//2+1) Mel filterbank matrix."""

    def hz2mel(hz: float) -> float:
        return 2595.0 * np.log10(1.0 + hz / 700.0)

    def mel2hz(mel: float) -> float:
        return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)

    fft_freqs = np.fft.rfftfreq(n_fft, d=1.0 / sr)
    mel_min, mel_max = hz2mel(f_min), hz2mel(f_max)
    mel_points = np.linspace(mel_min, mel_max, n_mels + 2)
    hz_points = np.array([mel2hz(m) for m in mel_points])

    fb = np.zeros((n_mels, len(fft_freqs)), dtype=np.float64)
    for m in range(1, n_mels + 1):
        f_lo, f_mid, f_hi = hz_points[m - 1], hz_points[m], hz_points[m + 1]
        for k, f in enumerate(fft_freqs):
            if f_lo <= f <= f_mid:
                fb[m - 1, k] = (f - f_lo) / (f_mid - f_lo + _EPS)
            elif f_mid < f <= f_hi:
                fb[m - 1, k] = (f_hi - f) / (f_hi - f_mid + _EPS)
    return fb


# Cache the filterbank for common configs
_FB_CACHE: dict[tuple, np.ndarray] = {}


def extract_mfcc(
    audio: np.ndarray,
    sr: int = 16000,
    n_mfcc: int = 13,
    n_fft: int = 512,
    n_mels: int = 26,
    frame_ms: float = 25.0,
    hop_ms: float = 10.0,
    pre_emphasis: float = 0.97,
) -> Optional[np.ndarray]:
    """Extract MFCC matrix from float32 mono audio.

    Returns an (T, n_mfcc) array, or None if the audio is too short.
    """
    # Pre-emphasis
    audio = audio.astype(np.float64)
    audio = np.append(audio[0], audio[1:] - pre_emphasis * audio[:-1])

    frame_len = int(sr * frame_ms / 1000)
    hop_len = int(sr * hop_ms / 1000)

    if len(audio) < frame_len:
        return None

    # Frame the signal
    n_frames = 1 + (len(audio) - frame_len) // hop_len
    frames = np.lib.stride_tricks.as_strided(
        audio,
        shape=(n_frames, frame_len),
        strides=(audio.strides[0] * hop_len, audio.strides[0]),
    ).copy()

    # Hamming window
    frames *= np.hamming(frame_len)

    # Power spectrum
    spec = np.abs(np.fft.rfft(frames, n=n_fft)) ** 2  # (n_frames, n_fft//2+1)

    # Mel filterbank
    key = (sr, n_fft, n_mels)
    if key not in _FB_CACHE:
        _FB_CACHE[key] = _mel_filterbank(sr, n_fft, n_mels)
    fb = _FB_CACHE[key]

    mel_energy = np.maximum(np.dot(spec, fb.T), _EPS)  # (n_frames, n_mels)
    log_mel = np.log(mel_energy)

    # DCT via matrix multiply (no scipy needed)
    n = log_mel.shape[1]
    k = np.arange(n_mfcc)[:, None]
    dct_basis = np.cos(np.pi * k * (2 * np.arange(n) + 1) / (2 * n))  # (n_mfcc, n_mels)
    mfcc = np.dot(log_mel, dct_basis.T)  # (n_frames, n_mfcc)

    # Cepstral mean subtraction (per-utterance normalisation)
    mfcc -= mfcc.mean(axis=0, keepdims=True)

    return mfcc.astype(np.float32)


def embed(audio: np.ndarray, sr: int = 16000) -> Optional[np.ndarray]:
    """Return a (13,) mean-MFCC embedding for one utterance, or None."""
    mfcc = extract_mfcc(audio, sr=sr)
    if mfcc is None or len(mfcc) == 0:
        return None
    return mfcc.mean(axis=0)


# ─────────────────────────────────────────────────────────────────────────────
# Result dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class VerificationResult:
    """Outcome of one speaker verification call."""

    is_enrolled: bool           # True once the model has been built
    similarity: float           # cosine similarity to speaker model (0–1)
    verified: bool              # similarity ≥ threshold
    rejected: bool              # similarity < threshold * rejection_ratio (hard reject)
    utterance_no: int           # which utterance this was

    def __str__(self) -> str:
        status = "VERIFIED" if self.verified else ("REJECTED" if self.rejected else "UNVERIFIED")
        enrolled = "enrolled" if self.is_enrolled else "enrolling"
        return f"Speaker[{enrolled}] {status} sim={self.similarity:.3f} utt={self.utterance_no}"


# ─────────────────────────────────────────────────────────────────────────────
# Verifier
# ─────────────────────────────────────────────────────────────────────────────

class SpeakerVerifier:
    """Per-call speaker verification using mean-MFCC cosine similarity.

    Args:
        sample_rate              Audio sample rate in Hz.
        n_enrollment_utterances  Number of clean (non-TTS) utterances to
                                 use for building the speaker model.
        similarity_threshold     Cosine similarity required for verification.
        rejection_ratio          Score below threshold × ratio → hard reject.
        enabled                  Set to False to bypass all verification.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        n_enrollment_utterances: int = 3,
        similarity_threshold: float = 0.60,
        rejection_ratio: float = 0.70,
        enabled: bool = True,
    ) -> None:
        self.sr = sample_rate
        self.n_enroll = n_enrollment_utterances
        self.threshold = similarity_threshold
        self.rejection_ratio = rejection_ratio
        self.enabled = enabled

        self._embeddings: list[np.ndarray] = []
        self._speaker_model: Optional[np.ndarray] = None
        self._utterance_count = 0

    # ── public API ────────────────────────────────────────────────────────────

    @property
    def is_enrolled(self) -> bool:
        return self._speaker_model is not None

    def verify(
        self,
        audio: np.ndarray,
        allow_enrollment: bool = True,
    ) -> VerificationResult:
        """Verify whether audio is from the enrolled speaker.

        Args:
            audio             float32 mono audio to verify.
            allow_enrollment  If True and not yet enrolled, this utterance
                              is added to the enrollment pool.  Set to False
                              during TTS playback (echo-corrupted audio must
                              not corrupt the speaker model).
        Returns:
            VerificationResult
        """
        self._utterance_count += 1
        utt_no = self._utterance_count

        if not self.enabled:
            return VerificationResult(
                is_enrolled=True, similarity=1.0,
                verified=True, rejected=False, utterance_no=utt_no,
            )

        emb = embed(audio, sr=self.sr)
        if emb is None:
            log.debug("speaker: utterance too short to embed (utt %d)", utt_no)
            return VerificationResult(
                is_enrolled=self.is_enrolled, similarity=0.0,
                verified=False, rejected=False, utterance_no=utt_no,
            )

        # ── enrollment phase ─────────────────────────────────────────────────
        if not self.is_enrolled:
            if allow_enrollment:
                self._embeddings.append(emb)
                if len(self._embeddings) >= self.n_enroll:
                    self._speaker_model = np.mean(
                        np.stack(self._embeddings), axis=0
                    )
                    log.info(
                        "speaker: model enrolled from %d utterances",
                        len(self._embeddings),
                    )
            return VerificationResult(
                is_enrolled=False, similarity=1.0,
                verified=True, rejected=False, utterance_no=utt_no,
            )

        # ── verification phase ───────────────────────────────────────────────
        sim = _cosine_sim(emb, self._speaker_model)
        verified = sim >= self.threshold
        rejected = sim < self.threshold * self.rejection_ratio

        result = VerificationResult(
            is_enrolled=True,
            similarity=float(sim),
            verified=verified,
            rejected=rejected,
            utterance_no=utt_no,
        )
        log.info("speaker: %s", result)
        return result

    def reset(self) -> None:
        """Start a new enrollment (call at the start of each session)."""
        self._embeddings.clear()
        self._speaker_model = None
        self._utterance_count = 0
        log.info("speaker verifier reset")


# ─────────────────────────────────────────────────────────────────────────────
# Utility
# ─────────────────────────────────────────────────────────────────────────────

def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    norm = np.linalg.norm(a) * np.linalg.norm(b)
    if norm < _EPS:
        return 0.0
    return float(np.dot(a, b) / norm)
