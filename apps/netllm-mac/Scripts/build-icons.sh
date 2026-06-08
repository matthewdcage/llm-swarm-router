#!/usr/bin/env bash
# Generate AppIcon.icns (light + dark transparent bee) and menubar PNGs from brand assets.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
ASSETS="$ROOT/assets"
MAC_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUT_BRAND="$MAC_DIR/build/Brand"
ASSET_CATALOG="$MAC_DIR/build/Assets.xcassets"
APPICON_SET="$ASSET_CATALOG/AppIcon.appiconset"
ICNS_OUT="$MAC_DIR/build/AppIcon.icns"
CAR_OUT="$MAC_DIR/build/Assets.car"

SRC_APP_LIGHT="${NETLLM_ICON_APP_LIGHT:-$ASSETS/llm-swam-router-icon.png}"
SRC_APP_DARK="${NETLLM_ICON_APP_DARK:-$ASSETS/llm-swam-router-icon-white.png}"
SRC_MENUBAR_LIGHT="${NETLLM_ICON_MENUBAR_LIGHT:-$ASSETS/llm-swam-router-icon.png}"
SRC_MENUBAR_DARK="${NETLLM_ICON_MENUBAR_DARK:-$ASSETS/llm-swam-router-icon-white.png}"

require_asset() {
  local path="$1"
  [[ -f "$path" ]] || {
    echo "Missing brand asset: $path" >&2
    exit 1
  }
}

require_asset "$SRC_APP_LIGHT"
require_asset "$SRC_APP_DARK"
require_asset "$SRC_MENUBAR_LIGHT"
require_asset "$SRC_MENUBAR_DARK"
require_asset "$ASSETS/llm-swam-router-icon.svg"
require_asset "$ASSETS/llm-swam-router-icon-white.png"
require_asset "$ASSETS/llm-swam-router-icon-black-bg.png"
require_asset "$ASSETS/llm-swam-router-icon-white-bg.png"

rm -rf "$ASSET_CATALOG" "$OUT_BRAND" "$ICNS_OUT" "$CAR_OUT"
mkdir -p "$APPICON_SET" "$OUT_BRAND"

echo "==> Copying brand assets"
cp "$ASSETS"/llm-swam-router-icon*.png "$ASSETS"/llm-swam-router-icon.svg "$OUT_BRAND/"

echo "==> Building menubar icons (light + dark menu bar)"
sips -z 18 18 "$SRC_MENUBAR_LIGHT" --out "$OUT_BRAND/MenubarIconLight.png" >/dev/null
sips -z 36 36 "$SRC_MENUBAR_LIGHT" --out "$OUT_BRAND/MenubarIconLight@2x.png" >/dev/null
sips -z 18 18 "$SRC_MENUBAR_DARK" --out "$OUT_BRAND/MenubarIconDark.png" >/dev/null
sips -z 36 36 "$SRC_MENUBAR_DARK" --out "$OUT_BRAND/MenubarIconDark@2x.png" >/dev/null
cp "$OUT_BRAND/MenubarIconLight.png" "$OUT_BRAND/MenubarIcon.png"
cp "$OUT_BRAND/MenubarIconLight@2x.png" "$OUT_BRAND/MenubarIcon@2x.png"

echo "==> Building AppIcon (transparent light + dark) via actool"
make_appicon_png() {
  local src="$1" prefix="$2" size="$3" name="$4"
  sips -z "$size" "$size" "$src" --out "$APPICON_SET/${prefix}${name}" >/dev/null
}

for size in 16 32 128 256 512; do
  make_appicon_png "$SRC_APP_LIGHT" "" "$size" "icon_${size}x${size}.png"
  make_appicon_png "$SRC_APP_LIGHT" "" "$((size * 2))" "icon_${size}x${size}@2x.png"
  make_appicon_png "$SRC_APP_DARK" "dark_" "$size" "icon_${size}x${size}.png"
  make_appicon_png "$SRC_APP_DARK" "dark_" "$((size * 2))" "icon_${size}x${size}@2x.png"
