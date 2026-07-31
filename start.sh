#!/bin/sh
# Boot Colloquy and, if the database is empty and autoseed is enabled (default),
# seed it with the launch missions. Makes first deploy a zero-step launch.
set -e
PORT="${PORT:-8080}"

uvicorn app.main:app --host 0.0.0.0 --port "$PORT" &
PID=$!

if [ "${COLLOQUY_AUTOSEED:-1}" = "1" ]; then
  (
    # wait for the server to answer
    for i in $(seq 1 30); do
      if python -c "import requests;requests.get('http://127.0.0.1:$PORT/api/v1/agents',timeout=2)" 2>/dev/null; then
        break
      fi
      sleep 1
    done
    COUNT=$(python - <<'EOF'
import os, requests
try:
    print(len(requests.get(f"http://127.0.0.1:{os.getenv('PORT','8080')}/api/v1/agents", timeout=5).json()))
except Exception:
    print("err")
EOF
)
    if [ "$COUNT" = "0" ]; then
      echo "[autoseed] empty database — seeding launch missions"
      python seed_missions.py --base "http://127.0.0.1:$PORT" || echo "[autoseed] seed failed (non-fatal)"
    else
      echo "[autoseed] database not empty ($COUNT agents) — skipping"
    fi
  ) &
fi

wait $PID
