#!/usr/bin/env bash
# Stop the backend started by start.sh.
set -u
. "$(dirname "$0")/_lib.sh"

if backend_running; then
  pid="$(cat "$PIDFILE")"
  kill "$pid"
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.5
  done
  if kill -0 "$pid" 2>/dev/null; then
    echo "Backend did not exit after 5 seconds (pid $pid). Stop it manually."
    exit 1
  fi
  echo "Backend stopped."
else
  echo "Backend is not running."
fi
rm -f "$PIDFILE"
