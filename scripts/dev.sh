#!/usr/bin/env bash
#
# Run the full stack locally on macOS: FastAPI backend (:8000) + Next.js admin
# dashboard (:3100). Ctrl-C stops both.
#
#   chmod +x scripts/dev.sh && ./scripts/dev.sh
#
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

[ -d .venv ] || { echo "!! run ./scripts/setup_mac.sh first"; exit 1; }
[ -d frontend/dashboard/node_modules ] || { echo "!! run ./scripts/setup_mac.sh first"; exit 1; }

pids=()
cleanup() { echo; echo "stopping…"; for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT INT TERM

echo "==> Backend  → http://localhost:8000"
# shellcheck disable=SC1091
source .venv/bin/activate
python -m backend.app.main &
pids+=($!)

echo "==> Dashboard → http://localhost:3100"
( cd frontend/dashboard && npm run dev ) &
pids+=($!)

echo
echo "Both running. Dashboard: http://localhost:3100   Backend: http://localhost:8000"
echo "Press Ctrl-C to stop."
wait
