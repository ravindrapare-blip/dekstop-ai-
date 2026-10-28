#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 <macos|windows> <github-output-path>" >&2
  exit 2
}

[[ $# -eq 2 ]] || usage
platform="$1"
output_path="$2"
[[ -n "$output_path" ]] || usage

missing=()

require_nonempty() {
  local variable_name="$1"
  if [[ -z "${!variable_name:-}" ]]; then
    missing+=("$variable_name")
  fi
}

require_sha256() {
  local variable_name="$1"
  local value="${!variable_name:-}"
  if [[ -z "$value" ]]; then
    missing+=("$variable_name")
  elif [[ ! "$value" =~ ^[0-9a-f]{64}$ ]]; then
    missing+=("${variable_name}(invalid)")
  fi
}

require_optional_sha256() {
  local variable_name="$1"
  local value="${!variable_name:-}"
  local normalized="${value//:/}"
  normalized="${normalized//[[:space:]]/}"
  if [[ -n "$value" && ! "$normalized" =~ ^[0-9A-Fa-f]{64}$ ]]; then
    missing+=("${variable_name}(invalid)")
  fi
}

require_blob_uri() {
  local variable_name="$1"
  local value="${!variable_name:-}"
  if [[ -z "$value" ]]; then
    missing+=("$variable_name")
  elif [[ ! "$value" =~ ^az://[^/]+/[^/]+/.+ ]]; then
    missing+=("${variable_name}(invalid)")
  fi
}

case "$platform" in
  macos)
    require_blob_uri AKV_CODESIGN_RCODESIGN_BLOB_URI
    require_sha256 AKV_CODESIGN_RCODESIGN_SHA256
    require_blob_uri AKV_CODESIGN_PKCS11_LIBRARY_BLOB_URI
    require_sha256 AKV_CODESIGN_PKCS11_LIBRARY_SHA256
    require_nonempty AKV_CODESIGN_AZURE_CLIENT_ID
    require_nonempty AKV_CODESIGN_TENANT
    require_nonempty AKV_CODESIGN_SUBSCRIPTION
    require_nonempty AKV_CODESIGN_KEY_VAULT_NAME
    require_nonempty AKV_CODESIGN_KEY_NAME
    require_nonempty AKV_NOTARIZATION_KEY_NAME
    require_optional_sha256 AKV_CODESIGN_CERTIFICATE_SHA256
    ;;
  windows)
    require_nonempty AZURE_ARTIFACT_SIGNING_CLIENT_ID
    require_nonempty AZURE_ARTIFACT_SIGNING_TENANT_ID
    require_nonempty AZURE_ARTIFACT_SIGNING_SUBSCRIPTION_ID
    require_nonempty AZURE_ARTIFACT_SIGNING_ENDPOINT
    require_nonempty AZURE_ARTIFACT_SIGNING_ACCOUNT_NAME
    require_nonempty AZURE_ARTIFACT_SIGNING_CERTIFICATE_PROFILE_NAME
    ;;
  *)
    echo "Unsupported signing platform: $platform" >&2
    exit 2
    ;;
esac

if ((${#missing[@]} == 0)); then
  signing_mode="signed"
  echo "::notice::${platform} signing configuration is complete; using signed artifacts."
else
  signing_mode="unsigned"
  echo "::warning::${platform} signing configuration is incomplete; using verified unsigned artifacts. Missing or invalid: ${missing[*]}"
fi

printf 'signing_mode=%s\n' "$signing_mode" >> "$output_path"
