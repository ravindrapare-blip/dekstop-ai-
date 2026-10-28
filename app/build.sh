#!/usr/bin/env bash
# Build the capsule app into app/build/Assistant.app. Needs the Xcode command line tools (macOS only).
#   ./build.sh          build
#   ./build.sh test     run the unit tests instead
set -euo pipefail

cd "$(dirname "$0")"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "The app builds on macOS only." >&2
  exit 1
fi
command -v swift >/dev/null 2>&1 || { echo "Swift not found. Run: xcode-select --install" >&2; exit 1; }

if [ "${1:-}" = "test" ]; then
  exec swift test
fi

swift build -c release
BIN="$(swift build -c release --show-bin-path)/AssistantApp"
APP="build/Assistant.app"

rm -rf "${APP:?}"
mkdir -p "$APP/Contents/MacOS"
cp "$BIN" "$APP/Contents/MacOS/Assistant"
cp Resources/Info.plist "$APP/Contents/Info.plist"

# An ad-hoc signature is enough to run locally and lets macOS remember permissions for the app.
codesign --force --sign - "$APP"

echo "Built $APP"
echo "Start it with: open $APP"
