"""Standalone Sarvam TTS check — isolates voice synthesis from the whole pipeline.

Run it from the repo root with the venv active:

    python test_tts.py

It reads TTS settings straight from your .env, calls the Sarvam TTS endpoint for
Marathi, Hindi and English, prints the exact HTTP status / error for each, and
saves a .wav you can play. If a call fails here, that same failure is what makes
the live agent show text but no voice.
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent


def load_env(path: Path) -> dict:
    env = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip()
    return env


def main() -> int:
    env = load_env(ROOT / ".env")
    key = env.get("SARVAM_API_KEY", "")
    model = env.get("TTS_MODEL", "bulbul:v3")
    speaker = env.get("TTS_SPEAKER", "priya")
    pace = float(env.get("TTS_PACE", "1.05"))

    if not key:
        print("No SARVAM_API_KEY found in .env")
        return 1

    print(f"model={model}  speaker={speaker}  pace={pace}\n")

    cases = {
        "mr-IN": "सिंकब्रॉड नेटवर्क्स ग्राहक सेवेत आपले स्वागत आहे. मी प्रिया, आपली कशी मदत करू शकते?",
        "hi-IN": "नमस्ते, मैं आपकी कैसे मदद कर सकती हूँ?",
        "en-IN": "Welcome to Syncbroad Networks customer care. How may I help you today?",
    }

    ok = True
    with httpx.Client(timeout=30.0) as client:
        for lang, text in cases.items():
            payload = {
                "model": model,
                "text": text,
                "target_language_code": lang,
                "speaker": speaker,
                "pace": pace,
                "speech_sample_rate": 24000,
                "enable_preprocessing": True,
            }
            try:
                r = client.post(
                    "https://api.sarvam.ai/text-to-speech",
                    headers={"api-subscription-key": key},
                    json=payload,
                )
            except Exception as e:
                print(f"[{lang}] REQUEST FAILED: {e}")
                ok = False
                continue

            if r.status_code != 200:
                print(f"[{lang}] HTTP {r.status_code}: {r.text[:400]}")
                ok = False
                continue

            audios = r.json().get("audios") or []
            if not audios:
                print(f"[{lang}] HTTP 200 but empty audios: {r.text[:200]}")
                ok = False
                continue

            wav = base64.b64decode(audios[0])
            out = ROOT / f"tts_test_{lang}.wav"
            out.write_bytes(wav)
            print(f"[{lang}] OK — {len(wav)} bytes → {out.name}")

    print("\nAll good." if ok else "\nAt least one call failed — see errors above.")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
