"""Persona Engine — configuration-driven identity, gender grammar, validation.

Success criteria under test (mirrors the persona spec):
  • AGENT_NAME / AGENT_GENDER alone fully transform the assistant
  • zero hardcoded identities in rendered prompts and fixed lines
  • Marathi/Hindi first-person grammar always matches the configured gender
  • the pre-TTS validation layer auto-corrects opposite-gender slips
  • caller-addressed (2nd/3rd-person) forms are never touched
  • language switching never changes the persona (session lock)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.config import Settings
from backend.app.conversation import safety
from backend.app.persona import get_persona
from backend.app.prompts.loader import compose_system_prompt
from backend.app.speech.pipeline import SpeechDirector
from backend.app.speech.plan import SpeechContext


def _settings(name="Priya", gender="female", speaker="", **kw) -> Settings:
    return Settings(agent_name=name, agent_gender=gender, tts_speaker=speaker,
                    tts_pace=1.0, **kw)


FEM = get_persona(_settings("Priya", "female"))
MAL = get_persona(_settings("Rahul", "male"))


# ── configuration-driven identity ────────────────────────────────────────────
def test_greeting_uses_configured_name_and_gender():
    assert "Priya" in FEM.greeting and FEM.greeting.rstrip("?").endswith("करू शकते")
    assert "Rahul" in MAL.greeting and MAL.greeting.rstrip("?").endswith("करू शकतो")


def test_silence_nudge_gendered_all_languages():
    assert "करू शकते" in FEM.silence_nudge["mr"]
    assert "कर सकती हूँ" in FEM.silence_nudge["hi"]
    assert "करू शकतो" in MAL.silence_nudge["mr"]
    assert "कर सकता हूँ" in MAL.silence_nudge["hi"]
    assert FEM.silence_nudge["en"] == MAL.silence_nudge["en"]  # English is genderless


def test_safety_lines_gendered():
    v = safety.SafetyVerdict(True, "wire_down")
    assert "नोंदवते" in safety.safety_line(v, "mr", FEM)
    assert "नोंदवतो" in safety.safety_line(v, "mr", MAL)
    shock = safety.SafetyVerdict(True, "electric_shock")
    assert "कर रही हूँ" in safety.safety_line(shock, "hi", FEM)
    assert "कर रहा हूँ" in safety.safety_line(shock, "hi", MAL)


def test_default_voice_follows_gender_and_override_wins():
    assert FEM.voice == "ritu"
    assert MAL.voice == "advait"
    pinned = get_persona(_settings("Priya", "female", speaker="anushka"))
    assert pinned.voice == "anushka"


# ── prompt integration: no hardcoded identity anywhere ───────────────────────
def test_rendered_prompt_contains_only_configured_identity():
    for persona, name, other in ((FEM, "Priya", "Rahul"), (MAL, "Rahul", "Priya")):
        prompt = compose_system_prompt("LANG", "MEMORY", persona=persona)
        assert name in prompt
        assert other not in prompt
        assert "रतन" not in prompt and "Ratan" not in prompt
        assert "{{" not in prompt          # every placeholder rendered
        assert persona.greeting in prompt  # greeting quote follows the persona


def test_gender_rules_injected_into_prompt():
    fp = compose_system_prompt("L", "M", persona=FEM)
    mp = compose_system_prompt("L", "M", persona=MAL)
    assert "is a WOMAN" in fp and "करती हूँ" in fp
    assert "is a MAN" in mp and "करता हूँ" in mp


# ── validation layer: enforce_gender ─────────────────────────────────────────
def test_male_persona_corrects_feminine_first_person():
    out = MAL.enforce_gender("एक मिनट, मैं देख रही हूँ, अभी बताती हूँ.", "hi")
    assert "रहा हूँ" in out and "बताता हूँ" in out
    assert "रही हूँ" not in out
    out = MAL.enforce_gender("मी तुमची तक्रार नोंदवते आणि मदत करू शकते.", "mr")
    assert "नोंदवतो" in out and "करू शकतो" in out


def test_female_persona_corrects_masculine_first_person():
    out = FEM.enforce_gender("मैं अभी जाँच करता हूँ और दर्ज कर रहा हूँ.", "hi")
    assert "करती हूँ" in out and "कर रही हूँ" in out
    out = FEM.enforce_gender("मी लगेच बघतो आणि तक्रार नोंदवतो.", "mr")
    assert "बघते" in out and "नोंदवते" in out


def test_caller_addressed_forms_never_touched():
    # Second person (आप + हैं) and third person forms must survive unchanged.
    line = "आप ऑनलाइन भी भर सकती हैं, और आपकी बेटी भी कर सकती हैं."
    assert MAL.enforce_gender(line, "hi") == line
    line_mr = "तुम्ही ॲपवरून भरू शकता, आणि सेवा लगेच सुरू होते."
    assert MAL.enforce_gender(line_mr, "mr") == line_mr


def test_pipeline_applies_gender_fix_before_tts():
    sd = SpeechDirector(_settings("Rahul", "male"))
    ctx = SpeechContext(language="hi", turn_no=2, is_first_utterance=True)
    plan = sd.plan_line("जी, मैं अभी देख रही हूँ.", ctx)
    assert "देख रहा हूँ" in plan.text
    assert "gender-fix" in plan.notes


# ── session lock / language switching ────────────────────────────────────────
def test_persona_is_cached_and_stable():
    a = get_persona(_settings("Rahul", "male"))
    b = get_persona(_settings("Rahul", "male"))
    assert a is b                          # same immutable instance
    # switching conversation language has no persona effect by construction:
    # every fixed line for every language carries the same configured gender.
    assert "सकता हूँ" in a.silence_nudge["hi"] and "शकतो" in a.silence_nudge["mr"]


def test_invalid_gender_falls_back_to_male():
    p = get_persona(_settings("X", "attack-helicopter"))
    assert p.gender == "male"


# ── end_call tool ─────────────────────────────────────────────────────────────
def test_end_call_tool_registered_and_ungated():
    import asyncio
    from backend.app.tools import registry as reg
    from backend.app.conversation.memory import CallMemory
    assert "end_call" in reg._UNGATED
    names = [s["function"]["name"] for s in reg.build_schemas()]
    assert "end_call" in names

    class _R(reg.ToolRegistry):
        def __init__(self): self.s = None; self.retriever = None; self._map = {}
    r = _R()
    out = asyncio.run(r._dispatch_inner("end_call", {"reason": "resolved"}, CallMemory()))
    assert out["status"] == "ok"
