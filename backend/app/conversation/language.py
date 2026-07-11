"""Language Adaptation Engine — deterministic core, prompt does the styling.

The old build's failure class ("says it will switch but stays in Marathi") was fixed
with a proxy regex hack. Here the engine owns language state:

  • detect per utterance: STT language hint > explicit command > script+lexicon
  • explicit request ("English please") = COMMAND → switch + pin until next command
  • otherwise mirror the caller with hysteresis (one stray word never flips the call)
  • output: a one-line directive injected into the system prompt each turn

Code-mixing is handled by the model (prompt says "mirror the blend"); the engine only
pins the BASE language so replies never jump unexpectedly."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# explicit language commands (caller names a language)
_COMMANDS: dict[str, list[str]] = {
    "en": [r"\benglish\b.{0,20}(please|me[in]|मध्ये|बोल|talk|speak)?",
           r"(talk|speak|बोल|बात).{0,15}english", r"english\s*(madhe|mein|me)\b"],
    "hi": [r"\bhindi\b.{0,20}(please|me[in]|बोल|talk|speak)?", r"हिन्दी|हिंदी",
           r"(talk|speak|बोल|बात).{0,15}hindi"],
    "mr": [r"\bmarathi\b.{0,20}(please|madhe|बोल|talk|speak)?", r"मराठी",
           r"(talk|speak|बोल|बात).{0,15}marathi"],
}

# Lexical markers separating Hindi vs Marathi (both Devanagari). These matter
# more than the STT hint — Sarvam regularly labels Marathi as hi-IN and vice
# versa, which was the root cause of the agent mixing the two mid-call.
# Substring-matched, so entries must not occur inside the OTHER language's
# common words (e.g. bare "आप" was removed from Hindi: it sits inside Marathi
# "आपण/आपले"; bare "तुम" removed from Marathi: it IS a Hindi word).
_MR_MARKERS = ["आहे", "आहेत", "नाही", "का?", "मला", "माझ", "तुमच", "तुम्ही", "आपण",
               "झाल", "करा", "करतो", "मध्ये", "कसे", "कशी", "काय", "वीज", "गेली",
               "आलं", "आलाय", "पाहिजे", "बोलत", "मी ", "जास्त", "खूप", "सांग",
               "द्या", "होय", "बरं", "करू", "येत"]
_HI_MARKERS = ["है", "हैं", "नहीं", "मुझे", "मेरा", "मेरी", "मेरे", "आपका", "आपको",
               "आपकी", "आपसे", "हुआ", "हुई", "करो", "कीजिए", "दीजिए", "में", "कैसे",
               "क्या", "गया", "गई", "चाहिए", "रहा", "रही", "रहे", "बिजली", "बहुत",
               "ज्यादा", "ज़्यादा", "अभी", "बता", "हो गया", "कर दो"]

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_LATIN = re.compile(r"[A-Za-z]")

# romanized Hindi/Marathi markers (codemix STT often outputs Latin script)
_ROM_HI = ["nahi", "hai", "mera", "bijli", "aa rahi", "gaya", "kyu", "zyada", "bahut", "karo",
           "chahiye", "kitna", "paisa", "bhai", "haan", "theek"]
_ROM_MR = ["aahe", "nahi ye", "majha", "mazha", "vij", "geli", "kiti", "pahije", "zala",
           "karaycha", "ho ka", "barobar", "madhe"]


_ASK_TURNS = 3   # consecutive indeterminate turns before we ever ask outright


@dataclass
class LanguageEngine:
    language: str = "und"          # active base language: mr|hi|en|und
    pinned: bool = False           # true after explicit command
    _streak: dict = field(default_factory=dict)   # candidate → consecutive turns
    _und_turns: int = 0            # consecutive turns we truly could not detect anything

    def update(self, text: str, stt_hint: str = "unknown") -> str:
        """Call once per user utterance. Returns the active language."""
        cmd = self._command(text)
        if cmd:
            if cmd != self.language:
                log.info("language COMMAND → %s", cmd)
            self.language = cmd
            self.pinned = True
            self._streak.clear()
            self._und_turns = 0
            return self.language

        detected = self._detect(text, stt_hint)
        if detected == "und":
            if self.language == "und":
                self._und_turns += 1
            return self.language
        self._und_turns = 0
        if self.language == "und":
            self.language = detected            # first real utterance sets the base
            return self.language
        if detected != self.language:
            # STRONG signal (whole utterance unambiguously in the other language)
            # → follow the caller in the SAME turn. Waiting two turns here meant
            # the agent audibly answered in the wrong language right after the
            # caller had clearly switched — an instant credibility killer.
            # A pinned language (explicit command) stays stickier: it needs two
            # consecutive strong turns before drifting.
            if self._is_strong(text, detected, stt_hint):
                self._streak[detected] = self._streak.get(detected, 0) + 1
                self._streak = {detected: self._streak[detected]}
                need = 2 if self.pinned else 1
                if self._streak[detected] >= need:
                    log.info("language switch (strong) %s → %s", self.language, detected)
                    self.language = detected
                    self.pinned = False
                    self._streak.clear()
                return self.language
            # WEAK/ambiguous signal (stray word, garbled STT) → old hysteresis:
            # 2 consecutive turns (3 if pinned) before following.
            self._streak[detected] = self._streak.get(detected, 0) + 1
            self._streak = {detected: self._streak[detected]}
            need = 3 if self.pinned else 2      # pinned language is stickier
            if self._streak[detected] >= need:
                log.info("language drift %s → %s", self.language, detected)
                self.language = detected
                self.pinned = False
                self._streak.clear()
        else:
            self._streak.clear()
        return self.language

    def _is_strong(self, text: str, detected: str, stt_hint: str) -> bool:
        """Is the whole utterance unambiguously in `detected`? Only then is a
        same-turn switch justified; one stray token never qualifies. Between
        Hindi and Marathi — the two languages STT confuses — the bar is much
        higher, so a mislabelled utterance can't flip the call and cause the
        agent to alternate between them."""
        low = text.lower()
        confusable = {self.language, detected} == {"hi", "mr"}
        if detected in ("hi", "mr"):
            if _DEVANAGARI.search(text):
                mr = sum(text.count(m) for m in _MR_MARKERS)
                hi = sum(text.count(m) for m in _HI_MARKERS)
                mine, other = (mr, hi) if detected == "mr" else (hi, mr)
                if confusable:
                    return mine >= 3 and mine >= 2 * max(other, 1)
                return mine >= 2 and mine > other
            rom = _ROM_MR if detected == "mr" else _ROM_HI
            return sum(m in low for m in rom) >= (3 if confusable else 2)
        if detected == "en":
            if _DEVANAGARI.search(text):
                return False
            hint = (stt_hint or "").lower()
            if hint.startswith(("hi", "mr")):
                return False                      # STT disagrees → not clear-cut
            words = re.findall(r"[A-Za-z]+", text)
            rom_hits = sum(m in low for m in _ROM_HI) + sum(m in low for m in _ROM_MR)
            return len(words) >= 4 and rom_hits == 0
        return False

    def directive(self) -> str:
        """One line for the system prompt. Deterministic, per turn."""
        name = {"mr": "Marathi", "hi": "Hindi", "en": "English"}.get(self.language)
        if not name:
            if self._und_turns >= _ASK_TURNS:
                return ("ACTIVE LANGUAGE: still not known after several turns. Ask once, "
                        "briefly and politely, which language they'd prefer — Marathi, "
                        "Hindi or English — then follow whatever they choose.")
            return ("ACTIVE LANGUAGE: not yet known. Open neutrally; adapt to whatever "
                    "language the caller uses first. Do not ask them to choose yet.")
        rule = ("The caller explicitly chose this language — every word of your reply "
                "must be in it until they ask otherwise."
                if self.pinned else
                "Reply ENTIRELY in this language. Everyday English loanwords the caller "
                "themselves uses (bill, light, meter, complaint) are fine inside it — "
                "but NEVER blend Hindi and Marathi: a Marathi reply contains zero Hindi "
                "words or grammar (no है/नहीं/करो/मेरा), a Hindi reply contains zero "
                "Marathi (no आहे/नाही/करा/माझा). Never jump languages on your own.")
        return f"ACTIVE LANGUAGE: {name}. {rule}"

    # ── internals ────────────────────────────────────────────────────────────
    @staticmethod
    def _command(text: str) -> str | None:
        low = text.lower()
        for lang, patterns in _COMMANDS.items():
            if any(re.search(p, low) for p in patterns):
                # avoid false trigger when caller merely code-mixes the word "english"
                if lang == "en" and not re.search(r"english", low):
                    continue
                return lang
        return None

    @staticmethod
    def _detect(text: str, stt_hint: str) -> str:
        hint = (stt_hint or "").lower()
        # Hindi/Marathi hints are NOT trusted blindly — Sarvam mislabels these
        # two constantly (same script). The WORDS decide; the hint only breaks
        # ties. This was the root cause of hi/mr mixing mid-call.
        if hint.startswith(("mr", "hi")) and _DEVANAGARI.search(text):
            mr = sum(text.count(m) for m in _MR_MARKERS)
            hi = sum(text.count(m) for m in _HI_MARKERS)
            if mr > hi:
                return "mr"
            if hi > mr:
                return "hi"
            return "mr" if hint.startswith("mr") else "hi"   # tie → trust hint
        if hint.startswith("mr"):
            return "mr"
        if hint.startswith("hi"):
            return "hi"
        if hint.startswith("en"):
            # STT says English but script may disagree; verify below
            if not _DEVANAGARI.search(text):
                low = text.lower()
                if sum(m in low for m in _ROM_MR) >= 2:
                    return "mr"
                if sum(m in low for m in _ROM_HI) >= 2:
                    return "hi"
                return "en"
        if _DEVANAGARI.search(text):
            mr = sum(text.count(m) for m in _MR_MARKERS)
            hi = sum(text.count(m) for m in _HI_MARKERS)
            if mr > hi:
                return "mr"
            if hi > mr:
                return "hi"
            return "hi" if hint.startswith("hi") else ("mr" if hint.startswith("mr") else "und")
        if _LATIN.search(text):
            low = text.lower()
            mr = sum(m in low for m in _ROM_MR)
            hi = sum(m in low for m in _ROM_HI)
            if mr >= 2 and mr > hi:
                return "mr"
            if hi >= 2 and hi > mr:
                return "hi"
            return "en"
        return "und"
