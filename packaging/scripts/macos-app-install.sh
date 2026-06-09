#!/usr/bin/env bash
# Install or upgrade llm-swarm-router.app with clean teardown of stale agents.
#
# Stops menubar app, Homebrew background agent, and orphaned serve processes so
# the new bundle owns the listen port and /ui/ is served from embedded static files.
#
# Usage:
#   macos-app-install.sh --dmg /path/to/llm-swarm-router.dmg
#   macos-app-install.sh --source /path/to/llm-swarm-router.app
#
# Options:
#   --install-path PATH   Default: /Applications/llm-swarm-router.app
#   --no-launch           Install only; do not open the app
#   --no-verify           Skip post-launch /health and /ui/ checks
#   --no-stop             Skip process teardown (maintainer debugging only)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
MOUNT_DMG="$ROOT/packaging/scripts/mount-dmg.sh"

APP_NAME="llm-swarm-router.app"
LEGACY_APP_NAME="netllm-mac.app"
DEFAULT_INSTALL="/Applications/$APP_NAME"
LEGACY_INSTALL="/Applications/$LEGACY_APP_NAME"

DMG=""
SOURCE=""
INSTALL_PATH="$DEFAULT_INSTALL"
DO_LAUNCH=1
DO_VERIFY=1
DO_STOP=1

usage() {
  sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dmg) DMG="$2"; shift 2 ;;
    --source) SOURCE="$2"; shift 2 ;;
    --install-path) INSTALL_PATH="$2"; shift 2 ;;
    --no-launch) DO_LAUNCH=0; shift ;;
    --no-verify) DO_VERIFY=0; shift ;;
    --no-stop) DO_STOP=0; shift ;;
    -h|--help) usage 0 ;;
    *) echo "Unknown option: $1" >&2; usage 1 ;;
  esac
done

[[ -n "$DMG" || -n "$SOURCE" ]] || usage 1
[[ -z "$DMG" || -z "$SOURCE" ]] || {
  echo "Use only one of --dmg or --source" >&2
  exit 1
}

agent_listen_port() {
  local config="${HOME}/.config/netllm/config.toml"
  local listen="127.0.0.1:11400"
  if [[ -f "$config" ]]; then
    listen="$(
      grep -E '^\s*listen\s*=' "$config" 2>/dev/null | head -1 \
        | sed -E 's/^[[:space:]]*listen[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/' \
        || echo "127.0.0.1:11400"
    )"
  fi
  local port="${listen##*:}"
  if [[ -z "$port" || "$port" == "$listen" ]]; then
    port="11400"
  fi
  echo "$port"
}

process_looks_like_netllm() {
  local pid="$1"
  local cmd
  cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  [[ -n "$cmd" ]] || return 1
  [[ "$cmd" == *netllm* || "$cmd" == *llm-swarm-router* ]]
}

wait_for_exit() {
  local pattern="$1"
  local seconds="${2:-15}"
  local i
  for ((i = 0; i < seconds * 2; i++)); do
    pgrep -f "$pattern" >/dev/null 2>&1 || return 0
    sleep 0.5
  done
  return 1
}

wait_port_free() {
  local port="$1"
  local seconds="${2:-20}"
  local i
  for ((i = 0; i < seconds * 2; i++)); do
    if ! lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.5
  done
  return 1
}

stop_homebrew_agent() {
  if ! command -v brew >/dev/null 2>&1; then
    return 0
  fi
  if brew services list 2>/dev/null | grep -qE '^netllm[[:space:]]+started'; then
    echo "==> Stopping Homebrew netllm service (avoids port conflict with menubar app)"
    brew services stop netllm 2>/dev/null || true
  fi
}

stop_menubar_apps() {
  echo "==> Quitting menubar app (graceful)"
  osascript -e 'tell application "llm-swarm-router" to quit' 2>/dev/null || true
  osascript -e 'tell application "netllm-mac" to quit' 2>/dev/null || true
  wait_for_exit "Contents/MacOS/netllm-mac" 8 || true

  if pgrep -f "Contents/MacOS/netllm-mac" >/dev/null 2>&1; then
    echo "==> Sending SIGTERM to remaining menubar processes"
    pkill -TERM -f "Contents/MacOS/netllm-mac" 2>/dev/null || true
    wait_for_exit "Contents/MacOS/netllm-mac" 5 || true
  fi
  if pgrep -f "Contents/MacOS/netllm-mac" >/dev/null 2>&1; then
    echo "==> Sending SIGKILL to stubborn menubar processes"
    pkill -KILL -f "Contents/MacOS/netllm-mac" 2>/dev/null || true
    wait_for_exit "Contents/MacOS/netllm-mac" 3 || true
  fi
}

stop_orphan_agents() {
  local port="$1"
  echo "==> Clearing orphaned netllm agents on port $port"

  pkill -TERM -f "Contents/MacOS/netllm-cli.* serve" 2>/dev/null || true
  pkill -TERM -f "netllm_cli\.main.*serve" 2>/dev/null || true
  pkill -TERM -f "[/]netllm serve" 2>/dev/null || true
  sleep 1

  local pid
  while read -r pid; do
    [[ -n "$pid" ]] || continue
    if process_looks_like_netllm "$pid"; then
      kill -TERM "$pid" 2>/dev/null || true
    fi
  done < <(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)

  sleep 1
  while read -r pid; do
    [[ -n "$pid" ]] || continue
    if process_looks_like_netllm "$pid"; then
      kill -KILL "$pid" 2>/dev/null || true
    fi
  done < <(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)

  if ! wait_port_free "$port" 20; then
    echo "WARN: port $port still in use after cleanup:" >&2
    lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
    echo "      Stop the process above manually, then re-run this script." >&2
    exit 1
  fi
  echo "==> Port $port is free"
}

