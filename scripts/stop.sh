#!/usr/bin/env bash
# Stop the backend started by start.sh.
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PIDFILE="$ROOT/data/run/backend.pid"

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  kill "$(cat "$PIDFILE")"
  echo "Backend stopped."
else
  echo "Backend is not running."
fi
rm -f "$PIDFILE"
