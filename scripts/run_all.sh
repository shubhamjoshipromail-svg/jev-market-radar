#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -x .venv/bin/python ]]; then
  PYTHON="$ROOT/.venv/bin/python"
else
  PYTHON="${PYTHON:-python3}"
fi

pids=()
stop_all() {
  trap - INT TERM EXIT
  if ((${#pids[@]})); then
    kill "${pids[@]}" 2>/dev/null || true
    wait "${pids[@]}" 2>/dev/null || true
  fi
}
trap stop_all INT TERM EXIT

"$PYTHON" -m radar.markets --loop 180 &
pids+=("$!")
"$PYTHON" -m radar.news.rss --loop 60 &
pids+=("$!")
"$PYTHON" -m radar.news.bluesky &
pids+=("$!")
"$PYTHON" -m api.server worker &
pids+=("$!")
"$PYTHON" -m radar.tracker &
pids+=("$!")
"$PYTHON" -m radar.notify &
pids+=("$!")
"$PYTHON" -m uvicorn api.server:app --host 127.0.0.1 --port "${PORT:-8000}" &
pids+=("$!")

echo "Jev Market Radar running at http://127.0.0.1:${PORT:-8000}"
wait