stop_netllm_stack() {
  stop_homebrew_agent
  stop_menubar_apps
  stop_orphan_agents "$(agent_listen_port)"
}

verify_bundle_contents() {
  local app="$1"
  local static="$app/Contents/Resources/netllm_packages/netllm-agent/src/netllm_agent/static"
  local index="$static/index.html"
  local version
  version="$(defaults read "$app/Contents/Info" CFBundleShortVersionString 2>/dev/null || echo unknown)"

  [[ -d "$app" ]] || {
    echo "FAIL: install path missing: $app" >&2
    exit 1
  }
  [[ -f "$index" ]] || {
    echo "FAIL: dashboard static files missing in bundle (expected $index)" >&2
    echo "      This build cannot serve /ui/ — rebuild or use a newer release DMG." >&2
    exit 1
  }
  echo "==> Installed app version $version with web dashboard static assets"
}

verify_agent_endpoints() {
  local port="$1"
  local base="http://127.0.0.1:${port}"
  local i code

  echo "==> Waiting for agent on $base"
  for ((i = 0; i < 40; i++)); do
    if curl -sf "${base}/health" >/dev/null 2>&1; then
      break
    fi
    sleep 0.5
  done
  curl -sf "${base}/health" >/dev/null || {
    echo "FAIL: agent not healthy at ${base}/health after launch" >&2
    echo "      Open the menubar app and choose Start Agent, or check logs in Settings." >&2
    exit 1
  }

  if ! curl -sf "${base}/" | grep -q '"dashboard"'; then
    echo "FAIL: agent on $port is too old (root JSON lacks dashboard field)" >&2
    echo "      Another netllm process may still own the port — re-run without --no-stop." >&2
    exit 1
  fi

  code="$(curl -s -o /dev/null -w "%{http_code}" "${base}/ui/")"
  [[ "$code" == "200" ]] || {
    echo "FAIL: GET ${base}/ui/ returned HTTP $code (expected 200)" >&2
    exit 1
  }
  echo "==> Verified ${base}/ui/ (HTTP 200)"
}

# Global set by resolve_source_app; trap registered in main script scope so
# $DMG_MOUNT is in scope when the EXIT trap fires (avoids "unbound variable"
# from local vars going out of scope after a $() subshell returns).
DMG_MOUNT=""
SOURCE_APP=""

resolve_source_app() {
  if [[ -n "$SOURCE" ]]; then
    [[ -d "$SOURCE" ]] || {
      echo "Source app not found: $SOURCE" >&2
      exit 1
    }
    SOURCE_APP="$SOURCE"
    return 0
  fi

  [[ -f "$DMG" ]] || {
    echo "DMG not found: $DMG" >&2
    exit 1
  }

  echo "==> Mounting DMG"
  if ! DMG_MOUNT="$("$MOUNT_DMG" "$DMG")"; then
    echo "Failed to mount DMG: $DMG" >&2
    exit 1
  fi

  local source="$DMG_MOUNT/$APP_NAME"
  [[ -d "$source" ]] || source="$DMG_MOUNT/$LEGACY_APP_NAME"
  [[ -d "$source" ]] || {
    echo "App not found on DMG volume ($DMG_MOUNT). Contents:" >&2
    ls -la "$DMG_MOUNT" >&2 || true
    exit 1
  }
  SOURCE_APP="$source"
}

if [[ "$DO_STOP" == 1 ]]; then
  stop_netllm_stack
else
  echo "WARN: --no-stop set; stale processes may cause /ui/ 404 after install" >&2
fi

resolve_source_app
# Trap registered here in main script scope so $DMG_MOUNT is always in scope.
[[ -z "$DMG_MOUNT" ]] || trap 'hdiutil detach "$DMG_MOUNT" -quiet 2>/dev/null || true' EXIT

if [[ -d "$LEGACY_INSTALL" && "$INSTALL_PATH" != "$LEGACY_INSTALL" ]]; then
  echo "==> Removing legacy /Applications install ($LEGACY_APP_NAME)"
  rm -rf "$LEGACY_INSTALL"
fi
if [[ -d "$INSTALL_PATH" ]]; then
  echo "==> Replacing $INSTALL_PATH"
  rm -rf "$INSTALL_PATH"
fi

echo "==> Copying app to $INSTALL_PATH"
ditto "$SOURCE_APP" "$INSTALL_PATH"
xattr -dr com.apple.quarantine "$INSTALL_PATH" 2>/dev/null || true

verify_bundle_contents "$INSTALL_PATH"

if [[ "$DO_LAUNCH" == 1 ]]; then
  echo "==> Launching $INSTALL_PATH"
  open -a "$INSTALL_PATH"
  if [[ "$DO_VERIFY" == 1 ]]; then
    verify_agent_endpoints "$(agent_listen_port)"
  fi
fi

cat <<EOF

Done. llm-swarm-router is installed at:
  $INSTALL_PATH

Dashboard: http://127.0.0.1:$(agent_listen_port)/ui/
Menubar: Open Dashboard · Settings… · About (version)

If macOS blocks the first launch: right-click the app in Applications → Open once.

EOF
