# Human Speech Generation Engine + Voice Director

An intelligent layer between the LLM and Sarvam TTS that turns *an AI reading text*
into *a human naturally speaking*. It does **not** optimise Gemini or Sarvam. It
transforms raw LLM output into natural spoken dialogue before it reaches the TTS
engine, so the listener stops hearing "synthesized speech" and starts hearing a
calm, experienced Mahavitaran customer-care executive on the phone.

Everything here is deterministic and offline — no extra network call on the live
path, so it adds no latency to the ~1.4–2.1 s speech-to-speech budget. An
optional micro-LLM restructuring pass exists for teams that want to trade a
little latency for extra polish, and is off by default.

## Pipeline

```
Conversation context
        │
Gemini (streamed sentences)
        │
ResponseOptimizer     clean written artefacts (markdown, parentheses), rewrite
        │             AI/IVR phrasings → spoken, English contractions
Voice Director        conversation context → ONE StyleProfile for the whole turn
        │             (+ caller-emotion adaptation)
Human Speech Engine   active-listening lead-in, genuine hesitation (only when a
        │             lookup ran), thought-groups for breathing
Prosody Planner       pauses typed by MEANING (thinking / empathy / confirmation
        │             / transition / listening / completion) + question intonation
Sarvam Formatter      meaning → Sarvam punctuation, spoken number grouping,
        │             one pace value per utterance
Sarvam TTS
```

The Voice Director sits above the engine and assigns a *performance* — a style
profile — so the whole reply is delivered with a consistent, intentional cadence
instead of every sentence read flat.

## Why a code layer, not just the prompt

The prompt modules (`prompts/modules/02_style.md`, `02b_emotion.md`) already ask
Gemini to speak naturally, and it largely does. But a prompt cannot *guarantee*
delivery, and it cannot touch two things the model does not control:

- **Sarvam pace.** Only the API controls speed. The engine sets a per-utterance
  pace (slower for numbers, steadier for angry callers) that the LLM has no way
  to influence.
- **Deterministic prosody + number grouping.** The engine enforces digit-group
  pauses so a 12-digit consumer number is never rushed, and shapes pauses by
  meaning — regardless of model drift.

Where the LLM already did the right thing (opened with an acknowledgement, spelled
a number digit-by-digit), the engine detects it and stays out of the way. It is a
**safety net and prosody layer**, additive by design, never fighting the model.

## Voice Director — style profiles

One profile is chosen per turn from the conversation context. Each defines
emotion, pace, pause roominess, whether a lead-in/hesitation is allowed, and how
far to slow for numbers.

| Phase | Emotion | Pace | Character |
|---|---|---|---|
| Greeting | warm | 1.00× | warm, welcoming, medium pace (wording preserved) |
| Verification | helpful | 0.95× | clear, deliberate, **slower for numbers** (0.80×) |
| Power outage | concerned | 0.97× | calm, reassuring |
| Billing | helpful | 0.97× | patient, explanatory |
| Complaint registered | confident | 0.98× | confident, reassuring |
| Emergency | calm-urgent | 0.98× | calm, direct, tight pauses (wording preserved) |
| Closing | warm | 1.02× | friendly, concise |
| Default | helpful | 1.00× | warm, professional |

Pace is a multiplier on `Settings.tts_pace`, clamped to
`[speech_pace_min, speech_pace_max]`.

**Caller-emotion adaptation** layers on top, sensed from the caller's words
(multilingual, conservative). An angry caller is met with *more patience* and a
steadier, slightly slower pace — mirrored in warmth, never in heat. Elderly →
extra patience, simpler grouping, numbers slower. Worried → calm and reassuring.
Calm → efficient, no manufactured empathy.

## Human Speech Engine

- **Active-listening lead-in** on the first line of a turn only — a rotated
  acknowledgement ("Alright…", "बरं…", "जी…") so ten identical problems get ten
  slightly different openings. Suppressed when the line already opens with one.
- **Genuine hesitation** ("Let me just check…", "एक मिनिट, बघते…") used *only*
  when a real tool/lookup ran this turn (`SpeechContext.processing`). Never faked.
- **Thought-grouping** breaks a long run-on line into breathing-sized groups at
  natural clause boundaries, so a reply never sounds like one uninterrupted
  paragraph.
- **Reviewed lines** (greeting, safety, apology, silence prompts) are marked
  `preserve_wording` — prosody and pace only, wording untouched.

## Prosody Planner — pauses by meaning

