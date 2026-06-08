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
CAR="$MAC/build/Assets.car"
METHOD_FILE="$MAC/build/.appicon-method"
BRAND="$MAC/build/Brand"
[[ -f "$ICNS" ]] || fail "AppIcon.icns not generated"
[[ -f "$METHOD_FILE" ]] || fail "missing .appicon-method (build-icons incomplete)"
METHOD="$(cat "$METHOD_FILE")"
file "$ICNS" | rg -qi 'Mac OS X icon' || fail "AppIcon.icns invalid format"
if [[ "$METHOD" == "actool" ]]; then
  [[ -f "$CAR" ]] || fail "Assets.car not generated (actool app icon)"
  ok "AppIcon.icns + Assets.car (actool light/dark)"
else
  ok "AppIcon.icns (iconutil fallback — actool unavailable on this host)"
fi

for f in \
  MenubarIconLight.png \
  MenubarIconLight@2x.png \
  MenubarIconDark.png \
  MenubarIconDark@2x.png \
  MenubarIcon.png \
  MenubarIcon@2x.png \
  llm-swam-router-icon.png \
  llm-swam-router-icon.svg
do
  [[ -f "$BRAND/$f" ]] || fail "missing $BRAND/$f"
done
ok "menubar + brand staging"

APP="${NETLLM_APP:-$MAC/build/Stage/llm-swarm-router.app}"
if [[ -d "$APP" ]]; then
  echo "==> staged app bundle"
  [[ -f "$APP/Contents/Resources/AppIcon.icns" ]] || fail "bundle missing AppIcon.icns"
  if [[ "${METHOD:-}" == "actool" ]]; then
    [[ -f "$APP/Contents/Resources/Assets.car" ]] || fail "bundle missing Assets.car"
  fi
  /usr/libexec/PlistBuddy -c 'Print :CFBundleIconFile' "$APP/Contents/Info.plist" 2>/dev/null \
    | rg -qx 'AppIcon' || fail "Info.plist missing CFBundleIconFile=AppIcon"
  /usr/libexec/PlistBuddy -c 'Print :CFBundleIconName' "$APP/Contents/Info.plist" 2>/dev/null \
    | rg -qx 'AppIcon' || fail "Info.plist missing CFBundleIconName=AppIcon"
  [[ -f "$APP/Contents/Resources/Brand/MenubarIconLight.png" ]] || fail "bundle missing light menubar icon"
  [[ -f "$APP/Contents/Resources/Brand/MenubarIconDark.png" ]] || fail "bundle missing dark menubar icon"
  [[ -f "$APP/Contents/Resources/Brand/llm-swam-router-icon.svg" ]] || fail "bundle missing SVG"
  ok "app bundle brand resources"
else
  echo "SKIP: no staged app at $APP (run build.sh release for bundle checks)"
fi

echo "ALL BRAND ICON CHECKS PASSED"
