"""Conversation eval harness. Drives the /chat text endpoint through scripted
scenarios and checks behavioural invariants — the regressions that actually bit the
old build (invented ticket numbers, language non-switch, holding phrases, menus).

Usage:  python evaluation/run_eval.py [--base http://localhost:8000]
Exit code 1 on any failure → CI-gateable.
"""
from __future__ import annotations

import argparse
import re
import sys
import uuid
from pathlib import Path

import httpx
import yaml

SCENARIOS = Path(__file__).parent / "scenarios"

# ── behavioural checks ──────────────────────────────────────────────────────
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_HOLDING = re.compile(r"please wait|one moment|एक मिनिट|मी बघते हं|i am checking|मैं देख रही हूँ, रुकिए", re.I)
_MARKDOWN = re.compile(r"[*_#]|^\s*[-•]\s|\d+\.\s+\S+.*\n\s*\d+\.\s", re.M)
_TOOL_LEAK = re.compile(r"verify_customer|get_bill|get_network_status|register_complaint|search_knowledge|transfer_to_human", re.I)
_TICKET = re.compile(r"TC[0-9A-Z]{8,}")


def check(name: str, reply: str, expect: dict, memory: dict) -> list[str]:
    fails = []
    if _HOLDING.search(reply):
        fails.append(f"holding phrase: {reply[:80]!r}")
    if _MARKDOWN.search(reply):
        fails.append(f"markdown/list in speech: {reply[:80]!r}")
    if _TOOL_LEAK.search(reply):
        fails.append(f"tool name leaked: {reply[:80]!r}")
    lang = expect.get("language")
    if lang == "en" and _DEVANAGARI.search(reply):
        fails.append(f"expected English, got Devanagari: {reply[:80]!r}")
    if lang in ("hi", "mr") and not _DEVANAGARI.search(reply) and len(reply) > 25:
        fails.append(f"expected {lang}, got Latin-only: {reply[:80]!r}")
    for phrase in expect.get("contains", []):
        if phrase.lower() not in reply.lower():
            fails.append(f"missing {phrase!r} in {reply[:80]!r}")
    for phrase in expect.get("not_contains", []):
        if phrase.lower() in reply.lower():
            fails.append(f"forbidden {phrase!r} present")
    if expect.get("ticket_number") and not _TICKET.search(reply.replace(" ", "")):
        # ticket may be read digit-by-digit — also accept it in memory
        if not memory.get("complaints"):
            fails.append("expected a registered ticket number")
    if expect.get("no_ticket") and (_TICKET.search(reply) or memory.get("complaints")):
        fails.append("ticket number appeared without verification — verify-gate breached!")
    if expect.get("verified") is not None and memory.get("verified") != expect["verified"]:
        fails.append(f"verified={memory.get('verified')} expected {expect['verified']}")
    return [f"[{name}] {f}" for f in fails]


def run_scenario(base: str, path: Path) -> list[str]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    session = f"eval-{uuid.uuid4().hex[:8]}"
    failures: list[str] = []
    print(f"\n── {doc['name']} ──")
    with httpx.Client(base_url=base, timeout=60) as client:
        for i, turn in enumerate(doc["turns"]):
            r = client.post("/chat", json={"session_id": session, "text": turn["user"],
                                           "lang_hint": turn.get("lang_hint", "unknown")})
            r.raise_for_status()
            body = r.json()
            reply = " ".join(body["replies"])
            print(f"  U: {turn['user'][:70]}")
            print(f"  A: {reply[:110]}")
            failures += check(f"{path.stem}:{i}", reply, turn.get("expect", {}), body["memory"])
        client.delete(f"/chat/{session}")
    return failures


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--only", default="")
    args = ap.parse_args()

    failures: list[str] = []
    for f in sorted(SCENARIOS.glob("*.yaml")):
        if args.only and args.only not in f.stem:
            continue
        try:
            failures += run_scenario(args.base, f)
        except Exception as e:
            failures.append(f"[{f.stem}] scenario crashed: {e}")

    print("\n" + "=" * 60)
    if failures:
        print(f"FAIL — {len(failures)} issue(s):")
        for f in failures:
            print("  ✗", f)
        sys.exit(1)
    print("PASS — all scenarios clean")


if __name__ == "__main__":
    main()
