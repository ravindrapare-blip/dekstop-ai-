#!/usr/bin/env bash
# Connect Hammerspoon to the assistant by adding a short block to ~/.hammerspoon/init.lua.
#   scripts/setup_hammerspoon.sh            add (or update) the block
#   scripts/setup_hammerspoon.sh --remove   take it out again
# Your own lines in init.lua are never touched. The first time, a copy is saved as init.lua.bak-assistant.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HS_DIR="${HAMMERSPOON_DIR:-$HOME/.hammerspoon}"
INIT="$HS_DIR/init.lua"
BEGIN="-- BEGIN local-assistant (added by scripts/setup_hammerspoon.sh; remove with --remove)"
END="-- END local-assistant"

# The port comes from config.yaml so the two sides cannot disagree.
PORT="$(awk '/^automation:/{f=1; next} /^[^ #]/{f=0} f && $1=="port:" {print $2; exit}' "$ROOT/config.yaml" 2>/dev/null || true)"
PORT="${PORT:-8767}"
case "$PORT" in *[!0-9]*|"") echo "automation.port in config.yaml must be a number." >&2; exit 1 ;; esac

lua_string() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }

# Drops our block, and the blank lines left behind, so repeated runs never pile up blank lines.
remove_block() {
  [ -f "$INIT" ] || return 0
  awk -v b="$BEGIN" -v e="$END" '
    $0 == b {skip=1; next}
    skip && $0 == e {skip=0; next}
    !skip {lines[++n] = $0}
    END {while (n > 0 && lines[n] == "") n--; for (i = 1; i <= n; i++) print lines[i]}
  ' "$INIT" > "$INIT.tmp-assistant"
  cat "$INIT.tmp-assistant" > "$INIT"
  rm -f "$INIT.tmp-assistant"
}

if [ "${1:-}" = "--remove" ]; then
  remove_block
  echo "Removed the assistant block from $INIT. Reload Hammerspoon to apply."
  exit 0
fi

[ -f "$ROOT/automation/hammerspoon/init.lua" ] || { echo "Cannot find automation/hammerspoon/init.lua." >&2; exit 1; }

mkdir -p "$HS_DIR"
touch "$INIT"
[ -e "$INIT.bak-assistant" ] || cp "$INIT" "$INIT.bak-assistant"
remove_block
{
  printf '\n%s\n' "$BEGIN"
  printf 'ASSISTANT_ROOT = "%s"\n' "$(lua_string "$ROOT")"
  printf 'ASSISTANT_PORT = %s\n' "$PORT"
  printf 'dofile(ASSISTANT_ROOT .. "/automation/hammerspoon/init.lua")\n'
  printf '%s\n' "$END"
} >> "$INIT"

# The shared secret. The backend also creates it on start; making it now means the order does not matter.
if [ -x "$ROOT/.venv/bin/python" ]; then
  "$ROOT/.venv/bin/python" - "$ROOT" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from automation.token import ensure_token
ensure_token(Path(sys.argv[1]) / "data" / ".token")
PY
fi

echo "Added the assistant block to $INIT (port $PORT)."
echo "Now reload Hammerspoon (its menu bar icon > Reload Config)."
echo "Hammerspoon may ask for Accessibility access the first time; that is normal."
