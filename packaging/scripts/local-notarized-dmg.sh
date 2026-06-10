#!/usr/bin/env bash
# Build, sign, notarize, and stage a Gatekeeper-safe macOS DMG (maintainer local release).
#
# Requires Developer ID Application in login keychain + Apple notary credentials.
#
# Usage:
#   export APPLE_ID='you@example.com'
#   export APPLE_TEAM_ID='XXXXXXXXXX'
#   export APPLE_APP_SPECIFIC_PASSWORD='xxxx-xxxx-xxxx-xxxx'
#   packaging/scripts/local-notarized-dmg.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

IDENTITY="$(security find-identity -v -p codesigning \
  | awk -F'"' '/Developer ID Application/ {print $2; exit}')"
if [[ -z "$IDENTITY" ]]; then
  cat >&2 <<'EOF'
No Developer ID Application certificate in your login keychain.

Fix in Xcode (about 2 minutes):
  1. Xcode → Settings → Accounts → your Apple ID → Manage Certificates…
  2. + → Developer ID Application
  3. Re-run this script

Verify:
  security find-identity -v -p codesigning | grep "Developer ID Application"
EOF
  exit 1
fi

for var in APPLE_ID APPLE_TEAM_ID APPLE_APP_SPECIFIC_PASSWORD; do
  [[ -n "${!var:-}" ]] || {
    echo "Set $var for notarization (see docs/macos-code-signing.md)" >&2
    exit 1
  }
done

echo "==> Building release app"
apps/netllm-mac/Scripts/build.sh release

APP="$ROOT/apps/netllm-mac/build/Stage/llm-swarm-router.app"
export CODESIGN_IDENTITY="$IDENTITY"
echo "==> Signing with: $IDENTITY"
bash packaging/scripts/codesign-mac-app.sh "$APP"

echo "==> Creating DMG"
bash packaging/scripts/create-dmg.sh

DMG="$ROOT/dist/llm-swarm-router.dmg"
bash packaging/scripts/notarize-dmg.sh "$DMG"

echo ""
echo "Gatekeeper-safe DMG ready:"
echo "  $DMG"
echo ""
echo "Install:"
echo "  open \"$DMG\""
echo "  # drag llm-swarm-router to Applications — should work without malware prompts on macOS 26+"
echo ""
spctl -a -t open --context context:primary-signature -v "$DMG" 2>&1 || true
