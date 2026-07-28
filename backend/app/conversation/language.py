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

from .numbers import looks_like_number_fragment

log = logging.getLogger(__name__)

# explicit language commands (caller names a language). The trailing cue
# group is MANDATORY (no trailing "?") — a bare, unguarded language name
# used to be enough to trigger an instant switch+pin on its own, which
# false-triggered on any incidental mention. _command() below additionally
# allows a bare name through when the WHOLE utterance is short (a caller
# answering "Hindi" or "मराठी" to "which language would you prefer?" has no
# verb to pair with), and screens every match for a nearby negation first.
_COMMANDS: dict[str, list[str]] = {
    "en": [r"\benglish\b.{0,20}(please|me[in]|मध्ये|बोल|talk|speak)",
           r"(talk|speak|बोल|बात).{0,15}english", r"english\s*(madhe|mein|me)\b"],
    "hi": [r"\bhindi\b.{0,20}(please|me[in]|बोल|talk|speak)",
           r"(talk|speak|बोल|बात).{0,15}hindi",
           r"(हिन्दी|हिंदी).{0,15}(बोल|बात|कर)", r"(बोल|बात|कर).{0,15}(हिन्दी|हिंदी)"],
    "mr": [r"\bmarathi\b.{0,20}(please|madhe|बोल|talk|speak)",
           r"(talk|speak|बोल|बात).{0,15}marathi",
           r"(मराठी).{0,15}(बोल|बात|कर)", r"(बोल|बात|कर).{0,15}(मराठी)"],
}
# Bare language-name mentions (no verb needed) — only trusted when they are
# essentially the WHOLE reply (see _SHORT_UTTERANCE_WORDS in _command), e.g.
# a one-word answer to "which language would you prefer?".
_BARE_NAME: dict[str, list[str]] = {
    "en": [r"\benglish\b"],
    "hi": [r"\bhindi\b", r"हिन्दी", r"हिंदी"],
    "mr": [r"\bmarathi\b", r"मराठी"],
}
# "don't speak Marathi", "मराठी मत बोलो", "not Hindi" — a language name
# appearing NEGATED is a request to AVOID it, the opposite of a command to
# switch to it. Real production bug this fixes: a caller saying "मराठी मत
# बोलो" (don't speak Marathi) was matched as a bare "मराठी" command and
# switched the call INTO Marathi — exactly backwards from what was asked.
#
# Devanagari terms are SUBSTRING-matched, no \b — Python's \w (and therefore
# \b) does not treat Devanagari vowel signs/anusvara (matras — ी ं etc.) as
# word characters, so \bनहीं\b silently fails to match "नहीं" at all when it
# sits between spaces (verified: re.search(r"\bनहीं\b", "मुझे मराठी नहीं
# आती") is None). Every other Devanagari list in this module (_MR_MARKERS,
# _HI_MARKERS) already avoids \b for exactly this reason; _NEGATION was the
# one place that didn't, and its Devanagari matches were silently no-ops
# until this was found and fixed. "ना" is deliberately excluded — as a bare
# 2-character substring it hits inside ordinary verb forms (जाना, आना,
# करना...) far more often than it's actually a negation.
_NEGATION_DEVANAGARI = ("नहीं", "नाही", "मत", "नको")
_NEGATION_LATIN = re.compile(r"\b(not|no|don'?t)\b")


def _has_negation(window: str) -> bool:
    return any(n in window for n in _NEGATION_DEVANAGARI) or bool(_NEGATION_LATIN.search(window))
_SHORT_UTTERANCE_WORDS = 4

# Lexical markers separating Hindi vs Marathi (both Devanagari). These matter
# more than the STT hint — Sarvam regularly labels Marathi as hi-IN and vice
# versa, which was the root cause of the agent mixing the two mid-call.
# Substring-matched, so entries must not occur inside the OTHER language's
# common words (e.g. bare "आप" was removed from Hindi: it sits inside Marathi
# "आपण/आपले"; bare "तुम" removed from Marathi: it IS a Hindi word).
_MR_MARKERS = ["आहे", "आहेत", "नाही", "का?", "मला", "माझ", "तुमच", "तुम्ही", "आपण",
               "झाल", "करा", "करतो", "मध्ये", "कसे", "कशी", "काय", "नेटवर्क नाही", "गेली",
               "आलं", "आलाय", "पाहिजे", "बोलत", "मी ", "जास्त", "खूप", "सांग",
               "द्या", "होय", "बरं", "करू", "येत"]
