#!/usr/bin/env bash
# Build netllm-mac.app with embedded venvstacks Python layers.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
MAC_DIR="$ROOT/apps/netllm-mac"
STAGE="$MAC_DIR/build/Stage"
APP="$STAGE/llm-swarm-router.app"
PYTHON_RES="$APP/Contents/Resources/Python"
PACKAGES_RES="$APP/Contents/Resources/netllm_packages"

MODE="${1:-release}"
REBUILD_DONOR="${REBUILD_DONOR:-auto}"

if ! command -v uv >/dev/null 2>&1; then
  echo "error: uv is required — install from https://docs.astral.sh/uv/ then run: uv sync" >&2
  exit 1
fi

version() {
  if [[ -n "${NETLLM_VERSION:-}" ]]; then
    echo "$NETLLM_VERSION"
    return
  fi
  # Use workspace Python (3.11+); system python3 on macOS may be 3.9/3.10 without tomllib.
  (cd "$ROOT" && uv run python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")
}

MARKETING_VERSION="$(version)"
CURRENT_PROJECT_VERSION="$(git -C "$ROOT" rev-list --count HEAD 2>/dev/null || echo 0)"

echo "==> netllm-mac $MARKETING_VERSION (build $CURRENT_PROJECT_VERSION)"

# --- brand icons ---
echo "==> Building app icons"
bash "$MAC_DIR/Scripts/build-icons.sh"

# --- venvstacks donor ---
FP="$(cd "$ROOT" && uv run python packaging/build.py --print-fingerprint)"
EXPORT="$ROOT/packaging/_export"
CACHED_FP="$EXPORT/.fingerprint"

need_export=0
if [[ "$REBUILD_DONOR" == "force" ]] || [[ "${2:-}" == "--rebuild-donor" ]]; then
  need_export=1
elif [[ "${2:-}" == "--no-rebuild-donor" ]]; then
  need_export=0
elif [[ ! -f "$CACHED_FP" ]] || [[ "$(cat "$CACHED_FP")" != "$FP" ]] || [[ ! -d "$EXPORT/cpython-3.11" ]]; then
  need_export=1
fi

if [[ "$need_export" == 1 ]]; then
  echo "==> Exporting venvstacks layers (fingerprint ${FP:0:12}…)"
  (cd "$ROOT" && uv run python packaging/build.py --venvstacks-only --force)
else
  echo "==> Reusing venvstacks export (fingerprint ${FP:0:12}…)"
fi

# --- Swift build ---
echo "==> Building Swift binary"
cd "$MAC_DIR"
swift build -c release 2>/dev/null || swift build -c release
BIN="$(swift build -c release --show-bin-path)/NetllmMac"

# --- Stage .app bundle ---
rm -rf "$STAGE"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "$PYTHON_RES" "$PACKAGES_RES"

cp "$BIN" "$APP/Contents/MacOS/netllm-mac"
chmod +x "$APP/Contents/MacOS/netllm-mac"

# Python layers
rsync -a "$EXPORT/" "$PYTHON_RES/"

# Workspace packages (pure source)
for pkg in netllm-core netllm-cli netllm-agent netllm-discovery netllm-sdk-openai netllm-sdk-anthropic; do
  src="$ROOT/packages/$pkg/src"
  if [[ -d "$src" ]]; then
    mkdir -p "$PACKAGES_RES/$pkg/src"
    rsync -a "$src/" "$PACKAGES_RES/$pkg/src/"
  fi
done
if [[ -d "$ROOT/src/netllm" ]]; then
  mkdir -p "$PACKAGES_RES/netllm"
  rsync -a "$ROOT/src/netllm/" "$PACKAGES_RES/netllm/"
fi

# CLI wrapper
cat > "$APP/Contents/MacOS/netllm-cli" <<'WRAPPER'
#!/bin/sh
set -e
DIR="$(cd "$(dirname "$0")/../Resources/Python" && pwd)"
PKGS_BASE="$(cd "$(dirname "$0")/../Resources/netllm_packages" && pwd)"
export PYTHONHOME="$DIR/cpython-3.11"
FW="$DIR/framework-framework-netllm/lib/python3.11/site-packages"
PYPATH="$FW"
for src in "$PKGS_BASE"/*/src "$PKGS_BASE"/netllm; do
  if [ -d "$src" ]; then
    PYPATH="$src:$PYPATH"
  fi
done
export PYTHONPATH="$PYPATH"
export PYTHONDONTWRITEBYTECODE=1
export NETLLM_BUNDLE_PATH="$(cd "$(dirname "$0")/../.." && pwd)"
exec "$PYTHONHOME/bin/python3" -m netllm_cli.main "$@"
WRAPPER
chmod +x "$APP/Contents/MacOS/netllm-cli"

# Bundled maintainer scripts (DMG upgrade / future in-app updater)
SCRIPTS_RES="$APP/Contents/Resources/Scripts"
mkdir -p "$SCRIPTS_RES"
for script in macos-app-install.sh mount-dmg.sh; do
  src="$ROOT/packaging/scripts/$script"
  if [[ -f "$src" ]]; then
    cp "$src" "$SCRIPTS_RES/$script"
    chmod +x "$SCRIPTS_RES/$script"
  fi
done

# Brand assets + app icon
BRAND_SRC="$MAC_DIR/build/Brand"
ICNS_SRC="$MAC_DIR/build/AppIcon.icns"
CAR_SRC="$MAC_DIR/build/Assets.car"
rsync -a "$BRAND_SRC/" "$APP/Contents/Resources/Brand/"
cp "$ICNS_SRC" "$APP/Contents/Resources/AppIcon.icns"
[[ -f "$CAR_SRC" ]] && cp "$CAR_SRC" "$APP/Contents/Resources/Assets.car"

# Info.plist
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key><string>en</string>
  <key>CFBundleExecutable</key><string>netllm-mac</string>
  <key>CFBundleIdentifier</key><string>com.netllm.mac</string>
  <key>CFBundleName</key><string>llm-swarm-router</string>
  <key>CFBundleDisplayName</key><string>llm-swarm-router</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundleIconName</key><string>AppIcon</string>
  <key>CFBundleShortVersionString</key><string>$MARKETING_VERSION</string>
  <key>CFBundleVersion</key><string>$CURRENT_PROJECT_VERSION</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>LSUIElement</key><true/>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

# Ad-hoc sign embedded Mach-O + bundle
echo "==> Codesigning"
find "$APP" -type f \( -name '*.dylib' -o -name '*.so' -o -perm -111 \) -print0 2>/dev/null | \
  while IFS= read -r -d '' f; do codesign -f -s - "$f" 2>/dev/null || true; done
codesign -f -s - "$APP" 2>/dev/null || true
xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true

echo "==> Staged: $APP"
echo "    open \"$APP\"                    # launch menubar app"
echo "    packaging/scripts/create-dmg.sh           # dist/llm-swarm-router.dmg"
echo "    scripts/emulate-user-install-mac.sh       # DMG → /Applications (user flow)"
