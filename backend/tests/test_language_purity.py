"""Language Purity Guard — de-blending hi↔mr drift before TTS.

Reproduces the exact production failure (a Hindi reply drifting into Marathi
mid-sentence) and pins the invariants: English/undetermined pass through, pure
lines are untouched, and only unambiguous function-word intrusions are swapped.
"""
from app.conversation.purity import enforce_language_purity as purify


# ── the exact production bug (logs, session a43d097a7f18, turn 1) ────────────
def test_hindi_reply_with_marathi_tail_is_deblended():
    blended = ("आपको नया कनेक्शन चाहिए. कहाँ पर लगवाना आहे, "
               "मतलब पूरा पता सांगू शकाल का?")
    out, changed = purify(blended, "hi")
    assert changed
    assert "आहे" not in out and "है" in out
    assert "सांगू शकाल का" not in out and "बता सकते हैं" in out


def test_marathi_reply_with_hindi_intrusions_is_deblended():
    blended = "तुमचा नंबर रजिस्टर है और मुझे मदत करा"
    out, changed = purify(blended, "mr")
    assert changed
    assert "है" not in out and "आहे" in out
    assert "और" not in out and "आणि" in out
    assert "मुझे" not in out and "मला" in out


# ── invariants: never touch what is already correct ──────────────────────────
def test_english_is_untouched():
    line = "Sure, I can register that new fiber connection for you."
    assert purify(line, "en") == (line, False)


def test_pure_hindi_untouched():
    line = "ठीक है, मैं आपका नया कनेक्शन रजिस्टर कर देता हूँ."
    assert purify(line, "hi") == (line, False)


def test_pure_marathi_untouched():
    line = "ठीक आहे, मी तुमचं नवीन कनेक्शन नोंदवतो."
    assert purify(line, "mr") == (line, False)


def test_undetermined_language_passes_through():
    line = "मला मदत"
    assert purify(line, "und") == (line, False)


# ── loanwords the caller uses must survive ───────────────────────────────────
def test_english_loanwords_survive():
    line = "आपका recharge और network दोनों ठीक हैं."
    out, _ = purify(line, "hi")
    assert "recharge" in out and "network" in out


# ── safety backstop: a wholly-foreign line is left alone, not mangled ────────
def test_wholly_foreign_line_not_wholesale_rewritten():
    # A fully Marathi sentence mis-fed as active=hi: most Devanagari tokens are
    # Marathi, so token-swapping is suppressed (phrases may still normalise).
    line = "मला आहे नाही तुमचा माझा आणि कसे"   # every token Marathi
    out, _ = purify(line, "hi")
    # backstop keeps the original tokens rather than producing Frankenstein Hindi
    assert "मला" in out and "तुमचा" in out


# ── punctuation-attached tokens are still caught ─────────────────────────────
def test_token_with_trailing_punctuation():
    out, changed = purify("हे बरोबर आहे?", "hi")
    assert changed and "है?" in out
