"""Priority fast-path. Deterministic keyword gate (never trust the model alone with
fraud and personal safety). On a security incident: manager speaks a fixed line in the
caller's language, logs the incident and transfers — skipping OTP and the normal flow.

A routine service problem is NOT an incident: plain "net nahi chal raha / recharge
failed" must not trip this, so triggers are specific (fraud, OTP scam, SIM-swap fraud,
stolen phone, money debited by fraud, threat/harassment calls)."""
from __future__ import annotations

import re
from dataclasses import dataclass

_INCIDENTS: list[tuple[str, str]] = [
    # (regex, incident type)
    # fraud / OTP scams / SIM-swap fraud
    (r"(fraud|scam|धोखाधड़ी|फ्रॉड|फसवणूक|ठग)", "fraud"),
    (r"(otp).{0,30}(share|बता|दे दिया|सांगितला|de diya|bata diya|माग|मांग)", "otp_scam"),
    (r"(sim).{0,20}(swap|band ho gaya|बंद हो गया|बंद झाल|block ho gaya|अचानक)", "sim_swap_fraud"),
    (r"(paise|पैसे|अमाउंट|amount|रुपये).{0,30}(kat|कट|गायब|nikal|निकल|उड|गेले|debit)", "unauthorised_debit"),
    (r"(account|खाते|खाता).{0,25}(hack|हॅक|हैक|खाली|empty)", "unauthorised_debit"),
    # stolen / lost device — SIM must be blocked immediately
    (r"(phone|फोन|मोबाइल|मोबाईल|mobile).{0,25}(chori|चोरी|stolen|छीन|hisak|खो गया|हरवला|gum|गुम)", "stolen_device"),
    (r"(sim).{0,15}(chori|चोरी|stolen|खो|हरवल)", "stolen_device"),
    # threat / harassment calls
    (r"(dhamki|धमकी|threat|blackmail|ब्लॅकमेल|ब्लैकमेल)", "harassment"),
    (r"(harass|परेशान कर|त्रास देत).{0,20}(call|कॉल|फोन)", "harassment"),
    (r"(unknown|अनजान|अनोळखी).{0,15}(number|नंबर).{0,25}(baar baar|बार बार|परत परत|राात)", "harassment"),
    # romanized variants (codemix STT often outputs Latin script)
    (r"(mera|majha).{0,15}(sim|number).{0,20}(koi aur|dusra|kisi aur)", "sim_swap_fraud"),
    (r"fake (call|kyc|message)|kyc.{0,15}(expire|band|suspend)", "fraud"),
]

# Fixed spoken lines (never LLM-generated; reviewed wording) live in
# backend/app/persona.py, generated with the configured agent's grammatical
# gender — this module stays identity-neutral.


@dataclass
class SafetyVerdict:
    emergency: bool
    incident_type: str = ""

    @property
    def line_key(self) -> str:
        return "stolen" if self.incident_type == "stolen_device" else "generic"


def assess(text: str) -> SafetyVerdict:
    low = text.lower()
    for pattern, incident in _INCIDENTS:
        if re.search(pattern, low):
            return SafetyVerdict(True, incident)
    return SafetyVerdict(False)


def safety_line(verdict: SafetyVerdict, language: str, persona) -> str:
    """Fixed priority line in the caller's language, worded for the configured
    persona's grammatical gender (PersonaContext from backend/app/persona.py)."""
    lang = language if language in ("mr", "hi", "en") else "mr"  # Maharashtra circle default
    table = (persona.safety_shock if verdict.line_key == "stolen"
             else persona.safety_generic)
    return table[lang]
