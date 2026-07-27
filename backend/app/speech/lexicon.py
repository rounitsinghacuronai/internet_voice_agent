"""Language-aware spoken vocabulary.

Each language (Marathi, Hindi, English) gets its OWN acknowledgements, thinking
fillers, empathy lines and small human interjections — never English speech
behaviour translated word-for-word into Marathi. The tables below are the raw
material the Human Speech Engine draws from; the VariationTracker decides which
item is used so the same one never repeats back-to-back.
"""
from __future__ import annotations

import re

# ── active-listening acknowledgements (turn openers) ─────────────────────────
# Rotated so ten callers with the same problem hear ten slightly different,
# equally professional openings.
ACKS: dict[str, list[str]] = {
    "en": ["Alright", "Okay", "I see", "Got it", "Sure", "Right", "Of course", "Thanks for that"],
    "hi": ["जी", "ठीक है", "अच्छा", "समझ गया", "जी बिलकुल", "अच्छा जी"],
    "mr": ["बरं", "ठीक आहे", "हं", "समजलं", "जी", "बरं का"],
}

# ── thinking fillers — used ONLY when a real lookup/tool call is happening ────
# Never faked. The manager sets SpeechContext.processing=True only when a tool
# actually ran this turn.
HESITATIONS: dict[str, list[str]] = {
    "en": ["Let me just check", "One moment", "Let me pull that up", "Let me take a look",
           "Just a second", "Let me see"],
    "hi": ["एक मिनट, देखता हूँ", "ज़रा चेक करता हूँ", "एक सेकंड", "अभी देखता हूँ",
           "ज़रा देखने दीजिए"],
    "mr": ["एक मिनिट, बघतो", "जरा तपासतो", "एक सेकंद", "आत्ता बघतो", "जरा बघू द्या"],
}

# ── TASK-AWARE thinking fillers — which tool is actually running ──────────────
# HESITATIONS above is the generic fallback ("let me check"). A caller who hears
# the SAME generic filler whether the agent is verifying their identity,
# registering a complaint, or preparing a transfer is a clear "this is a bot"
# tell — a human executive says "let me pull up your account" while verifying
# and "I'm registering that now" while filing a ticket, not the same line every
# time. Keyed by TASK BUCKET (not one entry per tool — most tools in a bucket
# want the same kind of filler); task_filler() below maps a tool name to its
# bucket, falling back to HESITATIONS for anything not explicitly mapped so a
# new tool never crashes or goes silent, it just sounds slightly more generic
# until it's added here.
TASK_FILLERS: dict[str, dict[str, list[str]]] = {
    "verification": {
        "en": ["I'm verifying your details", "Let me just verify that",
               "Let me pull up your account", "Verifying that now"],
        "hi": ["मैं आपकी डिटेल्स वेरीफाई कर रहा हूँ", "ज़रा वेरीफाई कर लेता हूँ",
               "आपका अकाउंट देखता हूँ"],
        "mr": ["मी तुमचे डिटेल्स तपासतो", "जरा वेरिफाय करतो", "तुमचं अकाउंट बघतो"],
    },
    "lookup": {
        "en": ["Let me check your connection", "Checking that now",
               "Let me pull that up", "Give me one second to check"],
        "hi": ["आपका कनेक्शन चेक करता हूँ", "अभी देखता हूँ", "एक सेकंड में बताता हूँ"],
        "mr": ["तुमचं कनेक्शन तपासतो", "आत्ता बघतो", "एक सेकंदात सांगतो"],
    },
    "ticket": {
        "en": ["I'm registering your complaint", "Filing that for you now",
               "Registering this right away"],
        "hi": ["मैं आपकी शिकायत दर्ज कर रहा हूँ", "अभी रजिस्टर करता हूँ"],
        "mr": ["मी तुमची तक्रार नोंदवत आहे", "आत्ता नोंदवतो"],
    },
    "knowledge": {
        "en": ["Looking up the relevant information", "Let me find that for you",
               "Checking our records on that"],
        "hi": ["मैं जानकारी देख रहा हूँ", "अभी जानकारी निकालता हूँ"],
        "mr": ["मी माहिती बघत आहे", "आत्ता माहिती काढतो"],
    },
    "escalation": {
        "en": ["I'm preparing your transfer", "Connecting you now, one moment",
               "Setting that up for you"],
        "hi": ["मैं आपका ट्रांसफर तैयार कर रहा हूँ", "अभी कनेक्ट करता हूँ"],
        "mr": ["मी तुमचं ट्रान्सफर तयार करत आहे", "आत्ता कनेक्ट करतो"],
    },
    "engineer": {
        "en": ["Checking engineer availability", "Let me check the visit schedule",
               "Looking at available slots"],
        "hi": ["इंजीनियर की उपलब्धता देखता हूँ", "स्लॉट चेक करता हूँ"],
        "mr": ["इंजिनिअरची उपलब्धता बघतो", "स्लॉट तपासतो"],
    },
    "connection": {
        "en": ["Setting that up now", "Processing that request",
               "Let me get that arranged"],
        "hi": ["अभी सेटअप करता हूँ", "रिक्वेस्ट प्रोसेस करता हूँ"],
        "mr": ["आत्ता सेटअप करतो", "विनंती प्रोसेस करतो"],
    },
}

