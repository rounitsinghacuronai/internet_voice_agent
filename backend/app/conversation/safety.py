"""Emergency fast-path. Deterministic keyword gate (never trust the model alone with
life safety). On hazard: manager speaks a fixed safety line in the caller's language,
logs the incident and transfers — skipping verification, OTP, everything.

A routine outage is NOT an emergency: plain "light gayi/current gaya" must not trip this,
so hazard terms are specific (wire down, shock, fire, sparking, pole collapse)."""
from __future__ import annotations

import re
from dataclasses import dataclass

_HAZARDS: list[tuple[str, str]] = [
    # (regex, incident type)
    (r"(wire|तार|line).{0,25}(down|fell|fall|snapp|broke|टूट|तुट|गिर|पड)", "wire_down"),
    (r"(तार|wire).{0,12}(खाली|रस्त्या|road|street)", "wire_down"),
    (r"(shock|करंट लग|झटका|electrocut|चटका)", "electric_shock"),
    (r"(transformer|ट्रान्सफॉर्मर|ट्रांसफार्मर|डीपी|dp).{0,30}(fire|burn|smoke|spark|blast|आग|जल|धूर|धुआ|ठिणग|चिंगारी|फट)", "transformer_fire"),
    (r"(आग|fire).{0,25}(transformer|तार|पोल|pole|मीटर|meter|डीपी)", "transformer_fire"),
    (r"(pole|पोल|खांब).{0,20}(fell|fall|collapse|गिर|पड|कोसळ)", "pole_collapse"),
    (r"(spark|ठिणग|चिंगारी|शॉर्ट सर्किट|short circuit)", "sparking"),
    (r"(live wire|खुली तार|उघडी तार|नंगा तार|current.{0,10}(तार|wire))", "live_conductor"),
    (r"(meter|मीटर).{0,15}(जल|burn|आग|smoke|धूर|धुआ)", "meter_burning"),
    # romanized Hindi/Marathi (codemix STT often outputs Latin script)
    (r"(taar|tar|wire).{0,20}(gir|tut|toot|pad|khali)", "wire_down"),
    (r"(current|shock).{0,12}(lag|laga|marla|basla)", "electric_shock"),
    (r"(transformer|dp).{0,30}(dhua|dhuaa|jal|aag|chingari|spark|phat|blast)", "transformer_fire"),
    (r"(aag(?![a-z])|jal rah|dhua nikal)", "transformer_fire"),
    (r"(pole|khamba).{0,18}(gir|pad|kosal)", "pole_collapse"),
    (r"chingari|thinag", "sparking"),
]

# fixed spoken safety lines (never LLM-generated; reviewed wording)
SAFETY_LINES: dict[str, dict[str, str]] = {
    "generic": {
        "mr": "कृपया त्या ठिकाणापासून लगेच दूर व्हा आणि कुणालाही जवळ जाऊ देऊ नका. मी ही आपत्कालीन तक्रार लगेच नोंदवते.",
        "hi": "कृपया उस जगह से तुरंत दूर हो जाइए और किसी को भी पास मत जाने दीजिए. मैं यह इमरजेंसी शिकायत अभी दर्ज कर रही हूँ.",
        "en": "Please move well away from it right now and keep everyone back. I am logging this emergency immediately.",
    },
    "electric_shock": {
        "mr": "आधी मेन स्विच बंद करा. त्या व्यक्तीला हाताने अजिबात स्पर्श करू नका — फक्त कोरड्या लाकडी काठीने बाजूला करा. मी ही आपत्कालीन तक्रार लगेच नोंदवते.",
        "hi": "पहले मेन स्विच बंद कीजिए. उस व्यक्ति को हाथ से बिल्कुल मत छूइए — सिर्फ सूखी लकड़ी की छड़ी से हटाइए. मैं यह इमरजेंसी शिकायत अभी दर्ज कर रही हूँ.",
        "en": "Switch off the main supply first. Do not touch the person with bare hands — move them only with a dry wooden stick. I am logging this emergency immediately.",
    },
}


@dataclass
class SafetyVerdict:
    emergency: bool
    incident_type: str = ""

    @property
    def line_key(self) -> str:
        return "electric_shock" if self.incident_type == "electric_shock" else "generic"


def assess(text: str) -> SafetyVerdict:
    low = text.lower()
    for pattern, incident in _HAZARDS:
        if re.search(pattern, low):
            return SafetyVerdict(True, incident)
    return SafetyVerdict(False)


def safety_line(verdict: SafetyVerdict, language: str) -> str:
    lang = language if language in ("mr", "hi", "en") else "mr"  # Maharashtra default
    return SAFETY_LINES[verdict.line_key][lang]
