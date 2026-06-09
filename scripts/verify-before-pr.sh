#!/usr/bin/env bash
# verify-before-pr.sh — local gate matching PR CI before push.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FULL=0
[[ "${1:-}" == "--full" ]] && FULL=1

fail() { echo "FAIL: $*" >&2; exit 1; }
ok() { echo "OK: $*"; }

echo "==> lint"
"$ROOT/scripts/ci.sh" lint

echo "==> test"
"$ROOT/scripts/ci.sh" test

if [[ "$(uname -s)" == "Darwin" ]]; then
  echo "==> swift release build (netllm-mac)"
  (cd "$ROOT/apps/netllm-mac" && swift build -c release) || fail "swift build -c release"

  if [[ "$FULL" == 1 ]]; then
    APP="$ROOT/apps/netllm-mac/build/Stage/llm-swarm-router.app"
    if [[ -d "$APP" ]]; then
      echo "==> menubar e2e"
      bash "$ROOT/scripts/test-menubar-e2e.sh"
      ok "menubar e2e"
    else
      echo "SKIP: menubar e2e (no $APP — run apps/netllm-mac/Scripts/build.sh release)"
    fi
  fi
else
  echo "SKIP: swift/menubar checks (not macOS)"
fi

ok "verify-before-pr complete"
