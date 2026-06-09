#!/usr/bin/env bash
# Verify macos-app-install.sh accepts in-app update flags and rejects unknown options.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$ROOT/packaging/scripts/macos-app-install.sh"

[[ -x "$SCRIPT" ]] || {
  echo "FAIL: macos-app-install.sh missing" >&2
  exit 1
}

"$SCRIPT" --help >/dev/null

grep -q -- '--in-app-update' "$SCRIPT" || {
  echo "FAIL: macos-app-install.sh missing --in-app-update flag" >&2
  exit 1
}
grep -q -- '--wait-for-pid' "$SCRIPT" || {
  echo "FAIL: macos-app-install.sh missing --wait-for-pid flag" >&2
  exit 1
}

if "$SCRIPT" --not-a-real-flag 2>/dev/null; then
  echo "FAIL: unknown option should exit non-zero" >&2
  exit 1
fi

# Co-located mount helper (bundled app layout).
STAGE="$ROOT/apps/netllm-mac/build/Stage/llm-swarm-router.app/Contents/Resources/Scripts"
if [[ -d "$STAGE" ]]; then
  [[ -x "$STAGE/mount-dmg.sh" ]] || {
    echo "FAIL: bundled mount-dmg.sh missing" >&2
    exit 1
  }
fi

echo "OK: macos-app-install.sh accepts documented flags"
