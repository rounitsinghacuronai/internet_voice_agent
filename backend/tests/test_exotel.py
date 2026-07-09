"""Exotel Voicebot transport tests — fully offline, no carrier or API keys.

Exercises the protocol translation in ExotelTransport against a fake carrier
WebSocket that speaks Exotel's JSON/base64 dialect:

  1.  Resampler: length, silence, round-trip fidelity, passthrough
  2.  accept() consumes connected+start and captures stream_sid + leg rate
  3.  Inbound media → decoded, resampled to 16 kHz, surfaced as {"bytes": ...}
  4.  stop / disconnect → surfaced as websocket.disconnect
  5.  Outbound send_bytes → base64 media events, 320-byte-multiple chunks
  6.  audio_end flushes the outbound tail (zero-padded to 320)
  7.  Barge-in (interrupted) → emits a clear event and drops the buffer
  8.  DTMF / mark / connected are consumed without surfacing to VoiceSession
  9.  Optional Basic auth accepts/rejects correctly
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend.app.config import get_settings
from backend.app.telephony.exotel import ExotelTransport, _MULTIPLE, _authorized
from backend.app.telephony.resample import resample_pcm16


# ─────────────────────────────────────────────────────────────────────────────
# Fake carrier WebSocket
# ─────────────────────────────────────────────────────────────────────────────

class FakeWS:
    """Mimics the Starlette WebSocket surface ExotelTransport uses.

    `inbox` is a list of ASGI-style receive() dicts fed to the transport.
    `sent` captures every send_text payload (parsed JSON) going back to Exotel.
    """

    def __init__(self, inbox=None, headers=None):
        self._inbox = list(inbox or [])
        self.sent: list[dict] = []
        self.accepted = False
        self.closed = False
        self.headers = headers or {}

    async def accept(self):
        self.accepted = True

    async def receive(self):
        if self._inbox:
            return self._inbox.pop(0)
        return {"type": "websocket.disconnect"}

    async def send_text(self, text):
        self.sent.append(json.loads(text))

    async def close(self, code=1000):
        self.closed = True

    # test helper
    def feed(self, msg: dict):
        self._inbox.append({"type": "websocket.receive", "text": json.dumps(msg)})


def _pcm(ms: int, rate: int, freq: float = 220.0, amp: float = 0.4) -> bytes:
    """A sine tone as PCM16 — deterministic, band-limited, good for resample checks."""
    n = int(rate * ms / 1000)
    t = np.arange(n) / rate
    return (np.sin(2 * np.pi * freq * t) * amp * 32767).astype("<i2").tobytes()


def _start_msg(rate: int, sid="stream_abc") -> dict:
    return {
        "event": "start",
        "sequence_number": 1,
        "stream_sid": sid,
        "start": {
            "stream_sid": sid,
            "call_sid": "call_xyz",
            "account_sid": "acct_1",
            "from": "09876543210",
            "to": "08012345678",
            "custom_parameters": {"queue": "premium"},
            "media_format": {"encoding": "base64", "sample_rate": str(rate)},
        },
    }


def _media_msg(pcm: bytes) -> dict:
    return {
        "event": "media",
        "sequence_number": 3,
        "stream_sid": "stream_abc",
        "media": {"chunk": 1, "timestamp": "10",
                  "payload": base64.b64encode(pcm).decode()},
    }


@pytest.fixture
def settings():
    return get_settings()


def transport(settings, inbox=None, headers=None):
    ws = FakeWS(inbox=inbox, headers=headers)
    return ExotelTransport(ws, settings), ws


# ─────────────────────────────────────────────────────────────────────────────
# 1. Resampler
# ─────────────────────────────────────────────────────────────────────────────

class TestResampler:
    def test_passthrough_when_rates_equal(self):
        pcm = _pcm(50, 16000)
        assert resample_pcm16(pcm, 16000, 16000) is pcm

    def test_empty_input(self):
        assert resample_pcm16(b"", 24000, 16000) == b""

    def test_output_length_scales_with_ratio(self):
        pcm = _pcm(100, 24000)                    # 2400 samples
        out = resample_pcm16(pcm, 24000, 16000)   # → ~1600 samples
        n_out = len(out) // 2
        assert abs(n_out - 1600) <= 1

    def test_upsample_8k_to_16k_doubles(self):
        pcm = _pcm(100, 8000)                     # 800 samples
        out = resample_pcm16(pcm, 8000, 16000)    # → ~1600 samples
        assert abs(len(out) // 2 - 1600) <= 1

    def test_odd_byte_count_is_handled(self):
        out = resample_pcm16(b"\x01\x02\x03", 8000, 16000)  # 3 bytes → drop 1
        assert len(out) % 2 == 0

    def test_roundtrip_preserves_tone_energy(self):
        """Down- then up-sample a 220 Hz tone; RMS should be broadly preserved."""
        pcm = _pcm(200, 24000, freq=220.0)
        down = resample_pcm16(pcm, 24000, 8000)
        up = resample_pcm16(down, 8000, 24000)
        a = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
        b = np.frombuffer(up, dtype="<i2").astype(np.float64)
        rms_a = np.sqrt(np.mean(a**2))
        rms_b = np.sqrt(np.mean(b**2))
        assert rms_b > rms_a * 0.6   # 220 Hz is well within the 8 kHz band


# ─────────────────────────────────────────────────────────────────────────────
# 2. accept() — connected + start handling
# ─────────────────────────────────────────────────────────────────────────────

class TestAccept:
    def test_accept_captures_stream_metadata(self, settings):
        t, ws = transport(settings, inbox=[
            {"type": "websocket.receive", "text": json.dumps({"event": "connected"})},
            {"type": "websocket.receive", "text": json.dumps(_start_msg(16000))},
        ])
        import asyncio
        asyncio.run(t.accept())
        assert ws.accepted
        assert t.stream_sid == "stream_abc"
        assert t.call_sid == "call_xyz"
        assert t.from_number == "09876543210"
        assert t.leg_rate == 16000
        assert t.custom_parameters == {"queue": "premium"}

    def test_accept_reads_leg_rate_from_start(self, settings):
        t, ws = transport(settings, inbox=[
            {"type": "websocket.receive", "text": json.dumps(_start_msg(8000))},
        ])
        import asyncio
        asyncio.run(t.accept())
        assert t.leg_rate == 8000

    def test_accept_handles_early_disconnect(self, settings):
        t, ws = transport(settings, inbox=[{"type": "websocket.disconnect"}])
        import asyncio
        asyncio.run(t.accept())
        assert t.stream_sid is None
        assert t._closed


# ─────────────────────────────────────────────────────────────────────────────
# 3-4. receive() — media, stop, dtmf, mark
# ─────────────────────────────────────────────────────────────────────────────

class TestReceive:
    def _accepted(self, settings, leg_rate, extra_inbox):
        inbox = [{"type": "websocket.receive", "text": json.dumps(_start_msg(leg_rate))}]
        inbox += extra_inbox
        t, ws = transport(settings, inbox=inbox)
        import asyncio
        asyncio.run(t.accept())
        return t, ws

    def test_media_decoded_and_resampled_to_16k(self, settings):
        pcm8k = _pcm(100, 8000)
        t, ws = self._accepted(settings, 8000, [
            {"type": "websocket.receive", "text": json.dumps(_media_msg(pcm8k))},
        ])
        import asyncio
        msg = asyncio.run(t.receive())
        assert msg["type"] == "websocket.receive"
        # 100 ms @ 8k upsampled to 16k ≈ 1600 samples = 3200 bytes
        assert abs(len(msg["bytes"]) // 2 - 1600) <= 2

    def test_media_passthrough_at_16k(self, settings):
        pcm16k = _pcm(100, 16000)
        t, ws = self._accepted(settings, 16000, [
            {"type": "websocket.receive", "text": json.dumps(_media_msg(pcm16k))},
        ])
        import asyncio
        msg = asyncio.run(t.receive())
        assert msg["bytes"] == pcm16k   # no resampling when leg == 16k

    def test_stop_surfaces_as_disconnect(self, settings):
        t, ws = self._accepted(settings, 16000, [
            {"type": "websocket.receive", "text": json.dumps(
                {"event": "stop", "stream_sid": "stream_abc",
                 "stop": {"reason": "callended"}})},
        ])
        import asyncio
        msg = asyncio.run(t.receive())
        assert msg["type"] == "websocket.disconnect"

    def test_dtmf_and_mark_are_skipped_until_media(self, settings):
        pcm = _pcm(50, 16000)
        t, ws = self._accepted(settings, 16000, [
            {"type": "websocket.receive", "text": json.dumps(
                {"event": "dtmf", "dtmf": {"digit": "5", "duration": "100"}})},
            {"type": "websocket.receive", "text": json.dumps(
                {"event": "mark", "mark": {"name": "x"}})},
            {"type": "websocket.receive", "text": json.dumps(_media_msg(pcm))},
        ])
        import asyncio
        msg = asyncio.run(t.receive())   # should skip dtmf+mark, return media
        assert msg["type"] == "websocket.receive"
        assert msg["bytes"] == pcm


# ─────────────────────────────────────────────────────────────────────────────
# 5-6. Outbound audio — chunking, base64, flush
# ─────────────────────────────────────────────────────────────────────────────

class TestOutbound:
    def _ready(self, settings, leg_rate=16000):
        t, ws = transport(settings)
        t.stream_sid = "stream_abc"
        t.leg_rate = leg_rate
        return t, ws

    def test_send_bytes_emits_multiple_of_320(self, settings):
        t, ws = self._ready(settings, 16000)
        import asyncio
        # 500 ms of TTS @ 24k → resampled to 16k, well over _MIN_SEND
        asyncio.run(t.send_bytes(_pcm(500, settings.tts_sample_rate)))
        assert ws.sent, "no media emitted"
        for m in ws.sent:
            assert m["event"] == "media"
            assert m["stream_sid"] == "stream_abc"
            raw = base64.b64decode(m["media"]["payload"])
            assert len(raw) % _MULTIPLE == 0

    def test_tail_flushed_on_audio_end(self, settings):
        t, ws = self._ready(settings, 16000)
        import asyncio
        # A short burst below _MIN_SEND stays buffered...
        asyncio.run(t.send_bytes(_pcm(30, settings.tts_sample_rate)))
        pre = len(ws.sent)
        # ...until audio_end forces a padded flush.
        asyncio.run(t.send_text(json.dumps({"type": "audio_end"})))
        assert len(ws.sent) > pre
        raw = base64.b64decode(ws.sent[-1]["media"]["payload"])
        assert len(raw) % _MULTIPLE == 0

    def test_send_bytes_noop_without_stream_sid(self, settings):
        t, ws = transport(settings)   # stream_sid still None
        import asyncio
        asyncio.run(t.send_bytes(_pcm(500, settings.tts_sample_rate)))
        assert ws.sent == []


# ─────────────────────────────────────────────────────────────────────────────
# 7. Barge-in → clear
# ─────────────────────────────────────────────────────────────────────────────

class TestBargeInClear:
    def test_interrupted_emits_clear_and_drops_buffer(self, settings):
        t, ws = transport(settings)
        t.stream_sid = "stream_abc"
        import asyncio
        # Buffer a little audio, then barge-in
        asyncio.run(t.send_bytes(_pcm(30, settings.tts_sample_rate)))
        asyncio.run(t.send_text(json.dumps({"type": "interrupted"})))
        assert any(m["event"] == "clear" for m in ws.sent)
        assert len(t._out_buf) == 0

    def test_control_telemetry_is_ignored(self, settings):
        t, ws = transport(settings)
        t.stream_sid = "stream_abc"
        import asyncio
        for typ in ("ready", "state", "user", "assistant", "audio_start", "memory"):
            asyncio.run(t.send_text(json.dumps({"type": typ, "value": "x"})))
        assert ws.sent == []   # none of these map to Exotel messages


# ─────────────────────────────────────────────────────────────────────────────
# 8. Auth
# ─────────────────────────────────────────────────────────────────────────────

class TestAuth:
    def test_open_when_no_credentials_configured(self, settings):
        settings.__dict__["exotel_api_key"] = ""
        settings.__dict__["exotel_api_token"] = ""
        ws = FakeWS(headers={})
        assert _authorized(ws, settings)

    def test_accepts_valid_basic_auth(self, settings):
        settings.__dict__["exotel_api_key"] = "KEY"
        settings.__dict__["exotel_api_token"] = "TOK"
        cred = base64.b64encode(b"KEY:TOK").decode()
        ws = FakeWS(headers={"authorization": f"Basic {cred}"})
        assert _authorized(ws, settings)

    def test_rejects_bad_basic_auth(self, settings):
        settings.__dict__["exotel_api_key"] = "KEY"
        settings.__dict__["exotel_api_token"] = "TOK"
        cred = base64.b64encode(b"KEY:WRONG").decode()
        ws = FakeWS(headers={"authorization": f"Basic {cred}"})
        assert not _authorized(ws, settings)
        # reset so other tests using the cached settings are unaffected
        settings.__dict__["exotel_api_key"] = ""
        settings.__dict__["exotel_api_token"] = ""
