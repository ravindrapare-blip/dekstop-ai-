#!/usr/bin/env bash
set -euo pipefail
source_notes=${1:?release notes source is required}
output_notes=${2:?release notes output is required}
[[ -s "$source_notes" ]] || { echo "Reviewed release notes are missing or empty: $source_notes" >&2; exit 1; }
grep -Eq '^## Models[[:space:]]*$' "$source_notes" || {
  echo "Release notes must include a Models section" >&2
  exit 1
}
cp -- "$source_notes" "$output_notes"
