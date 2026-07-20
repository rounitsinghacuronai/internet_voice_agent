"""Streaming-STT buffer reset — the first-turn latency fix.

While the opening greeting plays, the mic captures the greeting's own echo.
If that transcript/audio is left in the streaming-STT buffer, the caller's
FIRST finalize() has to wait behind it — the 2-3 s first-turn stall. ws_voice
now calls reset_buffer() the moment the greeting ends; this proves it clears
both the accumulated transcript parts and the not-yet-sent audio queue without
closing the connection.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.config import get_settings
from backend.app.providers.sarvam_stt_stream import SarvamSTTStream


def _stream() -> SarvamSTTStream:
    return SarvamSTTStream(get_settings().model_copy(), lang_getter=lambda: "mr")


def test_reset_buffer_clears_transcript_and_queue():
    st = _stream()
    # Simulate greeting echo having been transcribed + audio still queued.
    st._parts.extend(["नमस्कार", "echo", "of", "greeting"])
    st._final_evt.set()
    st._early_flushed_at = 123.0
    st._q.put_nowait(b"\x00\x00" * 480)
    st._q.put_nowait(b"\x00\x00" * 480)

    st.reset_buffer()

    assert st._parts == []                 # no greeting-echo transcript carried over
    assert st._q.qsize() == 0              # queued echo audio dropped
    assert not st._final_evt.is_set()
    assert st._early_flushed_at == 0.0


def test_reset_buffer_keeps_connection_state_untouched():
    """reset_buffer must NOT disable the stream or tear down the socket —
    the same warm connection serves the caller's first real turn."""
    st = _stream()
    st.disabled = False
    st.reset_buffer()                      # empty buffers — must be a no-op-safe call
    assert st.disabled is False


def test_reset_buffer_is_safe_when_empty():
    st = _stream()
    st.reset_buffer()                      # nothing queued — must not raise
    assert st._q.qsize() == 0
