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
LIKELY_MOUNT="/Volumes/$VOL_BASENAME"

if [[ -d "$LIKELY_MOUNT" ]]; then
  echo "$LIKELY_MOUNT"
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
