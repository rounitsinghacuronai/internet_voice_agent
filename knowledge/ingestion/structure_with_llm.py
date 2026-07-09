"""Stage 2 of knowledge ingestion: extracted text → structured YAML knowledge articles,
using Gemini Pro (quality over latency — this is offline).

The shipped knowledge/articles/*.yaml were authored from the CCCC Training Manual and
safety documents. Run this script to ingest NEW documents (circulars, tariff orders):

  GEMINI_API_KEY=... python knowledge/ingestion/structure_with_llm.py extracted/newdoc.txt

Each article it proposes lands in knowledge/articles/_proposed/ for HUMAN REVIEW before
being moved into articles/ — knowledge that reaches callers is always human-approved.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx

MODEL = os.environ.get("GEMINI_INGEST_MODEL", "gemini-2.5-pro")
BASE = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"

PROMPT = """You are a knowledge engineer for MSEDCL's voice customer-care AI.
Convert the raw document text below into structured YAML knowledge articles.

Rules:
- Each article: id, title, category (billing|safety|complaints|connections|general),
  kind (article|sop|faq|decision_tree|policy|script), source (doc name + page),
  version, language, sections (title, keywords list, text).
- One section = one self-contained concept a call-center agent could speak from.
- Keep every number, form name, time limit and helpline EXACTLY as in the source.
- Write section text as clear factual prose (no bullets), 40-120 words.
- Skip org-chart trivia, awards, and anything a caller would never ask.

Output ONLY the YAML documents separated by '---'.

DOCUMENT ({name}):
{text}
"""


def main(path: Path) -> None:
    key = os.environ["GEMINI_API_KEY"]
    text = path.read_text(encoding="utf-8")[:150_000]
    r = httpx.post(
        BASE,
        headers={"Authorization": f"Bearer {key}"},
        json={"model": MODEL,
              "messages": [{"role": "user", "content": PROMPT.format(name=path.name, text=text)}],
              "temperature": 0.2},
        timeout=180,
    )
    r.raise_for_status()
    yaml_text = r.json()["choices"][0]["message"]["content"]
    out_dir = Path(__file__).resolve().parents[1] / "articles" / "_proposed"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / (path.stem + ".yaml")
    out.write_text(yaml_text.strip().removeprefix("```yaml").removesuffix("```"), encoding="utf-8")
    print(f"Proposed articles → {out}  (review, then move into knowledge/articles/)")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
