#!/usr/bin/env bash
# Set up the project environment. Downloads no models and changes no system settings.
# Usage: scripts/install.sh [--check]   (--check only reports what is missing)
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1
missing=0

have() { command -v "$1" >/dev/null 2>&1; }
ok()   { printf '  ok       %s\n' "$1"; }
warn() { printf '  MISSING  %s\n' "$1"; missing=1; }
note() { printf '  note     %s\n' "$1"; }

echo "Checking system"
if [ "$(uname -s)" = "Darwin" ]; then
  ok "macOS $(sw_vers -productVersion 2>/dev/null) ($(uname -m))"
else
  note "not macOS: only the backend skeleton can run here"
fi

PY=""
for c in python3.12 python3.11 python3.10 python3; do
  if have "$c" && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -n "$PY" ]; then ok "python ($($PY --version 2>&1))"; else warn "python 3.10+ (brew install python@3.11)"; fi

if have node; then
  major="$(node -p 'process.versions.node.split(".")[0]')"
  if [ "$major" -eq 20 ]; then ok "node $(node --version)"
  else note "node $(node --version) found; Node 20 LTS is the target for Monterey"; fi
else
  warn "node 20 LTS (brew install node@20), needed for browser control"
fi

have ollama || note "ollama not found (needed from phase 1; see docs/install.md for the Monterey caveat)"
[ -d "/Applications/Google Chrome.app" ] || note "Google Chrome not found (needed from phase 4)"
[ -d "/Applications/Hammerspoon.app" ] || note "Hammerspoon not found (needed from phase 4)"

[ "$CHECK_ONLY" -eq 1 ] && exit "$missing"
[ -z "$PY" ] && { echo "Cannot continue without Python 3.10+"; exit 1; }

echo "Creating folders"
mkdir -p "$ROOT/data/logs" "$ROOT/data/run"

echo "Setting up Python environment"
[ -d "$ROOT/.venv" ] || "$PY" -m venv "$ROOT/.venv" || exit 1
"$ROOT/.venv/bin/pip" install --quiet --upgrade pip || exit 1
"$ROOT/.venv/bin/pip" install --quiet -e "$ROOT" || exit 1
ok "python dependencies installed in .venv"

if have npm; then
  echo "Installing browser worker dependency"
  if (cd "$ROOT/browser" && npm install --no-audit --no-fund --silent); then
    ok "puppeteer-core installed (no Chromium download)"
  else
    warn "npm install failed in browser/ (needed for browser control; re-run after fixing)"
  fi
fi

echo "Done. Start with: scripts/start.sh"