# Tool name → filler bucket. Anything not listed here falls back to the
# generic HESITATIONS table in task_filler() below.
_TOOL_FILLER_BUCKET: dict[str, str] = {
    "verify_customer": "verification", "send_otp": "verification",
    "verify_otp": "verification",
    "get_plan": "lookup", "get_bill": "lookup", "get_payment_status": "lookup",
    "get_recharge_history": "lookup", "get_usage": "lookup",
    "get_network_status": "lookup", "get_broadband_status": "lookup",
    "run_line_diagnostics": "lookup", "restart_ont": "lookup",
    "get_plan_catalog": "lookup", "track_complaint": "lookup",
    "get_new_connection_status": "lookup",
    "register_complaint": "ticket", "escalate_complaint": "ticket",
    "log_priority_incident": "ticket",
    "search_knowledge": "knowledge",
    "transfer_to_senior_executive": "escalation",
    "schedule_engineer_visit": "engineer",
    "register_new_connection": "connection", "request_sim_swap": "connection",
    "request_plan_change": "connection", "block_sim": "connection",
}


def filler_bucket_name(tool_names: list[str]) -> str:
    """The task bucket for the tool(s) about to run, or 'generic' if none of
    them are mapped. Exposed separately (not just inside task_filler) so the
    caller can key its anti-repetition rotation per-bucket — verification
    fillers and ticket fillers should rotate independently, not share one
    'never repeat' pool with every other task."""
    for name in tool_names or []:
        bucket = _TOOL_FILLER_BUCKET.get(name)
        if bucket:
            return bucket
    return "generic"


def task_filler(tool_names: list[str], lang: str) -> list[str]:
    """Pick the filler table for the tool(s) about to run. Uses the FIRST tool
    name (a round is almost always one primary action); unmapped/unknown tools
    fall back to the generic HESITATIONS table so this never goes silent or
    errors on a new/uncatalogued tool."""
    bucket = filler_bucket_name(tool_names)
    table = TASK_FILLERS.get(bucket)
    return lang_table(table, lang) if table else lang_table(HESITATIONS, lang)


# ── empathy — one per issue, never per sentence, and varied ───────────────────
EMPATHY: dict[str, list[str]] = {
    "en": ["I'm sorry you've had to deal with that", "That must be frustrating",
           "I understand how that feels", "Let's get this sorted", "I know that's a hassle"],
    "hi": ["समझ सकता हूँ, परेशानी हुई होगी", "माफ़ कीजिए आपको तकलीफ़ हुई",
           "चिंता मत कीजिए, मैं देखता हूँ", "मैं इसे ठीक करवाता हूँ"],
    "mr": ["मला कल्पना आहे किती त्रास होतो", "माफ करा, तुम्हाला त्रास झाला",
           "काळजी करू नका, मी बघतो", "हे मी लगेच सोडवतो"],
}

# ── small human reactions — let feeling show without announcing it ────────────
INTERJECTIONS: dict[str, list[str]] = {
    "en": ["Oh no", "Oh", "Ah"],
    "hi": ["अरे", "ओह", "अच्छा"],
    "mr": ["अरेरे", "अरे", "ओह"],
}

# ── warm confirmation openers (action completed) ─────────────────────────────
CONFIRMATIONS: dict[str, list[str]] = {
    "en": ["Done", "That's taken care of", "There we go", "All set"],
    "hi": ["हो गया जी", "हो गया", "बस हो गया"],
    "mr": ["झालं", "झालं बरं का", "बस झालं"],
}

# ── connectors that make clauses flow like one breath (available to formatter) ─
CONNECTORS: dict[str, list[str]] = {
    "en": ["so", "and", "because"],
    "hi": ["तो", "और", "क्योंकि"],
    "mr": ["म्हणून", "तर", "आणि", "पण"],
}

