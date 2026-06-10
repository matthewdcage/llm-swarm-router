#!/usr/bin/env bash
# Mount a DMG and print the mount point (stdout). Entity order in hdiutil -plist varies by OS.
set -euo pipefail

usage() {
  echo "Usage: mount-dmg.sh <path/to/image.dmg>" >&2
  exit 1
}

[[ $# -eq 1 ]] || usage
DMG="$1"
[[ -f "$DMG" ]] || { echo "DMG not found: $DMG" >&2; exit 1; }

VOL_BASENAME="$(basename "$DMG" .dmg)"
APP_NAME="llm-swarm-router.app"
LEGACY_APP_NAME="netllm-mac.app"

find_mounted_app() {
  local vol
  for vol in "/Volumes/$VOL_BASENAME" "/Volumes/$VOL_BASENAME "[0-9]*; do
    [[ -d "$vol" ]] || continue
    if [[ -d "$vol/$APP_NAME" || -d "$vol/$LEGACY_APP_NAME" ]]; then
      echo "$vol"
      return 0
    fi
  done
  return 1
}

existing="$(find_mounted_app || true)"
if [[ -n "$existing" ]]; then
  echo "$existing"
  exit 0
fi

plist_tmp="$(mktemp)"
cleanup() { rm -f "$plist_tmp"; }
trap cleanup EXIT

if ! hdiutil attach "$DMG" -nobrowse -plist >"$plist_tmp" 2>/dev/null; then
  echo "hdiutil attach failed for $DMG" >&2
  hdiutil attach "$DMG" -nobrowse >&2 || true
  exit 1
fi

mount=""
for i in $(seq 0 31); do
  candidate="$(plutil -extract "system-entities.$i.mount-point" raw "$plist_tmp" 2>/dev/null)" || candidate=""
  if [[ -n "$candidate" && -d "$candidate" ]]; then
    mount="$candidate"
    break
  fi
done

if [[ -z "$mount" ]]; then
  # Text fallback when plist layout is unexpected.
  mount="$(hdiutil attach "$DMG" -nobrowse 2>&1 | grep -oE '/Volumes/[^[:space:]]+' | tail -1 || true)"
fi

if [[ -z "$mount" || ! -d "$mount" ]]; then
  echo "Could not determine mount point for $DMG" >&2
  exit 1
fi

echo "$mount"
