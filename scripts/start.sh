#!/usr/bin/env bash
# Start the backend in the background. Phase 0 has no other services to start.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN="$ROOT/data/run"
LOGS="$ROOT/data/logs"
PIDFILE="$RUN/backend.pid"
PYTHON="$ROOT/.venv/bin/python"

[ -x "$PYTHON" ] || { echo "Run scripts/install.sh first."; exit 1; }
mkdir -p "$RUN" "$LOGS"

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "Backend already running (pid $(cat "$PIDFILE"))."
  exit 0
fi

cd "$ROOT"
nohup "$PYTHON" -m backend.main >>"$LOGS/backend.log" 2>&1 &
echo $! >"$PIDFILE"
echo "Backend started (pid $!). Log: data/logs/backend.log"