done

cat > "$APPICON_SET/Contents.json" <<'JSON'
{
  "images": [
    {"size":"16x16","idiom":"mac","filename":"icon_16x16.png","scale":"1x"},
    {"size":"16x16","idiom":"mac","filename":"icon_16x16@2x.png","scale":"2x"},
    {"size":"32x32","idiom":"mac","filename":"icon_32x32.png","scale":"1x"},
    {"size":"32x32","idiom":"mac","filename":"icon_32x32@2x.png","scale":"2x"},
    {"size":"128x128","idiom":"mac","filename":"icon_128x128.png","scale":"1x"},
    {"size":"128x128","idiom":"mac","filename":"icon_128x128@2x.png","scale":"2x"},
    {"size":"256x256","idiom":"mac","filename":"icon_256x256.png","scale":"1x"},
    {"size":"256x256","idiom":"mac","filename":"icon_256x256@2x.png","scale":"2x"},
    {"size":"512x512","idiom":"mac","filename":"icon_512x512.png","scale":"1x"},
    {"size":"512x512","idiom":"mac","filename":"icon_512x512@2x.png","scale":"2x"},
    {"size":"16x16","idiom":"mac","filename":"dark_icon_16x16.png","scale":"1x","appearances":[{"appearance":"luminosity","value":"dark"}]},
    {"size":"16x16","idiom":"mac","filename":"dark_icon_16x16@2x.png","scale":"2x","appearances":[{"appearance":"luminosity","value":"dark"}]},
    {"size":"32x32","idiom":"mac","filename":"dark_icon_32x32.png","scale":"1x","appearances":[{"appearance":"luminosity","value":"dark"}]},
    {"size":"32x32","idiom":"mac","filename":"dark_icon_32x32@2x.png","scale":"2x","appearances":[{"appearance":"luminosity","value":"dark"}]},
    {"size":"128x128","idiom":"mac","filename":"dark_icon_128x128.png","scale":"1x","appearances":[{"appearance":"luminosity","value":"dark"}]},
    {"size":"128x128","idiom":"mac","filename":"dark_icon_128x128@2x.png","scale":"2x","appearances":[{"appearance":"luminosity","value":"dark"}]},
    {"size":"256x256","idiom":"mac","filename":"dark_icon_256x256.png","scale":"1x","appearances":[{"appearance":"luminosity","value":"dark"}]},
    {"size":"256x256","idiom":"mac","filename":"dark_icon_256x256@2x.png","scale":"2x","appearances":[{"appearance":"luminosity","value":"dark"}]},
    {"size":"512x512","idiom":"mac","filename":"dark_icon_512x512.png","scale":"1x","appearances":[{"appearance":"luminosity","value":"dark"}]},
    {"size":"512x512","idiom":"mac","filename":"dark_icon_512x512@2x.png","scale":"2x","appearances":[{"appearance":"luminosity","value":"dark"}]}
  ],
  "info": {"version": 1, "author": "xcode"}
}
JSON

ACTOOL_OUT="$MAC_DIR/build/actool-out"
rm -rf "$ACTOOL_OUT"
mkdir -p "$ACTOOL_OUT"
xcrun actool "$ASSET_CATALOG" \
  --compile "$ACTOOL_OUT" \
  --platform macosx \
  --minimum-deployment-target 14.0 \
  --app-icon AppIcon \
  --output-partial-info-plist "$MAC_DIR/build/AppIcon-partial.plist" \
  >/dev/null

cp "$ACTOOL_OUT/AppIcon.icns" "$ICNS_OUT"
cp "$ACTOOL_OUT/Assets.car" "$CAR_OUT"

echo "==> Icons ready: $ICNS_OUT"
echo "    Assets.car: $CAR_OUT"
echo "    Brand: $OUT_BRAND"
