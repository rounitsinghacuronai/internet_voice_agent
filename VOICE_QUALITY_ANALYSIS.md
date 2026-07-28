# Voice Quality — Log Analysis & Fixes

Analysis of call `e92a73ba446f` (Jul 28, 12:32–12:37) plus a full read of the audio,
TTS, telephony and conversation code. The reported symptoms were: voice **too fast /
too slow**, **volume high / low**, and **cracking when the speed flutters**. Root causes
found and fixed below.

---

## 1. Voice too fast + speed flutter — ROOT CAUSE (biggest fix)

**What the logs show:** every turn synthesized cleanly, but the *pace* differed sentence
to sentence.

**Why:** the live runtime config (`.env`) had

```
TTS_PACE=1.2
SPEECH_PACE_MAX=1.2
```

`1.2` is 20% faster than a natural human rate — the code's own comment says ">1.0 sounds
fast/robotic." Worse, the per-sentence pace is `base_pace × style_pace`, and a sentence
containing a number is dropped to `base × number_pace (0.85)`. With `base = 1.2` the
result swung between **~1.02 (number lines)** and **1.20 (plain text)** every few
seconds — heard as the agent constantly speeding up and slowing down.

**Fix:**
- `.env`: `TTS_PACE 1.2 → 1.0` (natural rate), `SPEECH_PACE_MAX 1.2 → 1.08`, added
  `SPEECH_PACE_MIN=0.9`.
- `config.py`: defaults narrowed to `speech_pace_min 0.7→0.9`, `speech_pace_max 1.15→1.08`.

Numbers still read slightly slower for clarity, but the whole band is now narrow, so
there is no audible speed jump between sentences.

## 2. "Sometimes too fast / too slow" within a line — REST TTS rate not verified

**Why:** the streaming TTS path reads the WAV header and resamples if Sarvam returns a
rate other than requested. The **REST fallback path did not** — it assumed the PCM was
already at 24 kHz. If Sarvam ever returned e.g. 22.05 kHz, the audio played at the wrong
**speed** with no correction.

**Fix (`providers/sarvam_tts.py`):** the REST path now parses the WAV header
(`_parse_wav`) and resamples to the pipeline rate when they differ — same guarantee the
streaming path already had.

## 3. Voice cracking "in between when the speed flutters" — buffer underflow

**Why:** the Exotel outbound leg only ran **1.0 s** ahead of real-time playback
(`_LEAD_S`). Any synthesis stall longer than a second — a slow REST round-trip, or the
gap while the *next* sentence is still synthesizing — drained the phone-leg buffer to
empty. That underflow is a click/crackle followed by a gap, and it lands exactly when the
next (differently-paced) sentence starts — which is precisely the "cracking when the
speed flutters" you heard.

**Fix (`telephony/exotel.py`):** `_LEAD_S 1.0 → 2.0`. Two seconds of lead rides through
synthesis stalls without the caller hearing a seam. It stays far under Exotel's frame
limits and audio is still 320-byte-chunked, so barge-in `clear` remains effectively
instant.

> Note: the per-chunk resampling that *was* the classic cracking cause is already fixed
> in this codebase (`StreamResampler` in both `sarvam_tts` and `exotel.send_bytes`).
> Underflow was the remaining source.

## 4. Volume high/low — already handled, confirmed

`OutputLoudness` (continuous, cross-sentence leveling) is enabled and correctly applied
before both the caller and the AEC reference. No code bug found here; steadier pacing
(above) also reduces the perceived level wander, because underflow gaps no longer chop
the leveler's envelope.

---

## Other real bugs found in the same logs (fixed)

### 5. Misleading `sentence TTS took …ms` warnings (they scared the diagnosis)
The log line `sentence TTS took 12520ms (110 chars)` appeared **one line after
`CACHE HIT, 0 billed`** — a cache hit cannot take 12 s to synthesize. The metric was
measuring wall-clock, which **includes the intentional real-time pacing sleeps**, so it
false-alarmed on every sentence longer than ~2 s of audio and hid the genuine stalls.

**Fix (`api/ws_voice.py`):** now measures **first-audio latency** (true synthesis TTFB)
and **real-time lag** (`wall − audio_duration`), and only warns when we actually fell
behind real time. The number in the log finally means "the caller heard a gap."

### 6. STT stream disabled for the whole call by a harmless flush
`stt-stream: send failed (… Cannot flush: no audio input has been received.)` — an early
flush fired while the caller was still silent (right after the greeting), Sarvam raised,
and the code **disabled streaming STT for the entire call**, dropping to slower REST.

**Fix (`providers/sarvam_stt_stream.py`):** track whether any audio was sent since the
last flush; a flush with nothing buffered is a no-op and is skipped.

### 7. Duplicate new-connection registration
The call registered the **same** connection twice — `NC2607562E41` (12:36:13) and
`NC260739F561` (12:37:00) — one caller, one address, two applications. A barge-in
cancelled the turn *after* the tool ran but *before* the confirmation was spoken, so the
flow re-asked and registered again.

**Fix (`tools/telecom.py`):** `register_new_connection` is now idempotent — an identical
request (same name+contact+pincode+service, or same caller number) within 15 minutes
returns the existing application number instead of minting a duplicate.

---

## Not code bugs — infrastructure (flagged, not changed)

- **WhatsApp ops notifications failed every attempt:** `bridge 503: bridge not ready
  (starting)` × 3 for `TT-2026-CA5C`. The WhatsApp bridge at `WHATSAPP_BRIDGE_URL`
  (`127.0.0.1:3001`) was not running. Customer conversation is unaffected, but ops pings
  are being dropped — start/restart the bridge service.
- **Speech-to-speech latency over target:** `total avg=1733 max=2462 | target <800ms`.
  The dominant term is the LLM (`llm avg=1040`). Enabling TTS streaming (already
  `TTS_STREAMING_ENABLED=true`) helps first-audio; the LLM leg is the next lever.

---

## Verification
- `test_stream_resampler` (22), `test_exotel` (22), `test_silence_watchdog` (11),
  number/phone/pincode, new-connection and speech-engine suites — **all pass**
  (378 passing total in the runnable set).
- Direct functional checks added and passed for the telecom dedup and the REST
  rate-resample path.

## Files changed
- `.env` — pace values (live runtime)
- `backend/app/config.py` — pace-bound defaults
- `backend/app/providers/sarvam_tts.py` — REST rate verify + resample
- `backend/app/telephony/exotel.py` — outbound lead buffer 1 s → 2 s
- `backend/app/api/ws_voice.py` — corrected TTS timing diagnostic
- `backend/app/providers/sarvam_stt_stream.py` — flush-before-audio guard
- `backend/app/tools/telecom.py` — idempotent new-connection registration

> To apply on the server: pull the changes and **restart the service** so the new `.env`
> pace values load.
