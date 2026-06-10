#!/usr/bin/env bash
# Notarize a release DMG and staple the ticket (run after create-dmg.sh).
#
# Requires:
#   APPLE_ID, APPLE_TEAM_ID, APPLE_APP_SPECIFIC_PASSWORD
#
# Usage:
#   APPLE_ID=… APPLE_TEAM_ID=… APPLE_APP_SPECIFIC_PASSWORD=… \
#     packaging/scripts/notarize-dmg.sh dist/llm-swarm-router.dmg
set -euo pipefail

DMG="${1:-}"
for var in APPLE_ID APPLE_TEAM_ID APPLE_APP_SPECIFIC_PASSWORD; do
  [[ -n "${!var:-}" ]] || {
    echo "$var is required" >&2
    exit 1
  }
done

[[ -f "$DMG" ]] || {
  echo "DMG not found: $DMG" >&2
  exit 1
}

echo "==> Submitting $DMG to Apple notary service"
xcrun notarytool submit "$DMG" \
  --apple-id "$APPLE_ID" \
  --password "$APPLE_APP_SPECIFIC_PASSWORD" \
  --team-id "$APPLE_TEAM_ID" \
  --wait

echo "==> Stapling notarization ticket"
xcrun stapler staple "$DMG"
spctl -a -t open --context context:primary-signature -v "$DMG" 2>&1 || true
echo "==> Notarized: $DMG"
