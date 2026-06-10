#!/usr/bin/env bash
# Sign llm-swarm-router.app for distribution (Developer ID + hardened runtime).
#
# Requires:
#   CODESIGN_IDENTITY — e.g. "Developer ID Application: Your Name (TEAMID)"
#
# Usage:
#   CODESIGN_IDENTITY="Developer ID Application: …" \
#     packaging/scripts/codesign-mac-app.sh apps/netllm-mac/build/Stage/llm-swarm-router.app
set -euo pipefail

APP="${1:-}"
IDENTITY="${CODESIGN_IDENTITY:-}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENTITLEMENTS="${ENTITLEMENTS:-$SCRIPT_DIR/../macos/entitlements.plist}"

if [[ -z "$APP" || ! -d "$APP" ]]; then
  echo "Usage: CODESIGN_IDENTITY=\"Developer ID Application: …\" $0 /path/to/llm-swarm-router.app" >&2
  exit 1
fi

if [[ -z "$IDENTITY" ]]; then
  echo "CODESIGN_IDENTITY is not set — skipping Developer ID sign (build.sh uses ad-hoc for local dev)." >&2
  exit 0
fi

if [[ ! -f "$ENTITLEMENTS" ]]; then
  echo "Entitlements file not found: $ENTITLEMENTS" >&2
  exit 1
fi

echo "==> Signing with: $IDENTITY"

needs_entitlements() {
  local f="$1"
  [[ "$f" == *.dylib || "$f" == *.so ]] && return 1
  file "$f" 2>/dev/null | grep -q 'Mach-O.*executable'
}

sign_file() {
  local f="$1"
  if needs_entitlements "$f"; then
    codesign --force --options runtime --timestamp \
      --entitlements "$ENTITLEMENTS" \
      --sign "$IDENTITY" "$f"
  else
    codesign --force --options runtime --timestamp \
      --sign "$IDENTITY" "$f" 2>/dev/null \
      || codesign --force --options runtime --sign "$IDENTITY" "$f"
  fi
}

# Deepest Mach-O first (dylibs, helpers, python, CLI wrappers)
while IFS= read -r -d '' f; do
  sign_file "$f"
done < <(
  find "$APP" -type f \( -perm -111 -o -name '*.dylib' -o -name '*.so' \) -print0 2>/dev/null \
    | sort -rz
)

codesign --force --options runtime --timestamp \
  --entitlements "$ENTITLEMENTS" \
  --sign "$IDENTITY" "$APP"

codesign --verify --deep --strict --verbose=2 "$APP"
spctl -a -t exec -vv "$APP" 2>&1 || true
echo "==> Signed: $APP"