Pauses are typed by *why* they exist, not by a fixed length, so placement feels
intentional: `THINKING`, `CONFIRMATION`, `EMPATHY`, `TRANSITION`, `LISTENING`,
`COMPLETION`, plus `MICRO`/`BREATH`. A closing question is given a `LISTENING`
pause (rising intonation) that invites the caller back; a completed action gets a
`CONFIRMATION` beat; a feeling gets an `EMPATHY` beat. Urgent delivery tightens
transitions to micro-pauses.

## Sarvam TTS Formatter — tuned for Sarvam, not generic

Sarvam Bulbul reads prosody almost entirely from punctuation, so the formatter is
tuned to exactly how Sarvam interprets it:

| Punctuation | Sarvam behaviour | Used for |
|---|---|---|
| `,` comma | brief pause, intonation held | MICRO / BREATH, and between digit groups |
| `…` ellipsis | longer, suspended "thinking" pause | lead-ins, empathy |
| `.` period | full stop, falling intonation, fuller pause | confirmations, transitions, completion |
| `?` question mark | rising intonation, invites a reply | closing question (LISTENING) |

**Number pronunciation.** Long identifier numbers are grouped and voiced
digit-by-digit with a comma pause between groups, so Sarvam never rushes them:

- consumer (12) → `4, 4, 4` → "1 7 0 0, 1 2 3 4, 5 6 7 8"
- mobile (10) → `5, 5` · meter (9) → `3, 3, 3` · OTP (6) → `3, 3`

Rupee **amounts** are left alone (spoken as words), and **alphanumeric complaint
IDs** are left to the LLM's phonetic rendering — mechanically spacing ASCII codes
reads worse. When a line contains a spoken digit group, the utterance pace drops
to the style's `number_pace` for clarity.

## Wiring

`ConversationManager` owns one `SpeechDirector` per call (holds the
anti-repetition tracker). Per turn:

1. On the first spoken sentence, the Voice Director picks a `StyleProfile` from
   the turn's context (topic, verification state, complaint-just-registered,
   caller emotion, confidence). That profile is reused for every sentence of the
   turn — a consistent performance.
2. Each streamed sentence is rendered to a `SpokenPlan` (text + pace + style) and
   yielded as a `TurnChunk`. The **semantic** sentence is committed to LLM history
   (barge-in safety unchanged); the **spoken** form goes only to TTS.
3. `ws_voice` passes `chunk.pace` to `SarvamTTS.synthesize(text, lang, pace)` and
   forwards `chunk.style` to the client for telemetry.

Greeting, emergency, apology and silence prompts go through `render_fixed()`
(prosody + pace only). Everything is reversible via `SPEECH_ENABLED=false`, which
falls straight back to raw sentence → TTS.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `SPEECH_ENABLED` | `true` | Master switch for the whole layer |
| `SPEECH_LLM_RESTRUCTURE` | `false` | Optional micro-LLM restructuring pass (adds latency) |
| `SPEECH_PACE_MIN` | `0.7` | Lower clamp on per-utterance Sarvam pace |
| `SPEECH_PACE_MAX` | `1.15` | Upper clamp on per-utterance Sarvam pace |

## Speech-naturalness evaluation

`backend/app/speech/evaluate.py` scores a set of spoken lines on repetition,
sentence length, rhythm variety, pause density, pause-type variety,
acknowledgement diversity and AI-pattern residue, and produces a before/after
comparison. Run the demo from the repo root:

```
python -m evaluation.speech_naturalness
```

Representative before/after over six MSEDCL replies (deterministic path, base
pace 1.0):

| metric | before | after | delta |
|---|---|---|---|
| naturalness_score | 60.0 | 88.0 | +28.0 |
| ai_pattern_hits | 7 | 1 | −6 |
| pause_type_variety | 0.00 | 0.375 | +0.375 |
| rhythm_cv | 0.675 | 0.752 | +0.077 |

Example transform:

```
BEFORE: I understand. I will now verify your consumer account. Please note that
        your consumer number is 170012345678.
AFTER : I see. Let me verify your consumer account. Just so you know, your
        consumer number is 1 7 0 0, 1 2 3 4, 5 6 7 8.        [pace 0.80]
```

## Tests

`backend/tests/test_speech_engine.py` — 28 offline tests covering de-AI rewriting,
spoken number formatting, Voice Director style selection, caller-emotion
adaptation, lead-in/hesitation (and the no-double-ack guard), thought grouping,
question intonation, pace planning/clamping, variation, and the evaluator's
before/after signal. Run:

```
python -m pytest backend/tests/test_speech_engine.py -q
```
