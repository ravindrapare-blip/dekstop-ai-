#!/bin/sh

set -eu

script_name="$(basename -- "$0")"
script_dir=""
case "$script_name" in
  install.sh | install-open-interpreter.sh)
    script_dir="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
    ;;
esac

export OPEN_INTERPRETER_GITHUB_REPO="${OPEN_INTERPRETER_GITHUB_REPO:-${CODEX_GITHUB_REPO:-openinterpreter/openinterpreter}}"
export OPEN_INTERPRETER_PRODUCT_NAME="${OPEN_INTERPRETER_PRODUCT_NAME:-${CODEX_INSTALL_PRODUCT_NAME:-Open Interpreter}}"
export OPEN_INTERPRETER_PACKAGE_ASSET_STEM="${OPEN_INTERPRETER_PACKAGE_ASSET_STEM:-${CODEX_PACKAGE_ASSET_STEM:-open-interpreter-package}}"
export OPEN_INTERPRETER_COMMAND_NAME="${OPEN_INTERPRETER_COMMAND_NAME:-${CODEX_COMMAND_NAME:-interpreter}}"
export OPEN_INTERPRETER_ALIAS_COMMAND_NAMES="${OPEN_INTERPRETER_ALIAS_COMMAND_NAMES:-${CODEX_ALIAS_COMMAND_NAMES:-i}}"
export OPEN_INTERPRETER_RELEASE_TAG_PREFIX="${OPEN_INTERPRETER_RELEASE_TAG_PREFIX:-${CODEX_RELEASE_TAG_PREFIX:-rust-v}}"
export INTERPRETER_HOME="${INTERPRETER_HOME:-${CODEX_HOME:-$HOME/.openinterpreter}}"
export OPEN_INTERPRETER_INSTALL_DIR="${OPEN_INTERPRETER_INSTALL_DIR:-${CODEX_INSTALL_DIR:-$HOME/.local/bin}}"
export OPEN_INTERPRETER_RELEASE="${OPEN_INTERPRETER_RELEASE:-${CODEX_RELEASE:-latest}}"
export OPEN_INTERPRETER_NONINTERACTIVE="${OPEN_INTERPRETER_NONINTERACTIVE:-${CODEX_NON_INTERACTIVE:-false}}"

if [ -n "$script_dir" ] && [ "$script_name" != "install.sh" ] && [ -f "$script_dir/install.sh" ]; then
  exec "$script_dir/install.sh" "$@"
fi

if command -v curl >/dev/null 2>&1; then
  curl -fsSL "https://www.openinterpreter.com/install" | sh -s -- "$@"
  exit $?
fi

if command -v wget >/dev/null 2>&1; then
  wget -q -O - "https://www.openinterpreter.com/install" | sh -s -- "$@"
  exit $?
fi

echo "curl or wget is required to install Open Interpreter." >&2
exit 1
