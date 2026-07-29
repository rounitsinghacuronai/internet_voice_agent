"""Regression tests for production call 046362528d00: the caller reported the
agent was "fumbling", "very unclear", and "speaking anything I do not
understand", and that it never switched language.

Cause was text, not audio. speech/pipeline.py ran every line through
conversation/purity.py (a deterministic hi<->mr function-word swap) immediately
before TTS. Each of these inputs was clean, grammatical Marathi; each output is
grammatical in NEITHER language:

    'मी ऐकतो आहे.'                          -> 'मी ऐकतो है.'
    'अच्छा, तुम्हाला हिंदीमध्ये बोलायचं आहे का?'  -> '...आपको हिंदीमध्ये बोलायचं है का?'
    'ठीक आहे, पुणे मध्ये तुम्हाला ... हवं आहे.'  -> 'ठीक है, पुणे मध्ये आपको ... चाहिए.'

purity.py's stated safety argument is "each source token exists in only ONE of
the two languages, so a swap can never change meaning". That holds at the WORD
level and fails at the SENTENCE level: 'मी ऐकतो' is a Marathi verb inflected for
person, and pairing it with the Hindi copula 'है' yields a sentence in no
language. Its >50%-of-Devanagari-tokens backstop only guards against a WHOLLY
foreign line, so it is blind to the actual failure mode — a PARTIALLY rewritten
one. 'मी ऐकतो आहे' is 3 tokens with 1 in the map, and 1 > 1.5 is false, so the
swap proceeds. A 29-entry function-word table can only ever rewrite function
words while leaving verbs, postpositions and agreement in the source language,
so partial substitution is strictly worse than leaving the line alone. Sarvam is
then asked to pronounce text invalid in the language it was handed, which is
what the caller heard.

The pass is now gated behind speech_language_purity (default False), restoring
the reference deployment's behaviour of having no such stage at all. purity.py
and its own unit tests are kept so the module still works if ever enabled
deliberately.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.config import Settings, get_settings
from backend.app.speech.pipeline import SpeechDirector
from backend.app.speech.plan import SpeechContext


# the exact lines the LLM produced in call 046362528d00, with what purity
# turned each one into
PRODUCTION_LINES = [
    ("मी ऐकतो आहे.", "मी ऐकतो है."),
    ("अच्छा, तुम्हाला हिंदीमध्ये बोलायचं आहे का?",
     "अच्छा, आपको हिंदीमध्ये बोलायचं है का?"),
    ("तुमचा पूर्ण पत्ता आणि पिनकोड सांगू शकाल का?",
     "आपका पूर्ण पत्ता और पिनकोड बता सकते हैं?"),
]


def test_purity_pass_is_disabled_by_default():
    assert Settings().speech_language_purity is False


@pytest.mark.parametrize("original,corrupted", PRODUCTION_LINES)
def test_grammatical_marathi_survives_the_pipeline_untouched(original, corrupted):
    """The rendered line must still contain the Marathi the model wrote, and
    must NOT contain the ungrammatical hybrid purity used to produce."""
    sd = SpeechDirector(get_settings())
    ctx = SpeechContext(language="hi", turn_no=2, is_first_utterance=False)
    plan = sd.render(original, sd.direct(ctx), ctx)

    assert "purity-fix" not in plan.notes
    # the specific breakage: a Marathi verb left agreeing with a Hindi copula
    assert "ऐकतो है" not in plan.text
    assert "बोलायचं है" not in plan.text
    # and the swapped function words must not have been introduced
    for hindi_token in ("आपको", "आपका", "चाहिए", "बता सकते"):
        if hindi_token not in original:
            assert hindi_token not in plan.text, (
                f"purity-style rewrite reintroduced {hindi_token!r}")


def test_pipeline_does_not_import_purity_into_the_hot_path_by_default():
    """Guards the gate itself: the call must be behind the setting, not
    unconditional as it was before."""
    src = (Path(__file__).resolve().parents[1]
           / "app" / "speech" / "pipeline.py").read_text(encoding="utf-8")
    code = "\n".join(ln for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))
    assert "speech_language_purity" in code
    idx = code.index("enforce_language_purity(cleaned, lang)")
    preceding = code[:idx]
    assert "if getattr(self.s, \"speech_language_purity\", False):" in preceding


def test_purity_module_still_functions_when_explicitly_enabled():
    """The module is kept, not deleted — enabling the flag restores the old
    behaviour exactly (including, deliberately, its known breakage)."""
    from backend.app.conversation.purity import enforce_language_purity

    out, changed = enforce_language_purity("मी ऐकतो आहे.", "hi")
    assert changed is True
    assert out == "मी ऐकतो है."          # documents WHY it is off by default
