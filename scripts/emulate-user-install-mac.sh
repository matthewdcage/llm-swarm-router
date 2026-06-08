#!/usr/bin/env bash
# Build and install like an end user: DMG → Applications → launch (not build/Stage).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_NAME="llm-swarm-router.app"
INSTALL_PATH="/Applications/$APP_NAME"
DMG="$ROOT/dist/llm-swarm-router.dmg"

echo "==> Native install rehearsal for llm-swarm-router"
echo "    Quit the menubar app first (About → Quit llm-swarm-router)."
echo

echo "==> Building release app + DMG"
"$ROOT/apps/netllm-mac/Scripts/build.sh" release
"$ROOT/packaging/scripts/create-dmg.sh"

if pgrep -f "Contents/MacOS/netllm-mac" >/dev/null 2>&1; then
  echo "WARN: A netllm menubar/agent process is still running."
  echo "      Quit from the menu bar before installing from Applications."
fi

if [[ -d "$INSTALL_PATH" ]]; then
  echo "==> Removing previous /Applications install"
  rm -rf "$INSTALL_PATH"
fi

echo "==> Mounting DMG and copying to Applications (automates the drag)"
# -quiet suppresses mount path output; parse the /Volumes/ line from attach instead.
MOUNT="$(hdiutil attach "$DMG" -nobrowse 2>&1 | awk '/\/Volumes\// {print $NF; exit}')"
if [[ -z "$MOUNT" || ! -d "$MOUNT" ]]; then
  echo "Failed to mount DMG: $DMG" >&2
  exit 1
fi
trap 'hdiutil detach "$MOUNT" -quiet 2>/dev/null || true' EXIT
SOURCE="$MOUNT/$APP_NAME"
[[ -d "$SOURCE" ]] || SOURCE="$MOUNT/netllm-mac.app"
[[ -d "$SOURCE" ]] || {
  echo "App not found on DMG volume ($MOUNT). Contents:" >&2
  ls -la "$MOUNT" >&2 || true
  exit 1
}
ditto "$SOURCE" "$INSTALL_PATH"
xattr -dr com.apple.quarantine "$INSTALL_PATH" 2>/dev/null || true

echo "==> Launching from Applications (user path)"
open -a "$INSTALL_PATH"

cat <<EOF

Done. You should see the bee icon in the menu bar.

If macOS blocks the first launch: right-click llm-swarm-router in Applications → Open once.

Verify:
  - Menubar → About llm-swarm-router
  - Menubar → Settings…
  - Terminal: netllm status   (uses ~/.config/netllm/bin/netllm shim after first launch)

To repeat this flow later, re-run:
  $ROOT/scripts/emulate-user-install-mac.sh

EOF
