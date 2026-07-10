"""Anti-repetition rotation.

Humans don't reuse the exact same acknowledgement, filler, or sentence twice in
a row. This tracker lives on the SpeechDirector (one per call) and remembers
what was recently used so:

  • the same acknowledgement never fires back-to-back,
  • the engine can detect and down-weight a whole spoken line it already said,
  • ten callers with the same problem get ten slightly different deliveries.

Deterministic given a seed so tests are reproducible; seeded from the item list
+ recent history so it still feels varied in production.
"""
from __future__ import annotations

import hashlib
from collections import deque


class VariationTracker:
    def __init__(self, memory: int = 6):
        # recently emitted picks, per category key (e.g. "ack:en")
        self._recent: dict[str, deque[str]] = {}
        # fingerprints of whole lines already spoken this call
        self._said: deque[str] = deque(maxlen=40)
        self._memory = memory
        self._counter = 0

    def pick(self, category: str, options: list[str]) -> str:
        """Return an option that wasn't used recently for this category.

        Rotates deterministically but call-position-dependent, so consecutive
        picks differ and the sequence varies across calls."""
        if not options:
            return ""
        recent = self._recent.setdefault(category, deque(maxlen=self._memory))
        fresh = [o for o in options if o not in recent]
        pool = fresh or options
        self._counter += 1
        # position-dependent, stable index — not random, but never the same
        # element twice while fresh alternatives exist.
        idx = (self._counter + len(recent)) % len(pool)
        choice = pool[idx]
        recent.append(choice)
        return choice

    # ── whole-line repetition guard ──────────────────────────────────────────
    @staticmethod
    def _fingerprint(text: str) -> str:
        norm = "".join(ch.lower() for ch in text if ch.isalnum())
        return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]

    def seen_before(self, text: str) -> bool:
        return self._fingerprint(text) in self._said

    def remember(self, text: str) -> None:
        fp = self._fingerprint(text)
        if fp not in self._said:
            self._said.append(fp)
