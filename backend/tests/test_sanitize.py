"""_sanitize() — TTS-bound text cleanup. Regression test for a real production bug:
the LLM wrote a complaint number phonetically, then repeated it in parentheses in
its raw written form (e.g. "...SR two six zero... (SR260782D4E6)"), and the TTS
read both, so the caller heard the same number twice in one breath."""

from __future__ import annotations

from backend.app.conversation.manager import _sanitize


def test_strips_parenthetical_number_repeat():
    text = "इसका शिकायत क्रमांक है एसआर260782डी4ई6 (SR260782D4E6). और कोई मदद चाहिए?"
    out = _sanitize(text)
    assert "SR260782D4E6" not in out
    assert "(" not in out and ")" not in out
    assert "एसआर260782डी4ई6" in out  # the actual spoken form survives


def test_strips_parenthetical_aside_generic():
    assert (
        _sanitize("Your bill is two thousand (approx.) rupees.")
        == "Your bill is two thousand rupees."
    )


def test_still_strips_markdown_and_bullets():
    assert _sanitize("**Hello** there") == "Hello there"
    assert _sanitize("- first\n- second") == "first second"


def test_no_parentheses_untouched():
    text = "Your bill is two thousand three forty, due on the fifteenth."
    assert _sanitize(text) == text
