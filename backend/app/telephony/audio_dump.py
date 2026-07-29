"""Diagnostic WAV capture for the OUTBOUND audio path.

WHY THIS EXISTS
---------------
A caller has repeatedly reported "voice cracking" that survived four separate,
independently-real fixes found by reading code and logs (an output compressor,
a mid-sentence TTS split, a text-mangling purity rewrite, and a discarded first
streamed WAV chunk). Server logs cannot settle the question, because nothing in
them describes the SHAPE of the samples — the "TTS fell behind real-time"
warning only catches timing stalls, and this cracking happens with no such
warning present.

This module captures the exact bytes at two points and writes them to ordinary
WAV files that can be downloaded and listened to. Comparing the two answers the
question that log analysis cannot:

    tts_<session>.wav   PCM as it leaves the TTS provider, at tts_sample_rate
                        (24 kHz) — BEFORE the leg resample.
    leg_<session>.wav   PCM exactly as handed to Exotel, at leg_rate — AFTER
                        the resample, i.e. the literal wire content.

  • both clean          -> our pipeline is fine; the distortion is downstream
                           (the telephony leg, the codec, or the handset).
  • tts clean, leg bad  -> the 24k->leg_rate resample or the leg rate itself
                           is wrong (e.g. Exotel negotiated 8 kHz while we
                           were told 16 kHz).
  • both bad            -> Sarvam is returning damaged audio, or something
                           upstream of send_bytes is corrupting it.

SAFETY. Disabled unless DEBUG_AUDIO_DUMP_DIR is set. Every method swallows its
own exceptions: a diagnostic must never be able to break a live call. Writes
are plain buffered file appends on the existing event loop — at 16 kHz mono
that is ~32 KB/s per file, far below anything that would perturb the audio
path. Remember to unset the env var afterwards; these files grow for the whole
call and this is a deliberately unbounded debug tool, not a feature.
"""
from __future__ import annotations

import logging
import struct
from pathlib import Path

log = logging.getLogger(__name__)

_HEADER_SIZE = 44


def _header(rate: int, data_len: int) -> bytes:
    """Canonical 44-byte PCM16 mono WAV header."""
    return (
        b"RIFF" + struct.pack("<I", 36 + data_len) + b"WAVE"
        + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
        + b"data" + struct.pack("<I", data_len)
    )


class WavDump:
    """Append-only PCM16 mono WAV writer with a header patched on close.

    The header is written first with zero lengths so the file is valid-ish even
    if the process dies mid-call (most players will still decode it, and
    close() fixes the sizes on a clean hang-up)."""

    # Re-patch the header roughly this often so a file copied MID-CALL is still
    # playable. The original version only fixed the length in close(), which
    # meant any capture pulled off the box before the caller hung up declared
    # "0 frames" and every media player refused to open it — exactly what
    # happened on the first real capture. ~1 s of audio between patches is
    # negligible I/O and bounds the unplayable tail to the last second.
    _PATCH_EVERY_BYTES = 32000 * 2

    def __init__(self, path: Path, rate: int) -> None:
        self.path = path
        self.rate = rate
        self._n = 0
        self._since_patch = 0
        self._fh = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = path.open("wb")
            self._fh.write(_header(rate, 0))
        except Exception as e:                       # noqa: BLE001
            log.warning("audio dump: cannot open %s (%s) — capture disabled", path, e)
            self._fh = None

    def write(self, pcm: bytes) -> None:
        if self._fh is None or not pcm:
            return
        try:
            self._fh.write(pcm)
            self._n += len(pcm)
            self._since_patch += len(pcm)
            if self._since_patch >= self._PATCH_EVERY_BYTES:
                self._since_patch = 0
                self._patch_header()
        except Exception as e:                       # noqa: BLE001
            log.warning("audio dump: write failed (%s) — capture disabled", e)
            self.close()

    def _patch_header(self) -> None:
        """Rewrite the RIFF/data lengths in place, then seek back to append."""
        if self._fh is None:
            return
        pos = self._fh.tell()
        self._fh.seek(0)
        self._fh.write(_header(self.rate, self._n))
        self._fh.flush()
        self._fh.seek(pos)

    def close(self) -> None:
        fh, self._fh = self._fh, None
        if fh is None:
            return
        try:
            fh.seek(0)
            fh.write(_header(self.rate, self._n))
            fh.close()
            log.info("audio dump: wrote %s (%.1fs of %d Hz mono PCM16)",
                     self.path, self._n / 2.0 / max(1, self.rate), self.rate)
        except Exception as e:                       # noqa: BLE001
            log.warning("audio dump: close failed (%s)", e)


def open_dump(dump_dir: str, name: str, rate: int) -> WavDump | None:
    """Factory: returns None when capture is switched off."""
    if not dump_dir:
        return None
    return WavDump(Path(dump_dir) / name, rate)
