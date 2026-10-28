# Shared helpers, sourced by start.sh and stop.sh.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN="$ROOT/data/run"
LOGS="$ROOT/data/logs"
PIDFILE="$RUN/backend.pid"
PYTHON="$ROOT/.venv/bin/python"

# True only if the pid file names a live process that is really our backend.
# This stops a recycled pid from being mistaken for (or killed as) the backend.
backend_running() {
  [ -f "$PIDFILE" ] || return 1
  local pid
  pid="$(cat "$PIDFILE")"
  [ -n "$pid" ] || return 1
  ps -p "$pid" -o command= 2>/dev/null | grep -q "backend.main"
}
