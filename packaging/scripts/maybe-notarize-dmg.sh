#!/usr/bin/env bash
# Notarize DMG when Apple notary credentials are present; skip otherwise.
set -euo pipefail

DMG="${1:-}"
[[ -f "$DMG" ]] || {
  echo "DMG not found: $DMG" >&2
  exit 1
}

if [[ -z "${APPLE_ID:-}" || -z "${APPLE_TEAM_ID:-}" || -z "${APPLE_APP_SPECIFIC_PASSWORD:-}" ]]; then
  echo "Apple notary credentials not set — skipping DMG notarization (Gatekeeper workarounds still apply)."
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec bash "$SCRIPT_DIR/notarize-dmg.sh" "$DMG"
