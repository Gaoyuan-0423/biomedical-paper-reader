#!/usr/bin/env bash
# Start the paper reader at http://127.0.0.1:8765 (override with PORT=...)
set -euo pipefail
cd "$(dirname "$0")/backend"
PORT="${PORT:-8765}"
if ! python3 -c "import fastapi, uvicorn, pypdf, httpx, multipart" 2>/dev/null; then
  echo "Installing backend dependencies..."
  python3 -m pip install -r requirements.txt
fi
URL="http://127.0.0.1:${PORT}"
echo "Paper Reader -> ${URL}"
( sleep 1.5; command -v open >/dev/null && open "$URL" || true ) &
exec python3 -m uvicorn main:app --host 127.0.0.1 --port "$PORT"
