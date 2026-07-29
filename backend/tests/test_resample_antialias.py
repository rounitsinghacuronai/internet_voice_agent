"""Regression tests for aliasing on the outbound leg — the measured cause of
the long-running "voice cracking" report.

THE MEASUREMENT THAT STARTED THIS. Feed a 9 kHz tone through the real live
chain (22050 -> 24000 -> 16000) with no anti-alias filter and the STRONGEST
component of the output sits at 7 kHz. The energy does not disappear when the
rate drops; it mirrors around the new Nyquist and lands back inside the speech
band.

WHY THE LIVE CHAIN IS EXACTLY THAT. Both ends were confirmed on a real call,
not assumed:
  - Sarvam's streaming socket returns 22050 Hz
    ("working config found: {'output_audio_codec': 'wav'} (src rate 22050)"),
    so its content reaches ~11 kHz.
  - The Exotel leg is 16 kHz, read from the start message's media_format
    ("exotel start: ... leg_rate=16000Hz"), so it can only represent 8 kHz.
Everything between 8 and 11 kHz therefore has to go somewhere. TTS puts its
8-11 kHz energy into sibilants (s, sh, ch), so the artifact is transient and
consonant-locked — crackling on speech rather than steady distortion, which is
how it was reported ("cracking in between").

resample.py's module docstring had argued aliasing was inaudible because the
audio is "band-limited ... in the 300-3400 Hz voice band". True for a
narrowband 8 kHz telephone leg; false here, because this leg is 16 kHz
wideband and the audible band runs to 8 kHz.

These tests assert measured spectral behaviour, so they fail if the filter is
removed, mis-tuned, or accidentally applied to upsampling.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.telephony.resample import StreamResampler, _design_lowpass


def _tone(freq: float, fs: int, secs: float = 0.5, amp: float = 0.5) -> bytes:
    t = np.arange(int(fs * secs)) / fs
    return (amp * np.sin(2 * np.pi * freq * t) * 32767).astype("<i2").tobytes()


def _spectrum(pcm: bytes, fs: int):
    a = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
    sp = np.abs(np.fft.rfft(a * np.hanning(a.size)))
    return np.fft.rfftfreq(a.size, 1.0 / fs), sp


def _through_live_chain(freq: float) -> tuple[np.ndarray, np.ndarray]:
    """The exact production path: Sarvam 22050 -> pipeline 24000 -> leg 16000."""
    x = _tone(freq, 22050)
    y = StreamResampler(22050, 24000).process(x)
    z = StreamResampler(24000, 16000).process(y)
    return _spectrum(z, 16000)


@pytest.mark.parametrize("freq", [1000.0, 3000.0, 6000.0])
def test_speech_band_passes_through_at_the_right_frequency(freq):
    """The filter must not eat the voice. Everything a caller needs for
    intelligibility lives below ~6 kHz and has to survive."""
    f, sp = _through_live_chain(freq)
    assert abs(f[np.argmax(sp)] - freq) < 150.0


@pytest.mark.parametrize("freq", [9000.0, 10500.0])
def test_content_above_the_leg_nyquist_is_rejected_not_folded_back(freq):
    """The actual bug: without the filter these came out as the LOUDEST thing
    in the output, mirrored into 5-8 kHz. They must now be strongly attenuated
    relative to an in-band reference at the same amplitude."""
    _, sp_ref = _through_live_chain(3000.0)      # in-band, same amplitude
    _, sp_bad = _through_live_chain(freq)
    attenuation_db = 20.0 * np.log10(sp_bad.max() / sp_ref.max())
    assert attenuation_db < -20.0, (
        f"{freq:.0f} Hz survived at {attenuation_db:.1f} dB relative to a "
        "3 kHz reference — it is aliasing back into the speech band"
    )


def test_upsampling_is_left_unfiltered():
    """Raising the rate cannot alias, so no filter should be built for it —
    filtering there would only throw away real bandwidth."""
    up = StreamResampler(22050, 24000)
    down = StreamResampler(24000, 16000)
    assert up._fir is None
    assert down._fir is not None


def test_equal_rates_stay_a_passthrough():
    r = StreamResampler(16000, 16000)
    assert r._fir is None
    pcm = _tone(1000.0, 16000, secs=0.05)
    assert r.process(pcm) == pcm


def test_filter_state_is_carried_so_chunking_does_not_change_the_output():
    """The filter has memory. If its tail were not carried across chunks it
    would re-ring at every chunk boundary — replacing one click source with
    another. Chunked and unchunked must be bit-identical."""
    pcm = _tone(3000.0, 24000, secs=0.4)
    whole = StreamResampler(24000, 16000).process(pcm)

    chunked = StreamResampler(24000, 16000)
    step = 24000 * 2 // 25            # ~40 ms chunks
    parts = [chunked.process(pcm[i:i + step]) for i in range(0, len(pcm), step)]
    joined = b"".join(parts)

    a = np.frombuffer(whole, dtype="<i2")
    b = np.frombuffer(joined, dtype="<i2")
    n = min(a.size, b.size)
    assert abs(a.size - b.size) <= 1
    assert np.array_equal(a[:n], b[:n])


def test_output_duration_is_unchanged_by_filtering():
    """A filter must not add or drop samples — playback speed depends on it."""
    pcm = _tone(1000.0, 24000, secs=1.0)
    out = StreamResampler(24000, 16000).process(pcm)
    expected = 24000 * 16000 // 24000
    assert abs(len(out) // 2 - expected) <= 2


# ── the filter kernel itself ────────────────────────────────────────────────

def test_lowpass_kernel_has_unity_dc_gain_and_linear_phase():
    h = _design_lowpass(cutoff_hz=7200.0, fs=24000, ntaps=63)
    assert h.size == 63                      # odd -> integer group delay
    assert abs(h.sum() - 1.0) < 1e-12        # unity DC gain: no level shift
    assert np.allclose(h, h[::-1])           # symmetric -> linear phase


def test_even_tap_count_is_coerced_to_odd():
    assert _design_lowpass(7200.0, 24000, ntaps=64).size == 65