_HI_MARKERS = ["है", "हैं", "नहीं", "मुझे", "मेरा", "मेरी", "मेरे", "आपका", "आपको",
               "आपकी", "आपसे", "हुआ", "हुई", "करो", "कीजिए", "दीजिए", "में", "कैसे",
               "क्या", "गया", "गई", "चाहिए", "रहा", "रही", "रहे", "क्यों", "बहुत",
               "ज्यादा", "ज़्यादा", "अभी", "बता", "हो गया", "कर दो"]

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_LATIN = re.compile(r"[A-Za-z]")

# romanized Hindi/Marathi markers (codemix STT often outputs Latin script)
_ROM_HI = ["nahi", "hai", "mera", "matlab", "aa rahi", "gaya", "kyu", "zyada", "bahut", "karo",
           "chahiye", "kitna", "paisa", "bhai", "haan", "theek"]
_ROM_MR = ["aahe", "nahi ye", "majha", "mazha", "kasa", "geli", "kiti", "pahije", "zala",
           "karaycha", "ho ka", "barobar", "madhe"]

# Universal cross-lingual pleasantries/interjections — a Hindi or Marathi
# speaker says these in Latin script constantly WITHOUT meaning to switch
# language (real production case: a caller mid-Hindi-conversation said
# "Hello" and "Thank you" as pure politeness, and each one got counted as a
# confident "en" vote, drifting the whole call's language after just two of
# them). None of these carry real language signal, so _detect() treats them
# as "und" (no vote) rather than "en" — they must not move the streak at all.
_NEUTRAL_FILLERS = {
    "hello", "hi", "hey", "hmm", "hm", "mm", "mhm", "uh", "um", "uhh", "umm",
    "ok", "okay", "yes", "no", "bye", "thanks", "thank you", "thankyou",
}
# Single-word subset — also catches a REPEATED filler ("hello hello hello",
# real production case: STT echoes a word a few times while the caller
# pauses or the line briefly hangs). The whole-string check above only
# matches ONE bare occurrence, not several run together.
_NEUTRAL_FILLER_WORDS = {f for f in _NEUTRAL_FILLERS if " " not in f}


_ASK_TURNS = 3   # consecutive indeterminate turns before we ever ask outright


