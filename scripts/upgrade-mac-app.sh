#!/usr/bin/env bash
# Upgrade llm-swarm-router from a release DMG (no local build).
# Use on any Mac after downloading from GitHub Releases.
#
# Usage:
#   ./scripts/upgrade-mac-app.sh
#   ./scripts/upgrade-mac-app.sh ~/Downloads/llm-swarm-router.dmg
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INSTALLER="$ROOT/packaging/scripts/macos-app-install.sh"
DMG="${1:-$ROOT/dist/llm-swarm-router.dmg}"

if [[ ! -f "$DMG" ]]; then
  cat >&2 <<EOF
DMG not found: $DMG

Download the latest release DMG, then run:
  $0 ~/Downloads/llm-swarm-router.dmg

Or from a repo checkout with a local build:
  $ROOT/scripts/emulate-user-install-mac.sh
EOF
  exit 1
fi

chmod +x "$INSTALLER"
exec "$INSTALLER" --dmg "$DMG"
