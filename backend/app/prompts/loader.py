"""Prompt composer. Modules are numbered .md files — git-versioned, single source of
truth (fixes the workflow.json-vs-paste-files drift of the old build). Composed fresh
each turn with the dynamic blocks (language directive, call memory, knowledge context)."""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)
MODULES_DIR = Path(__file__).parent / "modules"


@lru_cache
def _static_modules() -> str:
    parts = []
    for f in sorted(MODULES_DIR.glob("*.md")):
        parts.append(f.read_text(encoding="utf-8").strip())
    log.info("Loaded %d prompt modules", len(parts))
    return "\n\n".join(parts)


def compose_system_prompt(language_directive: str, memory_block: str,
                          knowledge_block: str = "") -> str:
    sections = [_static_modules(), language_directive, memory_block]
    if knowledge_block:
        sections.append("[KNOWLEDGE CONTEXT — grounded facts for this turn]\n" + knowledge_block)
    return "\n\n".join(s for s in sections if s)