# ── reassurance tails used by reassuring/worried delivery ─────────────────────
REASSURE: dict[str, list[str]] = {
    "en": ["don't worry", "we'll sort this out"],
    "hi": ["चिंता मत कीजिए", "हम देख लेंगे"],
    "mr": ["काळजी करू नका", "आपण बघू"],
}


# ── AI / documentation phrasings → natural spoken alternatives ────────────────
# These are the tells that make a reply sound like text being read. Each entry
# is (compiled pattern, {lang: replacement}). A replacement of "" deletes the
# phrase (and the engine cleans up the leftover spacing/leading connective).
# Applied case-insensitively; only clearly-safe rewrites that never touch a
# number, name, amount, or factual claim.
_AI_PATTERNS_RAW: list[tuple[str, dict[str, str]]] = [
    (r"\bI (?:completely |totally |fully )?understand\b",
     {"en": "I see", "hi": "अच्छा", "mr": "समजलं"}),
    (r"\bI (?:sincerely |deeply )?apolog(?:ise|ize)\b",
     {"en": "I'm sorry", "hi": "माफ़ कीजिए", "mr": "माफ करा"}),
    (r"\bI (?:would like to|want to) apolog(?:ise|ize)\b",
     {"en": "I'm sorry", "hi": "माफ़ कीजिए", "mr": "माफ करा"}),
    (r"\bI will now\b", {"en": "Let me", "hi": "मैं अभी", "mr": "मी आत्ता"}),
    (r"\bI shall now\b", {"en": "Let me", "hi": "मैं अभी", "mr": "मी आत्ता"}),
    (r"\bThe next step is to\b", {"en": "Next,", "hi": "अब", "mr": "आता"}),
    (r"\bThe next step\b", {"en": "Next", "hi": "अब", "mr": "आता"}),
    (r"\bPlease note that\b", {"en": "Just so you know,", "hi": "बस बता दूँ,", "mr": "एवढं सांगतो,"}),
    (r"\bPlease note\b", {"en": "Just so you know", "hi": "बस बता दूँ", "mr": "एवढं सांगतो"}),
    (r"\bPlease be advised that\b", {"en": "", "hi": "", "mr": ""}),
    (r"\bAs an AI(?: language model)?,?\b", {"en": "", "hi": "", "mr": ""}),
    (r"\bI'm an AI(?: assistant)?,?\b", {"en": "", "hi": "", "mr": ""}),
    (r"\bThis process (?:will|may)\b", {"en": "This", "hi": "यह", "mr": "हे"}),
    (r"\bKindly\b", {"en": "Please", "hi": "कृपया", "mr": "कृपया"}),
    (r"\bIn order to\b", {"en": "To", "hi": "", "mr": ""}),
    (r"\bhas been successfully (registered|processed|completed|updated)\b",
     {"en": r"is \1", "hi": "हो गया", "mr": "झालं"}),
    (r"\bI have (registered|processed|completed|verified|updated|noted)\b",
     {"en": r"I've \1", "hi": "मैंने कर दिया", "mr": "मी केलं"}),
]

AI_PATTERNS: list[tuple[re.Pattern, dict[str, str]]] = [
    (re.compile(p, re.IGNORECASE), r) for p, r in _AI_PATTERNS_RAW
]

# ── English written→spoken contractions (safe, never touches digits/names) ────
# Makes English delivery sound spoken rather than written. Marathi/Hindi keep
# their own natural spoken forms via the LLM + tables above.
_SPOKEN_FORMS_RAW: list[tuple[str, str]] = [
    (r"\bcannot\b", "can't"),
    (r"\bdo not\b", "don't"),
    (r"\bdoes not\b", "doesn't"),
    (r"\bdid not\b", "didn't"),
    (r"\bwill not\b", "won't"),
    (r"\bis not\b", "isn't"),
    (r"\bare not\b", "aren't"),
    (r"\bwas not\b", "wasn't"),
    (r"\bit is\b", "it's"),
    (r"\bthat is\b", "that's"),
    (r"\bthere is\b", "there's"),
    (r"\bwe will\b", "we'll"),
    (r"\byou will\b", "you'll"),
    (r"\bI will\b", "I'll"),
    (r"\bI have\b", "I've"),
    (r"\bwe have\b", "we've"),
    (r"\byou have\b", "you've"),
    (r"\blet us\b", "let's"),
]

SPOKEN_FORMS: list[tuple[re.Pattern, str]] = [
    (re.compile(p, re.IGNORECASE), r) for p, r in _SPOKEN_FORMS_RAW
]


def lang_table(table: dict[str, list[str]], lang: str) -> list[str]:
    """Fetch a language's list from a table, defaulting to Marathi."""
    return table.get(lang) or table.get("mr") or []
