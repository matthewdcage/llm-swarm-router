#!/usr/bin/env bash
# Create a release DMG from a staged llm-swarm-router.app (drag-to-Applications layout).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STAGE="$ROOT/apps/netllm-mac/build/Stage"
APP_NAME="llm-swarm-router.app"
LEGACY_APP_NAME="netllm-mac.app"
OUT_DIR="$ROOT/dist"
DMG="$OUT_DIR/llm-swarm-router.dmg"
VOL_NAME="llm-swarm-router"

if [[ -d "$STAGE/$APP_NAME" ]]; then
  APP="$STAGE/$APP_NAME"
elif [[ -d "$STAGE/$LEGACY_APP_NAME" ]]; then
  APP="$STAGE/$LEGACY_APP_NAME"
  APP_NAME="$LEGACY_APP_NAME"
else
  echo "Missing staged app — run apps/netllm-mac/Scripts/build.sh release first" >&2
  exit 1
fi

LAYOUT="$(mktemp -d)"
trap 'rm -rf "$LAYOUT"' EXIT

ditto "$APP" "$LAYOUT/$APP_NAME"
ln -s /Applications "$LAYOUT/Applications"

mkdir -p "$OUT_DIR"
rm -f "$DMG"
hdiutil create -volname "$VOL_NAME" -srcfolder "$LAYOUT" -ov -format UDZO "$DMG"
echo "Created $DMG"
echo "User flow: open DMG → drag $APP_NAME to Applications → launch from Applications"
