"""Persona Engine — the single source of truth for the agent's identity.

Every identity-bearing string in the application (name, gender, greetings,
first-person grammar, safety lines, prompt identity block, default TTS voice)
is GENERATED here from three environment variables:

    AGENT_NAME=Ratan
    AGENT_GENDER=male        # male | female
    AGENT_ROLE=customer care executive

Changing only these values transforms the whole assistant — greeting, prompts,
fixed spoken lines, grammatical gender in Marathi and Hindi, and the default
Sarvam voice — with zero code or prompt edits. No other component is allowed
to hard-code a name or a gendered verb form.

Architecture:

    Settings (.env)
        → get_persona(settings)  → PersonaContext   (cached, immutable)
            → ConversationManager   (greeting / nudges / closings / emergency)
            → prompts.loader        (renders {{AGENT_NAME}} / {{GENDER_GRAMMAR}}
                                     / {{GREETING}} placeholders each session)
            → conversation.safety   (gendered fixed safety lines)
            → SpeechDirector        (pre-TTS gender validation & auto-rewrite)
            → SarvamTTS             (default speaker when TTS_SPEAKER unset)

PERSONA LOCK: ConversationManager resolves the persona ONCE at session start
and keeps the reference for the whole call; language switching mid-call can
never change it. A process restart re-reads the environment.

GENDER VALIDATION LAYER: enforce_gender() runs on every line immediately
before TTS formatting. It deterministically rewrites opposite-gender
FIRST-PERSON forms (Hindi forms anchored to "हूँ", Marathi to a curated
agent-verb list) so a model slip like "मैं देख रही हूँ" from a male persona is
corrected to "देख रहा हूँ" before the caller ever hears it. Second/third-person
forms addressed to the CALLER are never touched.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache

log = logging.getLogger(__name__)

MALE, FEMALE = "male", "female"

# Default Sarvam Bulbul v3 voices per gender, used when TTS_SPEAKER is unset.
_DEFAULT_VOICE = {MALE: "advait", FEMALE: "ritu"}

# ── gendered first-person pairs (female_form, male_form) ────────────────────
# HINDI — every pair is anchored to the first-person "हूँ", which in Hindi can
# only ever refer to the speaker, so rewriting is always safe.
_PAIRS_HI: list[tuple[str, str]] = [
    ("रही हूँ", "रहा हूँ"),
    ("सकती हूँ", "सकता हूँ"),
    ("करती हूँ", "करता हूँ"),
    ("देखती हूँ", "देखता हूँ"),
    ("बताती हूँ", "बताता हूँ"),
    ("भेजती हूँ", "भेजता हूँ"),
    ("समझती हूँ", "समझता हूँ"),
    ("चाहती हूँ", "चाहता हूँ"),
    ("जाँचती हूँ", "जाँचता हूँ"),
    ("करवाती हूँ", "करवाता हूँ"),
    ("समझ गई, ", "समझ गया, "),
    ("समझ गई.", "समझ गया."),
]
# MARATHI — no "हूँ" anchor exists, so only a curated list of verbs the AGENT
# uses about itself is rewritten ("मी तपासते/तपासतो" class). Broad forms like
# bare "करते/जाते" are deliberately excluded: they are also third-person and
# rewriting them would corrupt sentences about the supply, the team, etc.
_PAIRS_MR: list[tuple[str, str]] = [
    ("ू शकते", "ू शकतो"),          # "करू शकते / देऊ शकते ..." first-person modal
    ("तपासते", "तपासतो"),
    ("बघते", "बघतो"),
    ("नोंदवते", "नोंदवतो"),
    ("सांगते", "सांगतो"),
    ("पाठवते", "पाठवतो"),
    ("सोडवते", "सोडवतो"),
    ("बोलते", "बोलतो"),
    ("करवते", "करवतो"),
]


@dataclass(frozen=True, eq=False)
class PersonaContext:
    """Immutable, session-locked identity. Everything below is derived purely
    from (name, gender, role) — no other component decides identity.

    eq=False → identity-based hashing: instances come exclusively from
    get_persona()'s cache, so identity equality is exact, and the dict fields
    (which would break field-based hashing) stay out of the hash."""
    name: str
    gender: str                       # "male" | "female"
    role: str
    voice: str                        # default TTS speaker for this gender
    greeting: str = ""
    # Personalized opener for a recognized caller (call from a registered mobile).
    # Contains a literal "{first}" placeholder filled with the caller's first name.
    greeting_personal: str = ""
    apology: dict = field(default_factory=dict)
    silence_nudge: dict = field(default_factory=dict)
    no_response_closing: dict = field(default_factory=dict)
    emergency_follow: dict = field(default_factory=dict)
    safety_generic: dict = field(default_factory=dict)
    safety_shock: dict = field(default_factory=dict)
    # ── AI → human escalation (spoken in the caller's current language) ──
    transfer_intro: dict = field(default_factory=dict)      # warm hand-off + connecting
    transfer_failed: dict = field(default_factory=dict)     # transfer could not complete
    transfer_callback: dict = field(default_factory=dict)   # offer a callback instead

    # ── recognized-caller opener ─────────────────────────────────────────────
    def personal_greeting(self, first_name: str) -> str:
        """Opener when the caller is recognized from a registered mobile. Falls
        back to the standard greeting if no name or template is available."""
        first = (first_name or "").strip()
        if not first or not self.greeting_personal:
            return self.greeting
        return self.greeting_personal.replace("{first}", first)

    # ── prompt integration ───────────────────────────────────────────────────
    def identity_line(self) -> str:
        return (f"You are {self.name}, a warm, experienced {self.role} at "
                "Syncbroad Networks, a leading mobile, broadband and fiber provider, "
                "on a live phone call.")

    def gender_grammar_rules(self) -> str:
        if self.gender == FEMALE:
            return (
                f"GRAMMATICAL GENDER — ABSOLUTE RULE. {self.name} is a WOMAN. Every "
                "first-person form you speak, in every language, must be feminine, on every "
                "turn, with zero exceptions.\n"
                "- Marathi: मी तपासते, मी बघते, मी मदत करू शकते, मी तक्रार नोंदवते, मी बोलते आहे.\n"
                "- Hindi: मैं देखती हूँ, मैं जाँच करती हूँ, मैं कर सकती हूँ, मैं दर्ज कर रही हूँ, मैं समझती हूँ.\n"
                "- NEVER say about yourself: करतो, शकतो, तपासतो, नोंदवतो, करता हूँ, रहा हूँ, "
                "सकता हूँ, समझ गया.\n"
                "- Forms addressed to the CALLER follow the caller, not you. Switching language "
                "never changes your gender."
            )
        return (
            f"GRAMMATICAL GENDER — ABSOLUTE RULE. {self.name} is a MAN. Every "
            "first-person form you speak, in every language, must be masculine, on every "
            "turn, with zero exceptions.\n"
            "- Marathi: मी तपासतो, मी बघतो, मी मदत करू शकतो, मी तक्रार नोंदवतो, मी बोलतो आहे.\n"
            "- Hindi: मैं देखता हूँ, मैं जाँच करता हूँ, मैं कर सकता हूँ, मैं दर्ज कर रहा हूँ, मैं समझता हूँ.\n"
            "- NEVER say about yourself: करते, शकते, तपासते, नोंदवते, करती हूँ, रही हूँ, "
            "सकती हूँ, समझ गई.\n"
            "- Forms addressed to the CALLER follow the caller, not you. Switching language "
            "never changes your gender."
        )

    # ── validation layer (runs on every line just before TTS) ───────────────
    def enforce_gender(self, text: str, lang: str = "mr") -> str:
        """Deterministically rewrite opposite-gender FIRST-PERSON forms to the
        configured gender. Applied to every spoken line as the last text stage
        before TTS formatting; caller-addressed forms are never touched."""
        if not text:
            return text
        out = text
        # apply both languages' pairs — code-mixed lines can carry either
        for female, male in _PAIRS_HI + _PAIRS_MR:
            if self.gender == MALE:
                out = out.replace(female, male)
            else:
                out = out.replace(male, female)
        if out != text:
            log.info("persona: gender-corrected line (%s persona): %r → %r",
                     self.gender, text[:60], out[:60])
        return out

    def render(self, template: str) -> str:
        """Fill persona placeholders in a prompt template."""
        return (template
                .replace("{{AGENT_NAME}}", self.name)
                .replace("{{AGENT_ROLE}}", self.role)
                .replace("{{GREETING}}", self.greeting)
                .replace("{{GENDER_GRAMMAR}}", self.gender_grammar_rules()))


def _build(name: str, gender: str, role: str, voice: str) -> PersonaContext:
    f = gender == FEMALE
    # Marathi/Hindi first-person forms for the fixed, reviewed lines
    mr_can = "करू शकते" if f else "करू शकतो"
    mr_speaking = "बोलतेय" if f else "बोलतोय"      # "…speaking" — gendered
    hi_can = "कर सकती हूँ" if f else "कर सकता हूँ"
    mr_log = "नोंदवते" if f else "नोंदवतो"
    hi_log = "कर रही हूँ" if f else "कर रहा हूँ"
    mr_block = "करते" if f else "करतो"
    hi_block = "करती हूँ" if f else "करता हूँ"
    hi_taking = "ले रही हूँ" if f else "ले रहा हूँ"
    hi_connect = "जोड़ रही हूँ" if f else "जोड़ रहा हूँ"

    return PersonaContext(
        name=name, gender=gender, role=role, voice=voice,
        greeting=("सिंकब्रॉड नेटवर्क्स ग्राहक सेवेत आपले स्वागत आहे. "
                  f"मी {name}, आपली कशा प्रकारे मदत {mr_can}?"),
        greeting_personal=(f"नमस्कार {{first}}! सिंकब्रॉड नेटवर्क्स ग्राहक सेवेत आपले स्वागत आहे. "
                           f"मी {name} {mr_speaking}, आपली कशा प्रकारे मदत {mr_can}?"),
        apology={
            "mr": "माफ करा, थोडी तांत्रिक अडचण आली. कृपया पुन्हा सांगाल का?",
            "hi": "माफ़ कीजिए, थोड़ी तकनीकी दिक्कत आ गई. कृपया दोबारा बताइए?",
            "en": "Sorry, I hit a small technical issue. Could you say that again?",
        },
        silence_nudge={
            "mr": f"हॅलो, आपण तिथे आहात का? मी आपली कशी मदत {mr_can}?",
            "hi": f"हैलो, क्या आप वहाँ हैं? मैं आपकी कैसे मदद {hi_can}?",
            "en": "Hello, are you still there? How may I help you?",
        },
        no_response_closing={
            "mr": ("तुमच्याकडून कोणतेही प्रतिउत्तर न आल्यामुळे आपला कॉल डिस्कनेक्ट करण्यात येत आहे. "
                   "सिंकब्रॉड नेटवर्क्समध्ये संपर्क केल्याबद्दल धन्यवाद. आपला दिवस शुभ असो."),
            "hi": ("आपकी ओर से कोई प्रति-उत्तर न आने के कारण आपका कॉल डिस्कनेक्ट किया जा रहा है. "
                   "सिंकब्रॉड नेटवर्क्स में संपर्क करने के लिए धन्यवाद. आपका दिन शुभ रहे."),
            "en": ("As there's no response from your side, this call is being disconnected. "
                   "Thank you for calling Syncbroad Networks. Have a nice day."),
        },
        emergency_follow={
            "mr": ("फ्रॉड आणि सुरक्षा टीमला कळवलं आहे, आणि मी आपल्याला वरिष्ठ अधिकाऱ्याशी जोडत आहे. "
                   "नेमकं काय झालं ते थोडक्यात सांगू शकाल का?"),
            "hi": (f"फ्रॉड और सुरक्षा टीम को सूचना दे दी है, और मैं आपको सीनियर अधिकारी से {hi_connect}. "
                   "ठीक-ठीक क्या हुआ, थोड़े में बता दीजिए?"),
            "en": ("Our fraud and security team has been alerted, and I'm connecting you to a "
                   "senior officer. Can you briefly tell me exactly what happened?"),
        },
        safety_generic={
            "mr": ("काळजी करू नका, हे मी गांभीर्याने घेत आहे. कृपया कोणालाही OTP, पासवर्ड किंवा "
                   f"बँक तपशील सांगू नका आणि अनोळखी लिंकवर क्लिक करू नका. मी ही तक्रार लगेच {mr_log}."),
            "hi": (f"चिंता मत कीजिए, मैं इसे गंभीरता से {hi_taking}. कृपया किसी को भी OTP, पासवर्ड या "
                   f"बैंक डिटेल मत बताइए और किसी अनजान लिंक पर क्लिक मत कीजिए. मैं यह शिकायत अभी दर्ज {hi_log}."),
            "en": ("Don't worry — I'm taking this seriously. Please don't share any OTP, password "
                   "or bank details with anyone, and don't click unknown links. I am logging this "
                   "incident immediately."),
        },
        safety_shock={
            "mr": (f"काळजी करू नका. मी आधी हे सिम कार्ड तात्काळ ब्लॉक {mr_block} म्हणजे त्याचा गैरवापर होणार नाही, "
                   f"आणि ही तक्रार लगेच {mr_log}."),
            "hi": (f"चिंता मत कीजिए. सबसे पहले मैं यह सिम तुरंत ब्लॉक {hi_block} ताकि इसका गलत इस्तेमाल न हो, "
                   f"और यह शिकायत अभी दर्ज {hi_log}."),
            "en": ("Don't worry. First, I'm blocking that SIM right away so it can't be misused, "
                   "and I am logging this incident immediately."),
        },
        transfer_intro={
            "mr": ("आपल्या संयमाबद्दल धन्यवाद. सुरुवातीची पडताळणी मी पूर्ण केली आहे. "
                   "या बाबतीत आपल्याला आमच्या वरिष्ठ अधिकाऱ्याची मदत लागेल. आपली संपूर्ण "
                   "माहिती आणि तपशील मी आधीच तयार करून ठेवला आहे, त्यामुळे तुम्हाला पुन्हा "
                   "काही सांगावं लागणार नाही. मी आपला कॉल आता जोडत आहे."),
            "hi": ("आपके धैर्य के लिए धन्यवाद. शुरुआती जाँच मैंने पूरी कर ली है. इस मामले में "
                   "आपको हमारे सीनियर अधिकारी की सहायता चाहिए होगी. आपकी पूरी जानकारी और "
                   "ब्यौरा मैंने पहले ही तैयार कर लिया है, इसलिए आपको दोबारा कुछ बताने की "
                   "ज़रूरत नहीं पड़ेगी. मैं आपका कॉल अभी जोड़ रहा हूँ."),
            "en": ("Thank you for your patience. I've completed the initial verification. "
                   "This issue needs help from one of our senior executives. I've already "
                   "prepared a full summary of your case, so you won't have to explain "
                   "everything again. I'm connecting your call now."),
        },
        transfer_failed={
            "mr": ("क्षमस्व, तांत्रिक अडचणीमुळे मी आत्ता कॉल जोडू शकलो नाही."),
            "hi": ("क्षमा कीजिए, तकनीकी दिक्कत के कारण मैं अभी कॉल नहीं जोड़ पाया."),
            "en": ("I'm sorry — a technical issue meant I couldn't connect the call right now."),
        },
        transfer_callback={
            "mr": ("काळजी करू नका, आपली संपूर्ण माहिती नोंदवली आहे आणि आमचे वरिष्ठ अधिकारी "
                   "आपल्याला याच नंबरवर लवकरच परत कॉल करतील."),
            "hi": ("चिंता मत कीजिए, आपकी पूरी जानकारी दर्ज कर ली है और हमारे सीनियर अधिकारी "
                   "आपको इसी नंबर पर जल्द ही कॉल बैक करेंगे."),
            "en": ("Don't worry — I've saved your full details and our senior executive will "
                   "call you back on this number shortly."),
        },
    )


@lru_cache(maxsize=8)
def _cached(name: str, gender: str, role: str, voice: str) -> PersonaContext:
    persona = _build(name, gender, role, voice)
    log.info("persona: %s (%s), role=%r, default voice=%s", name, gender, role, voice)
    return persona


def get_persona(settings) -> PersonaContext:
    """Resolve the PersonaContext from Settings. Cached: identical config →
    the same immutable instance for the process lifetime."""
    gender = (getattr(settings, "agent_gender", MALE) or MALE).strip().lower()
    if gender not in (MALE, FEMALE):
        log.warning("persona: AGENT_GENDER=%r invalid — defaulting to male", gender)
        gender = MALE
    name = (getattr(settings, "agent_name", "") or "Ratan").strip()
    role = (getattr(settings, "agent_role", "") or "customer care executive").strip()
    voice = (getattr(settings, "tts_speaker", "") or "").strip() or _DEFAULT_VOICE[gender]
    return _cached(name, gender, role, voice)
