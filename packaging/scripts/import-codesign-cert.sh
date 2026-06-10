#!/usr/bin/env bash
# Import Developer ID certificate into an ephemeral keychain (CI or local).
#
# Inputs (env):
#   MACOS_CERTIFICATE_P12 — base64-encoded .p12 (required to import)
#   MACOS_CERTIFICATE_PASSWORD — export password for the .p12
#   KEYCHAIN_PASSWORD — password for the ephemeral keychain
#
# Outputs:
#   Sets CODESIGN_IDENTITY in GITHUB_ENV when running in Actions, else exports it.
#
# When MACOS_CERTIFICATE_P12 is unset, prints a notice and exits 0 (ad-hoc fallback).
set -euo pipefail

if [[ -z "${MACOS_CERTIFICATE_P12:-}" ]]; then
  echo "MACOS_CERTIFICATE_P12 not set — release build will use ad-hoc signing."
  exit 0
fi

for var in MACOS_CERTIFICATE_PASSWORD KEYCHAIN_PASSWORD; do
  [[ -n "${!var:-}" ]] || {
    echo "$var is required when MACOS_CERTIFICATE_P12 is set" >&2
    exit 1
  }
done

CERT_PATH="$(mktemp -t netllm-cert).p12"
KEYCHAIN="${RUNNER_TEMP:-/tmp}/netllm-build.keychain-db"
trap 'rm -f "$CERT_PATH"' EXIT

echo "$MACOS_CERTIFICATE_P12" | base64 --decode > "$CERT_PATH"

security create-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN"
security default-keychain -s "$KEYCHAIN"
security unlock-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN"
security set-keychain-settings -lut 21600 "$KEYCHAIN"
security import "$CERT_PATH" -k "$KEYCHAIN" -P "$MACOS_CERTIFICATE_PASSWORD" \
  -T /usr/bin/codesign -T /usr/bin/security
security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$KEYCHAIN_PASSWORD" "$KEYCHAIN"

IDENTITY="$(security find-identity -v -p codesigning "$KEYCHAIN" \
  | awk -F'"' '/Developer ID Application/ {print $2; exit}')"

if [[ -z "$IDENTITY" ]]; then
  echo "No Developer ID Application identity found in imported certificate" >&2
  security find-identity -v -p codesigning "$KEYCHAIN" >&2 || true
  exit 1
fi

echo "Imported signing identity: $IDENTITY"
if [[ -n "${GITHUB_ENV:-}" ]]; then
  {
    echo "CODESIGN_IDENTITY=$IDENTITY"
    echo "KEYCHAIN_PATH=$KEYCHAIN"
  } >> "$GITHUB_ENV"
else
  export CODESIGN_IDENTITY="$IDENTITY"
  export KEYCHAIN_PATH="$KEYCHAIN"
fi
