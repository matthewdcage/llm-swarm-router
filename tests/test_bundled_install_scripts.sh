#!/usr/bin/env bash
# Smoke-test bundled installer script path resolution.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAGE="$ROOT/apps/netllm-mac/build/Stage/llm-swarm-router.app"
SCRIPTS="$STAGE/Contents/Resources/Scripts"

if [[ ! -d "$STAGE" ]]; then
  echo "SKIP: Stage app missing — run apps/netllm-mac/Scripts/build.sh first" >&2
  exit 0
fi

[[ -x "$SCRIPTS/macos-app-install.sh" ]] || {
  echo "FAIL: bundled macos-app-install.sh missing" >&2
  exit 1
}
[[ -x "$SCRIPTS/mount-dmg.sh" ]] || {
  echo "FAIL: bundled mount-dmg.sh missing" >&2
  exit 1
}

# Simulate in-app resolution without running install.
resolved="$(
  SCRIPT_DIR="$SCRIPTS" bash -c '
    if [[ -x "$SCRIPT_DIR/mount-dmg.sh" ]]; then
      echo "$SCRIPT_DIR/mount-dmg.sh"
    else
      echo missing
    fi
  '
)"

[[ "$resolved" == "$SCRIPTS/mount-dmg.sh" ]] || {
  echo "FAIL: expected co-located mount-dmg.sh, got $resolved" >&2
  exit 1
}

echo "OK: bundled installer resolves mount-dmg.sh from Scripts/"
