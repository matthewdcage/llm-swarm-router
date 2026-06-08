#!/usr/bin/env bash
# Build and install like an end user: DMG → Applications → launch (not build/Stage).
# Uses macos-app-install.sh for clean upgrades (stops stale agents before replace).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INSTALLER="$ROOT/packaging/scripts/macos-app-install.sh"
DMG="$ROOT/dist/llm-swarm-router.dmg"

echo "==> Native install rehearsal for llm-swarm-router"
echo

echo "==> Building release app + DMG"
"$ROOT/apps/netllm-mac/Scripts/build.sh" release
"$ROOT/packaging/scripts/create-dmg.sh"

chmod +x "$INSTALLER"
"$INSTALLER" --dmg "$DMG"

cat <<EOF
To repeat this flow later:
  $ROOT/scripts/emulate-user-install-mac.sh

To upgrade from a downloaded release DMG (no build):
  $ROOT/scripts/upgrade-mac-app.sh ~/Downloads/llm-swarm-router.dmg
EOF
