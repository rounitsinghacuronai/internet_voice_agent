"""Prompt composer. Modules are numbered .md files — git-versioned, single source of
truth (fixes the workflow.json-vs-paste-files drift of the old build). Composed fresh
each turn with the dynamic blocks (language directive, call memory, knowledge context).

PERSONA INTEGRATION: the .md modules contain ZERO hardcoded identity. They use
placeholders — {{AGENT_NAME}}, {{AGENT_ROLE}}, {{GREETING}}, {{GENDER_GRAMMAR}} —
rendered from the session's PersonaContext (backend/app/persona.py). Rendered
output is cached per persona, so per-turn cost is a dict lookup."""
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


# Rendered (persona-filled) modules, cached per persona instance. PersonaContext
# is frozen/hashable, and one process serves one configuration, so this holds a
# single entry in practice.
_RENDER_CACHE: dict[object, str] = {}


def _rendered_modules(persona) -> str:
    cached = _RENDER_CACHE.get(persona)
    if cached is None:
        cached = persona.render(_static_modules())
        _RENDER_CACHE[persona] = cached
    return cached


def compose_system_prompt(language_directive: str, memory_block: str,
                          knowledge_block: str = "", confidence_directive: str = "",
                          persona=None) -> str:
    static = _rendered_modules(persona) if persona is not None else _static_modules()
    sections = [static, language_directive, memory_block]
    if confidence_directive:
        sections.append(confidence_directive)
    if knowledge_block:
        sections.append("[KNOWLEDGE CONTEXT — grounded facts for this turn]\n" + knowledge_block)
    return "\n\n".join(s for s in sections if s)
