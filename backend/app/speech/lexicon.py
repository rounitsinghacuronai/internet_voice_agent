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
