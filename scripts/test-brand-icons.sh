#!/usr/bin/env bash
# Verify brand asset pipeline (icon generation + bundle layout).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MAC="$ROOT/apps/netllm-mac"
ASSETS="$ROOT/assets"
BUILD_ICONS="$MAC/Scripts/build-icons.sh"

fail() { echo "FAIL: $*" >&2; exit 1; }
ok() { echo "OK: $*"; }

echo "==> source assets"
for f in \
  llm-swam-router-icon.png \
  llm-swam-router-icon-white.png \
  llm-swam-router-icon-black-bg.png \
  llm-swam-router-icon-white-bg.png \
  llm-swam-router-icon.svg
do
  [[ -f "$ASSETS/$f" ]] || fail "missing $ASSETS/$f"
done
ok "all 5 brand files present"

echo "==> build-icons.sh"
bash "$BUILD_ICONS"

ICNS="$MAC/build/AppIcon.icns"
BRAND="$MAC/build/Brand"
[[ -f "$ICNS" ]] || fail "AppIcon.icns not generated"
file "$ICNS" | rg -qi 'Mac OS X icon' || fail "AppIcon.icns invalid format"
ok "AppIcon.icns"

for f in \
  MenubarIcon.png \
  MenubarIcon@2x.png \
  llm-swam-router-icon.png \
  llm-swam-router-icon.svg
do
  [[ -f "$BRAND/$f" ]] || fail "missing $BRAND/$f"
done
ok "menubar + brand staging"

APP="${NETLLM_APP:-$MAC/build/Stage/netllm-mac.app}"
if [[ -d "$APP" ]]; then
  echo "==> staged app bundle"
  [[ -f "$APP/Contents/Resources/AppIcon.icns" ]] || fail "bundle missing AppIcon.icns"
  /usr/libexec/PlistBuddy -c 'Print :CFBundleIconFile' "$APP/Contents/Info.plist" 2>/dev/null \
    | rg -qx 'AppIcon' || fail "Info.plist missing CFBundleIconFile=AppIcon"
  [[ -f "$APP/Contents/Resources/Brand/MenubarIcon.png" ]] || fail "bundle missing menubar icon"
  [[ -f "$APP/Contents/Resources/Brand/llm-swam-router-icon.svg" ]] || fail "bundle missing SVG"
  ok "app bundle brand resources"
else
  echo "SKIP: no staged app at $APP (run build.sh release for bundle checks)"
fi

echo "ALL BRAND ICON CHECKS PASSED"
