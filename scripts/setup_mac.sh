#!/usr/bin/env bash
#
# One-time local setup on macOS: Python backend venv + Node dashboard deps.
#
#   chmod +x scripts/setup_mac.sh && ./scripts/setup_mac.sh
#
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "==> Backend: Python venv + deps"
command -v python3 >/dev/null || { echo "!! install Python 3 (brew install python)"; exit 1; }
[ -d .venv ] || python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt
[ -f .env ] || { cp .env.example .env; echo "   created .env — paste SARVAM_API_KEY + GEMINI_API_KEY"; }

echo "==> Dashboard: Node deps"
command -v node >/dev/null || { echo "!! install Node 18+ (brew install node)"; exit 1; }
cd frontend/dashboard
npm install
[ -f .env.local ] || { cp .env.local.example .env.local; echo "   created frontend/dashboard/.env.local"; }

echo
echo "✅ Setup complete. Start everything with:  ./scripts/dev.sh"
