"""Stage 1 of knowledge ingestion: PDF → per-page text dumps.

Usage:  python knowledge/ingestion/extract_pdfs.py <pdf_dir> [out_dir]

Notes from the source-document corpus:
  • Training manual & diary extract cleanly.
  • SafetyManual / SurakshaPustika are scanned images → need OCR (tesseract with
    mar+hin+eng traineddata) — flagged in the report.
  • Do_Dont_for_Safety uses legacy embedded fonts → garbled glyphs; re-author manually.
"""
from __future__ import annotations

import sys
from pathlib import Path

import fitz  # pymupdf


def extract(pdf_dir: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for pdf in sorted(pdf_dir.glob("*.pdf")):
        doc = fitz.open(pdf)
        pages = [f"--- PAGE {i + 1} ---\n{p.get_text()}" for i, p in enumerate(doc)]
        text = "\n".join(pages)
        chars_per_page = len(text) / max(1, doc.page_count)
        out = out_dir / (pdf.stem.replace(" ", "_") + ".txt")
        out.write_text(text, encoding="utf-8")
        flag = "OK" if chars_per_page > 200 else "LOW TEXT — likely scanned, needs OCR"
        print(f"{pdf.name}: {doc.page_count}p, {len(text)} chars  [{flag}]")


if __name__ == "__main__":
    pdf_dir = Path(sys.argv[1])
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else pdf_dir / "extracted"
    extract(pdf_dir, out_dir)
