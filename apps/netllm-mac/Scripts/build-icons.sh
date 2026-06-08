#!/usr/bin/env bash
# Generate AppIcon.icns and menubar PNGs from repo brand assets.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
ASSETS="$ROOT/assets"
MAC_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUT_BRAND="$MAC_DIR/build/Brand"
ICONSET="$MAC_DIR/build/AppIcon.iconset"
ICNS_OUT="$MAC_DIR/build/AppIcon.icns"

SRC_APP="${NETLLM_ICON_APP:-$ASSETS/llm-swam-router-icon-black-bg.png}"
SRC_MENUBAR_LIGHT="${NETLLM_ICON_MENUBAR_LIGHT:-$ASSETS/llm-swam-router-icon.png}"
SRC_MENUBAR_DARK="${NETLLM_ICON_MENUBAR_DARK:-$ASSETS/llm-swam-router-icon-white.png}"

require_asset() {
  local path="$1"
  [[ -f "$path" ]] || {
    echo "Missing brand asset: $path" >&2
    exit 1
  }
}

require_asset "$SRC_APP"
require_asset "$SRC_MENUBAR_LIGHT"
require_asset "$SRC_MENUBAR_DARK"
require_asset "$ASSETS/llm-swam-router-icon.svg"
require_asset "$ASSETS/llm-swam-router-icon-white.png"
require_asset "$ASSETS/llm-swam-router-icon-white-bg.png"

rm -rf "$ICONSET" "$OUT_BRAND"
mkdir -p "$ICONSET" "$OUT_BRAND"

echo "==> Copying brand assets"
cp "$ASSETS"/llm-swam-router-icon*.png "$ASSETS"/llm-swam-router-icon.svg "$OUT_BRAND/"

echo "==> Building menubar icons (light + dark menu bar)"
sips -z 18 18 "$SRC_MENUBAR_LIGHT" --out "$OUT_BRAND/MenubarIconLight.png" >/dev/null
sips -z 36 36 "$SRC_MENUBAR_LIGHT" --out "$OUT_BRAND/MenubarIconLight@2x.png" >/dev/null
sips -z 18 18 "$SRC_MENUBAR_DARK" --out "$OUT_BRAND/MenubarIconDark.png" >/dev/null
sips -z 36 36 "$SRC_MENUBAR_DARK" --out "$OUT_BRAND/MenubarIconDark@2x.png" >/dev/null
# Legacy names (template fallback)
cp "$OUT_BRAND/MenubarIconLight.png" "$OUT_BRAND/MenubarIcon.png"
cp "$OUT_BRAND/MenubarIconLight@2x.png" "$OUT_BRAND/MenubarIcon@2x.png"

echo "==> Building AppIcon.icns from $(basename "$SRC_APP")"
make_icon() {
  local size="$1"
  local name="$2"
  sips -z "$size" "$size" "$SRC_APP" --out "$ICONSET/$name" >/dev/null
}

make_icon 16 icon_16x16.png
make_icon 32 icon_16x16@2x.png
make_icon 32 icon_32x32.png
make_icon 64 icon_32x32@2x.png
make_icon 128 icon_128x128.png
make_icon 256 icon_128x128@2x.png
make_icon 256 icon_256x256.png
make_icon 512 icon_256x256@2x.png
make_icon 512 icon_512x512.png
make_icon 1024 icon_512x512@2x.png

iconutil -c icns "$ICONSET" -o "$ICNS_OUT"

echo "==> Icons ready: $ICNS_OUT"
echo "    Brand: $OUT_BRAND"
