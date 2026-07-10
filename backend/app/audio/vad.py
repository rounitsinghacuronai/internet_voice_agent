"""Silero VAD wrapper (ONNX, CPU, ~1 ms per 32 ms frame).

Silero v5 detail that silently breaks naive wrappers: the ONNX model expects each
512-sample frame PREPENDED with the last 64 samples of the previous frame (576 total)
— the official python wrapper does this internally. Without the context the model
returns near-zero probabilities and VAD never fires. We replicate the context here and
auto-fall back to bare-512 input for older model files.

Falls back to an energy gate if onnxruntime/model unavailable so the server always boots.

PERFORMANCE: loading the ONNX model (reading the file + graph optimization inside
onnxruntime) is the expensive, one-time part — per-call state (the LSTM-style
`_state`, the v5 context window `_ctx`) is cheap and MUST stay per-session (concurrent
calls would corrupt each other's state if shared). `SileroVAD.__init__` used to reload
the model fresh on every single call — confirmed in production logs ("Silero VAD
loaded" appearing before the WebSocket even finished accepting), adding pure latency
per call for zero benefit. `load_vad_session()` loads the model ONCE at process
startup; each `SileroVAD` instance (one per call) accepts that shared session and only
owns its own small per-call buffers. onnxruntime's InferenceSession.run() is safe to
reuse this way — the graph holds no per-call state, only what's passed via the
`state`/`sr` inputs each call, which is already tracked externally per instance.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

FRAME = 512   # Silero expects 512 samples @16k (32 ms)
CTX = 64      # v5 context samples


def load_vad_session() -> Any | None:
    """Load the Silero VAD ONNX model once. Call at process startup and share
    the result across all VoiceSession/SileroVAD instances. Returns None (and
    logs a warning) if onnxruntime or the model file is unavailable — callers
    fall back to the energy-gate heuristic in that case."""
    try:
        import onnxruntime as ort  # type: ignore

        model = Path(__file__).with_name("silero_vad.onnx")
        if not model.exists():
            import urllib.request

            url = ("https://github.com/snakers4/silero-vad/raw/master/"
                   "src/silero_vad/data/silero_vad.onnx")
            log.info("Downloading Silero VAD model…")
            urllib.request.urlretrieve(url, model)  # noqa: S310
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        session = ort.InferenceSession(str(model), opts,
                                       providers=["CPUExecutionProvider"])
        log.info("Silero VAD model loaded (shared across calls)")
        return session
    except Exception as e:  # pragma: no cover
        log.warning("Silero unavailable (%s) — energy-gate fallback", e)
        return None


class SileroVAD:
    def __init__(self, threshold: float = 0.5, ort_session: Any | None = None):
        """`ort_session`: a pre-loaded model from load_vad_session(), shared
        across every call in the process. If omitted, this instance loads its
        own (kept for standalone/test usage) — production no longer takes
        that path, since main.py loads the model once and passes it in."""
        self.threshold = threshold
        self.last_prob = 0.0          # most recent speech probability (diagnostics)
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._ctx = np.zeros(CTX, dtype=np.float32)
        self._use_ctx = True          # v5: 576-sample input; auto-detected on first run
        self._probe_done = False
        self._prob_log = 0
        self._session = ort_session if ort_session is not None else load_vad_session()

    @property
    def has_model(self) -> bool:
        """True when the real Silero model is loaded (last_prob is a genuine speech
        probability). False in the energy-gate fallback, where last_prob is only
        RMS energy and probability-based gates must be skipped."""
        return self._session is not None

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._ctx = np.zeros(CTX, dtype=np.float32)

    def _run(self, samples: np.ndarray, sample_rate: int) -> float:
        out, self._state = self._session.run(
            None,
            {"input": samples.reshape(1, -1).astype(np.float32),
             "state": self._state, "sr": np.array(sample_rate, dtype=np.int64)},
        )[:2]
        return float(out[0][0])

    def is_speech(self, frame: np.ndarray, sample_rate: int = 16000) -> bool:
        """frame: float32 mono in [-1,1], length FRAME."""
        if self._session is None:
            self.last_prob = float(np.sqrt(np.mean(frame ** 2)))
            return self.last_prob > 0.012

        if not self._probe_done:
            self._probe_done = True
            try:
                self._run(np.concatenate([self._ctx, frame]), sample_rate)
                self._use_ctx = True
                log.info("Silero VAD: v5 context mode (576-sample input)")
            except Exception:
                self._use_ctx = False
                self.reset()
                log.info("Silero VAD: legacy mode (512-sample input)")

        try:
            if self._use_ctx:
                prob = self._run(np.concatenate([self._ctx, frame]), sample_rate)
                self._ctx = frame[-CTX:]
            else:
                prob = self._run(frame, sample_rate)
        except Exception as e:
            log.warning("VAD inference failed (%s) — energy fallback", e)
            self._session = None
            self.last_prob = float(np.sqrt(np.mean(frame ** 2)))
            return self.last_prob > 0.012

        self.last_prob = prob
        # diagnostic: log a few loud-frame probabilities after start
        if self._prob_log < 10 and float(np.abs(frame).max()) > 0.02:
            self._prob_log += 1
            log.info("VAD prob on loud frame: %.2f (threshold %.2f)", prob, self.threshold)
        return prob >= self.threshold
