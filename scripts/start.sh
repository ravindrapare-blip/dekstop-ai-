#!/usr/bin/env bash
# Start the backend in the background. Phase 0 has no other services to start.
set -eu
. "$(dirname "$0")/_lib.sh"

[ -x "$PYTHON" ] || { echo "Run scripts/install.sh first."; exit 1; }
mkdir -p "$RUN" "$LOGS"

if backend_running; then
  echo "Backend already running (pid $(cat "$PIDFILE"))."
  exit 0
fi
rm -f "$PIDFILE"

cd "$ROOT"
PORT="$("$PYTHON" -c 'from backend.config import load_config; print(load_config()["backend"]["port"])')"

nohup "$PYTHON" -m backend.main >>"$LOGS/backend.log" 2>&1 &
pid=$!
echo "$pid" >"$PIDFILE"

# Wait up to 10 seconds for the health endpoint instead of assuming success.
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
  kill -0 "$pid" 2>/dev/null || break
  if curl -fs -m 1 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
    # The answer may come from something else already on the port while our own
    # process is still failing to bind. Our process must survive a moment longer.
    sleep 1
    kill -0 "$pid" 2>/dev/null || break
    echo "Backend started (pid $pid) on 127.0.0.1:$PORT. Log: data/logs/backend.log"
    exit 0
  fi
  sleep 0.5
done

echo "Backend failed to start. Last log lines:"
tail -n 10 "$LOGS/backend.log" || true
kill "$pid" 2>/dev/null || true
rm -f "$PIDFILE"
exit 1