@dataclass
class LanguageEngine:
    language: str = "und"          # active base language: mr|hi|en|und
    pinned: bool = False           # true after explicit command
    _streak: dict = field(default_factory=dict)   # candidate → consecutive turns
    _und_turns: int = 0            # consecutive turns we truly could not detect anything

    def update(self, text: str, stt_hint: str = "unknown",
              suppress_weak: bool = False) -> str:
        """Call once per user utterance. Returns the active language.

        `suppress_weak`: pass True while a number capture is actively in
        progress (CallMemory.number_buffer.active). An utterance that
        reaches here at all despite that already failed the Number
        Recognition Engine's own "looks like a digit fragment" check (that
        path never calls into run_turn()/this method — see ws_voice.py) —
        so if it STILL doesn't carry a clear, strong language signal, it is
        overwhelmingly likely to be more STT noise from the same
        digit-reading (a hallucinated stray word from mumbling/a pause),
        not a genuine language statement. Real production case: mid PIN-
        code capture, 'Eight' then 'charging' each scored a weak vote and,
        together, incorrectly drifted the whole call from Hindi to English.
        With this on, only a STRONG signal (an unambiguous whole-utterance
        switch, or an explicit command via _command()) can move the
        language while capture is in progress; weak/ambiguous votes are
        dropped instead of accumulating toward a switch."""
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
            # 2 consecutive turns (3 if pinned) before following. Mid-number-
            # capture, don't even let it accumulate — see suppress_weak above.
            if suppress_weak:
                return self.language
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
        switch_now = ("If your PREVIOUS replies were in a different language, SWITCH "
                      "to this one starting with your very next word — do not stay in "
                      "the old language just because the conversation began in it. ")
        rule = ("The caller explicitly chose this language — every word of your reply "
                "must be in it until they ask otherwise. " + switch_now
                if self.pinned else
                switch_now +
                "Reply ENTIRELY in this language. Everyday English loanwords the caller "
                "themselves uses (bill, recharge, network, data) are fine inside it — "
                "but NEVER blend Hindi and Marathi: a Marathi reply contains zero Hindi "
                "words or grammar (no है/नहीं/करो/मेरा), a Hindi reply contains zero "
                "Marathi (no आहे/नाही/करा/माझा). Never jump languages on your own.")
        return f"ACTIVE LANGUAGE: {name}. {rule}"

    # ── internals ────────────────────────────────────────────────────────────
    @staticmethod
    def _command(text: str) -> str | None:
        low = text.lower()
        is_short = len(low.split()) <= _SHORT_UTTERANCE_WORDS

        def _negated(match: re.Match) -> bool:
            window = low[max(0, match.start() - 15):match.end() + 15]
            return _has_negation(window)

        for lang, patterns in _COMMANDS.items():
            for p in patterns:
                m = re.search(p, low)
                if m and not _negated(m):
                    return lang
        # No verb-paired match — allow a BARE language name through only when
        # the reply is short enough that the name plausibly IS the whole
        # answer (e.g. "Hindi", "मराठी", "Marathi please" already handled
        # above with "please"; this covers the bare one-word case).
        if is_short:
            for lang, patterns in _BARE_NAME.items():
                for p in patterns:
                    m = re.search(p, low)
                    if m and not _negated(m):
                        return lang
        return None

    @staticmethod
    def _detect(text: str, stt_hint: str) -> str:
        # Empty transcript (silence/noise the STT gave up on) or a bare
        # cross-lingual pleasantry carries NO language signal — never vote,
        # regardless of what STT's language hint claims (an empty STT result
        # was observed tagged hint="en-IN", which would otherwise have been
        # read as a confident "caller is speaking English").
        stripped = re.sub(r"[^\w\s]", "", text.strip().lower())
        if not stripped or stripped in _NEUTRAL_FILLERS:
            return "und"
        words = stripped.split()
        if words and all(w in _NEUTRAL_FILLER_WORDS for w in words):
            return "und"
        # A bare spoken NUMBER ("eight", "double three", "आठ") carries no
        # language signal — it's a digit, not a language choice. Real
        # production bug this fixes: a caller reading their PIN code
        # digit-by-digit said "Eight" (STT, hint=unknown) between longer
        # utterances; with nothing else to go on this fell through to the
        # Latin-script branch below and scored a full confident "en" vote,
        # priming the hysteresis streak that a SECOND unrelated garbled word
        # ("charging" — almost certainly more STT noise from the same
        # digit-reading, not real speech) then tipped over, incorrectly
        # drifting the whole call from Hindi to English mid pincode-capture.
        if looks_like_number_fragment(text):
            return "und"
        hint = (stt_hint or "").lower()

        # Devanagari script → Hindi vs Marathi is decided ENTIRELY by marker
        # words, NEVER by STT's hi/mr hint, including as a tie-breaker.
        # Sarvam "regularly labels Marathi as hi-IN and vice versa" (see
        # module docstring) — trusting that hint on a tie was gambling with a
        # signal the code's own comments say is unreliable. A genuine tie
        # (0-0 included, e.g. a short reply with no distinguishing word)
        # casts NO vote and leaves the active language exactly where it was,
        # rather than risking a switch on a coin-flip.
        if _DEVANAGARI.search(text):
            mr = sum(text.count(m) for m in _MR_MARKERS)
            hi = sum(text.count(m) for m in _HI_MARKERS)
            if mr > hi:
                return "mr"
            if hi > mr:
                return "hi"
            return "und"

        # Latin script: romanized Hindi/Marathi markers are checked FIRST,
        # regardless of what the hint says — the old code trusted a bare
        # hint="mr"/"hi" here with NO romanized-marker check at all (only the
        # hint="en" path verified against romanized markers), so romanized
        # Hindi/Marathi mislabelled "mr-IN"/"hi-IN" by Sarvam went straight
        # through unchecked. Only once markers are silent does the hint speak
        # — and only for "en", which isn't systematically confused with
        # anything; an hi/mr hint with zero supporting markers still casts no
        # vote, same reasoning as the Devanagari tie above.
        if _LATIN.search(text):
            low = text.lower()
            mr = sum(m in low for m in _ROM_MR)
            hi = sum(m in low for m in _ROM_HI)
            if mr >= 2 and mr > hi:
                return "mr"
            if hi >= 2 and hi > mr:
                return "hi"
            if hint.startswith(("hi", "mr")):
                return "und"
            return "en"

        return "und"
