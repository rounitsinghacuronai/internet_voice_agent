# Robustness Test Scenarios

Companion to the Conversation Robustness Layer (`backend/app/conversation/numbers.py`,
`robustness.py`, and the hardened gates in `tools/registry.py`). For each scenario:
what's automated today (`backend/tests/test_number_recognition.py`,
`test_robustness.py`), and what still requires live-call / manual QA — audio-domain
behavior (actual background noise, real barge-in timing, real STT hallucinations)
can't be meaningfully unit-tested without recorded audio fixtures, which this pass
did not have time to build. Treat the "manual QA" scenarios as a checklist for the
next live test round on the Exotel number, not as already-verified.

## Automated (unit-tested today)

| # | Scenario | Expected behavior | Test |
|---|---|---|---|
| 1 | Interrupted numbers (pauses between digit groups) | Digit fragments merge into one complete number; nothing sent to the LLM/tools until complete | `test_number_buffer_merges_fragments_across_pauses` |
| 2 | Partial mobile/consumer number, caller stops early | Buffer holds partial state indefinitely; never marks complete below expected length | `test_number_buffer_never_completes_early` |
| 3 | Customer changes one digit ("last digit is 2") | Only the trailing digit(s) replaced; full number is not re-collected | `test_correction_updates_only_the_wrong_digit` |
| 4 | Customer over-speaks past the number's length | Buffer truncates at expected length rather than corrupting | `test_number_buffer_truncates_overlong_input` |
| 5 | Number spoken entirely as words in one breath | Same digit-word vocabulary catches it inline via `CallMemory.scan_user_text` | `test_normalize_digit_words_preserves_sentence_structure` |
| 6 | Wrong-length / impossible number reaches a tool call | `verify_consumer`/`send_otp`/`verify_otp` refuse with `invalid_number_format` before hitting the backend | gate added in `tools/registry.py::_validate_number_args` (exercised indirectly; see Manual QA #16 for the live-call version) |
| 7 | Low-confidence utterance (noisy audio) | LLM told to confirm only the uncertain part, never "please repeat everything" | `test_low_confidence_when_audio_unclear` |
| 8 | Medium-confidence utterance | LLM told to infer from context, not re-ask by default | `test_medium_confidence_between_thresholds` |
| 9 | Fixed language_code call (no Sarvam language_probability) | Absence of the signal doesn't drag confidence down artificially | `test_missing_language_confidence_does_not_drag_down_tier` |
| 10 | Background word during an active topic (TV, stray word) | Active topic (e.g. outage) does NOT flip on a single unrelated word | `test_topic_stability_ignores_single_stray_background_word` |
| 11 | Genuine topic change, stated clearly and consistently | Topic switches after 2 consistent utterances about the new topic | `test_topic_stability_switches_after_consistent_new_topic` |

## Manual QA — requires a live call or recorded audio fixture

| # | Scenario | Expected AI behavior |
|---|---|---|
| 12 | Background television | Primary speaker's utterance is transcribed; TV audio either fails the VAD's peak-probability gate (dropped as noise) or, if it leaks into the same utterance, the confidence tier drops to MEDIUM/LOW so the LLM is told to lean on context rather than react to it. Topic must not flip on TV content (covered logically by #10, needs a real noisy recording to confirm end-to-end). |
| 13 | Fan / AC background noise | Same as above — steady-state noise should mostly be handled by the existing spectral noise gate (`audio/spectral_gate.py`) before it reaches STT at all; confirm it isn't triggering unnecessary LOW-confidence hedging on otherwise clear speech. |
| 14 | Road traffic noise | Same pattern; also confirm VAD's `speech_confirm_peak_prob` gate isn't so strict it drops genuine speech over traffic, or so loose that traffic alone triggers an utterance. |
| 15 | Multiple people speaking at once | Speaker verification (`audio/speaker_verifier.py`) exists for this exact case but ships **disabled by default** (`speaker_verify_enabled: false`) because MFCC cosine-similarity was found too sensitive to mic/room variation in earlier testing. Decide whether to enable it for telephony specifically (fixed acoustic path, less variation than browser testing) before relying on it. |
| 16 | Slow / elderly speaker with long thinking-pauses | `vad_end_silence_ms` (650ms default) and `vad_max_utterance_s` govern this — confirm the current hangover is generous enough for real elderly callers without making ordinary turn-taking feel laggy. This is a tuning call best made against real call recordings, not guessed. |
| 17 | Wrong verification (consumer number resolves to someone else, or lookup fails) | `verify_consumer` gate already refuses writes without fresh verification (`tools/registry.py`); confirm the LLM's spoken response on a failed lookup is graceful and re-prompts for a corrected number rather than looping silently. |
| 18 | STT hallucination (transcript unrelated to what was said) | No code can detect a hallucination directly — the confidence-tier proxy (peak_prob) is the only signal available, and a confident hallucination on clear audio won't trigger it. This is a real, currently-unaddressed gap; the practical mitigation is the LLM's own turn-taking (asking a clarifying question when the response doesn't fit context) rather than a detection mechanism. Flagging honestly rather than claiming it's solved. |
| 19 | Tool failures / API timeouts (Sarvam, Gemini, backend tools) | `ProviderError` → spoken apology in the active language (`conversation/manager.py::_APOLOGY`) already exists for STT/LLM; tool-level exceptions are caught in `tools/registry.py::_dispatch_inner` and returned as an `{"error": ...}` dict for the LLM to react to conversationally. Confirm the LLM's reaction to a tool error is natural, not robotic. |
| 20 | Language switching mid-number | The number buffer only cares about digit words across `_EN`/`_HI_DEV`/`_MR_DEV`/`_ROM` vocabularies — a caller switching languages mid-number is still captured as long as the words map to a known digit. Worth a real test since STT language-hint switching mid-utterance could affect how Sarvam transcribes the digit words themselves. |
| 21 | Barge-in during number collection/verification | Not yet specifically handled — barge-in cancels the current turn/TTS as normal, but the number buffer's state is independent of the turn lifecycle (it lives on `CallMemory`, not the cancellable turn task), so a barge-in mid-collection should NOT lose already-buffered digits. Confirm this holds in a real interrupted-number test call. |
| 22 | Returning customer within the same call (multiple complaints) | `CallMemory.complaints` already accumulates multiple `ComplaintRecord`s per call, and `render_block()` surfaces all of them to the LLM every turn — confirm the LLM correctly references the right complaint when the caller asks about "the second one" or similar. |

## Known scope boundaries (not attempted this pass)

- **True STT confidence**: Sarvam's API has no per-word/utterance transcription confidence, only `language_probability` (confidence in the *detected language*). The confidence tiering here is an honest proxy (VAD peak-probability + that language signal), not real STT confidence — documented in `robustness.py` so it isn't mistaken for one later.
- **STT hallucination detection**: no deterministic signal exists to catch a confident-but-wrong transcript; see scenario #18.
- **Speaker verification for multi-speaker rejection**: exists in the codebase already but is disabled by default pending real telephony-audio tuning; see scenario #15.
