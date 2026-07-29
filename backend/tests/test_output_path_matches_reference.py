"""Locks the OUTBOUND audio path to THIS project's known-good configuration.

This file previously asserted the opposite of what it asserts now, and the
reason is worth recording, because the mistake was methodological rather than
technical.

The outbound path was diffed against a different deployment ("the reference")
whose voice quality the caller liked, and every difference was treated as a
defect. On that basis three things were switched off: the output loudness
leveler, the first-audio comma flush, and the hi<->mr purity rewrite. But the
correct baseline was never the other project's file layout — it was THIS
project's own history. Git shows the loudness leveler (with the identical
continuous implementation, start_sentence() already a no-op) and the 80-char
first flush were both live throughout the period the caller reported the voice
sounding correct. Disabling them removed what was holding the level steady, and
the next report was "sometimes slow and loud, sometimes fast and very low
volume".

What stays disabled, and why the asymmetry is principled:

  - purity rewrite: OFF, on direct evidence rather than comparison. Production
    logs show it turning grammatical Marathi into text valid in no language
    ('मी ऐकतो आहे' -> 'मी ऐकतो है'), and the call after disabling it came back
    with clean Hindi throughout. Kept off; see test_no_language_purity_rewrite.

  - loudness leveler + first flush: ON, restored to the known-good values.
    Both were disabled on reasoning, not measurement, and neither disabling
    fixed a reported symptom.

Measured audio fixes made along the way are independent of all this and stay:
the anti-alias filter on the leg downsample, the streaming-WAV first-chunk
parse, and the fade on severed tails.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.config import Settings, get_settings


# ── known-good configuration ────────────────────────────────────────────────

def test_output_loudness_is_enabled():
    """Restored: this was True throughout the period the voice was correct."""
    assert Settings().tts_loudness_normalize is True


def test_voice_session_builds_the_loudness_stage():
    from backend.app.api import ws_voice

    deps = MagicMock()
    deps.settings = get_settings()
    with patch.object(ws_voice, "SileroVAD", MagicMock()), \
         patch.object(ws_voice, "Endpointer", MagicMock()), \
         patch.object(ws_voice, "AudioPipeline", MagicMock()):
        sess = ws_voice.VoiceSession(MagicMock(), deps)
    assert sess._loudness is not None


def test_first_flush_threshold_is_restored():
    s = Settings()
    assert s.llm_first_flush_chars == 80


def test_manager_uses_the_first_flush_threshold_for_the_opening_segment():
    src = (Path(__file__).resolve().parents[1]
           / "app" / "conversation" / "manager.py").read_text(encoding="utf-8")
    code = "\n".join(ln for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))
    assert "llm_first_flush_chars" in code
    assert "self._turn_is_first" in code


def test_purity_rewrite_stays_disabled():
    """The one thing disabled on evidence, not comparison — it corrupted text."""
    assert Settings().speech_language_purity is False


# ── measured audio fixes must stay wired in ─────────────────────────────────

def test_streaming_resamplers_still_used_on_both_legs():
    """Guards against reverting to the stateless per-chunk resample_pcm16,
    which restarts interpolation at every chunk seam."""
    for mod in ("telephony/exotel.py", "providers/sarvam_tts.py"):
        src = (Path(__file__).resolve().parents[1] / "app" / mod
               ).read_text(encoding="utf-8")
        assert "StreamResampler" in src, f"{mod} lost its streaming resampler"


def test_leg_downsample_is_still_anti_aliased():
    """Measured: without this a 9 kHz tone came out at 7 kHz, full strength."""
    src = (Path(__file__).resolve().parents[1]
           / "app" / "telephony" / "resample.py").read_text(encoding="utf-8")
    assert "_design_lowpass" in src


def test_severed_tails_are_still_faded():
    """Measured: abrupt cuts at 42.1 s / 159.6 s / 217.8 s in a captured call."""
    src = (Path(__file__).resolve().parents[1]
           / "app" / "telephony" / "exotel.py").read_text(encoding="utf-8")
    assert "_fade_out_tail" in src


def test_pace_band_matches_the_known_good_values():
    """Restored to 0.7–1.15. These were briefly narrowed to 0.9–1.08 to shrink
    the perceived speed difference between plain and number-carrying lines —
    coherent in theory, but applied without evidence, and it clamped the
    deliberately slower number styles (number_pace 0.78–0.9) UP to 0.9, making
    digits faster and less clear than the Voice Director intended.
    profiles.py has never changed, so these bounds are what its per-style
    paces were designed against."""
    s = Settings()
    assert s.speech_pace_min == 0.7
    assert s.speech_pace_max == 1.15
    assert s.tts_pace == 1.0


def test_style_paces_are_not_clamped_by_the_band():
    """The bounds must not silently rewrite any profile's intended delivery."""
    from backend.app.speech.profiles import PROFILES

    s = Settings()
    for name, prof in PROFILES.items():
        for label, val in (("pace", prof.pace), ("number_pace", prof.number_pace)):
            if val is None:
                continue
            eff = val * s.tts_pace
            assert s.speech_pace_min <= eff <= s.speech_pace_max, (
                f"{name.value}.{label}={val} lands outside "
                f"[{s.speech_pace_min}, {s.speech_pace_max}] and gets clamped"
            )
