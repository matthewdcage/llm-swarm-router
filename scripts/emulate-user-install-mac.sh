#!/usr/bin/env bash
# Build and install like an end user: Stage app → Applications → launch.
# Uses macos-app-install.sh --source (Gatekeeper-safe on macOS 26+; ad-hoc DMGs fail).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INSTALLER="$ROOT/packaging/scripts/macos-app-install.sh"
STAGE_APP="$ROOT/apps/netllm-mac/build/Stage/llm-swarm-router.app"

echo "==> Native install rehearsal for llm-swarm-router"
echo

echo "==> Building release Stage app"
"$ROOT/apps/netllm-mac/Scripts/build.sh" release

[[ -d "$STAGE_APP" ]] || { echo "Stage app missing: $STAGE_APP" >&2; exit 1; }

chmod +x "$INSTALLER"
"$INSTALLER" --source "$STAGE_APP"

cat <<EOF
To repeat this flow later:
  $ROOT/scripts/emulate-user-install-mac.sh

Optional maintainer DMG (ad-hoc until notarized):
  $ROOT/packaging/scripts/create-dmg.sh
  $ROOT/scripts/upgrade-mac-app.sh $ROOT/dist/llm-swarm-router.dmg
EOF
