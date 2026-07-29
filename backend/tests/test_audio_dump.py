"""Tests for the outbound-audio diagnostic capture (telephony/audio_dump.py).

This is a debug tool wired into the live audio path, so the properties that
matter are: it produces a WAV a player can actually open, it captures the
samples byte-for-byte (a lossy capture would send us chasing ghosts), it is
completely inert when switched off, and it can never raise into the call.
"""
from __future__ import annotations

import struct
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.config import Settings
from backend.app.telephony.audio_dump import WavDump, open_dump


PCM = bytes(range(256)) * 8       # 2048 bytes


def test_disabled_by_default():
    assert Settings().debug_audio_dump_dir == ""
    assert open_dump("", "x.wav", 16000) is None


def test_produces_a_wav_the_stdlib_can_read(tmp_path):
    d = open_dump(str(tmp_path), "leg.wav", 16000)
    assert d is not None
    d.write(PCM)
    d.write(PCM)
    d.close()

    with wave.open(str(tmp_path / "leg.wav"), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 16000
        assert w.readframes(w.getnframes()) == PCM + PCM


def test_capture_is_byte_exact_at_either_rate(tmp_path):
    for rate in (8000, 16000, 24000):
        d = open_dump(str(tmp_path), f"r{rate}.wav", rate)
        d.write(PCM)
        d.close()
        with wave.open(str(tmp_path / f"r{rate}.wav"), "rb") as w:
            assert w.getframerate() == rate
            assert w.readframes(w.getnframes()) == PCM


def test_header_declares_the_real_length(tmp_path):
    d = open_dump(str(tmp_path), "len.wav", 16000)
    d.write(PCM)
    d.close()
    raw = (tmp_path / "len.wav").read_bytes()
    assert raw[:4] == b"RIFF" and raw[8:12] == b"WAVE"
    assert struct.unpack("<I", raw[4:8])[0] == 36 + len(PCM)
    assert struct.unpack("<I", raw[40:44])[0] == len(PCM)


def test_writes_after_close_are_ignored(tmp_path):
    d = open_dump(str(tmp_path), "closed.wav", 16000)
    d.write(PCM)
    d.close()
    d.write(PCM)          # must not raise, must not corrupt
    d.close()             # double close must not raise
    with wave.open(str(tmp_path / "closed.wav"), "rb") as w:
        assert w.readframes(w.getnframes()) == PCM


def test_unwritable_directory_degrades_quietly(tmp_path):
    """A diagnostic must never be able to break a live call."""
    blocker = tmp_path / "afile"
    blocker.write_text("not a directory")
    d = WavDump(blocker / "nested" / "x.wav", 16000)
    d.write(PCM)          # no exception
    d.close()             # no exception


def test_empty_writes_are_harmless(tmp_path):
    d = open_dump(str(tmp_path), "empty.wav", 16000)
    d.write(b"")
    d.close()
    with wave.open(str(tmp_path / "empty.wav"), "rb") as w:
        assert w.getnframes() == 0
