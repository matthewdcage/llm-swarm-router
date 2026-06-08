#!/usr/bin/env bash
# Create a release DMG from a staged netllm-mac.app
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APP="$ROOT/apps/netllm-mac/build/Stage/netllm-mac.app"
OUT_DIR="$ROOT/dist"
DMG="$OUT_DIR/netllm-mac.dmg"
VOL_NAME="netllm"

if [[ ! -d "$APP" ]]; then
  echo "Missing staged app — run apps/netllm-mac/Scripts/build.sh release first" >&2
  exit 1
fi

mkdir -p "$OUT_DIR"
rm -f "$DMG"
hdiutil create -volname "$VOL_NAME" -srcfolder "$APP" -ov -format UDZO "$DMG"
echo "Created $DMG"
