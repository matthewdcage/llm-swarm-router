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
#   --in-app-update       Menubar-driven update: wait for caller PID, skip osascript quit
#   --wait-for-pid PID    Wait for this process to exit before replacing the .app
#   --cache-cleanup DIR   Remove verified update DMGs from this cache directory after install
#   --log-file PATH       Append installer output to this log file
#   --no-launch           Install only; do not open the app
#   --no-verify           Skip post-launch /health and /ui/ checks
#   --no-stop             Skip process teardown (maintainer debugging only)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MOUNT_DMG="$SCRIPT_DIR/mount-dmg.sh"
# Repo dev fallback only (packaging/scripts/ → repo root).
if [[ ! -x "$MOUNT_DMG" ]]; then
  repo_root="$(cd "$SCRIPT_DIR/../.." && pwd)"
  if [[ -x "$repo_root/packaging/scripts/mount-dmg.sh" ]]; then
    MOUNT_DMG="$repo_root/packaging/scripts/mount-dmg.sh"
  fi
fi

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
IN_APP_UPDATE=0
WAIT_FOR_PID=""
CACHE_CLEANUP=""
INSTALL_LOG=""

usage() {
  sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

log_line() {
  local line="==> $*"
  echo "$line"
  if [[ -n "$INSTALL_LOG" ]]; then
    mkdir -p "$(dirname "$INSTALL_LOG")"
    printf '%s\n' "$line" >> "$INSTALL_LOG"
  fi
}

install_fail() {
  local msg="$1"
  echo "FAIL: $msg" >&2
  if [[ -n "$INSTALL_LOG" ]]; then
    printf 'FAIL: %s\n' "$msg" >> "$INSTALL_LOG"
  fi
  if [[ "$IN_APP_UPDATE" == 1 ]]; then
    osascript -e "display alert \"Update install failed\" message \"$msg\" as critical" 2>/dev/null || true
  fi
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dmg) DMG="$2"; shift 2 ;;
    --source) SOURCE="$2"; shift 2 ;;
    --install-path) INSTALL_PATH="$2"; shift 2 ;;
    --in-app-update) IN_APP_UPDATE=1; shift ;;
    --wait-for-pid) WAIT_FOR_PID="$2"; shift 2 ;;
    --cache-cleanup) CACHE_CLEANUP="$2"; shift 2 ;;
    --log-file) INSTALL_LOG="$2"; shift 2 ;;
    --no-launch) DO_LAUNCH=0; shift ;;
    --no-verify) DO_VERIFY=0; shift ;;
    --no-stop) DO_STOP=0; shift ;;
    -h|--help) usage 0 ;;
    *) echo "Unknown option: $1" >&2; usage 1 ;;
  esac
done

[[ -n "$DMG" || -n "$SOURCE" ]] || usage 1
[[ -z "$DMG" || -z "$SOURCE" ]] || install_fail "Use only one of --dmg or --source"

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

wait_for_pid() {
  local pid="$1"
  local seconds="${2:-45}"
  [[ -n "$pid" ]] || return 0
  log_line "Waiting for menubar app (pid $pid) to exit"
  local i
  for ((i = 0; i < seconds * 2; i++)); do
    kill -0 "$pid" 2>/dev/null || {
      log_line "Menubar app exited"
      return 0
    }
    sleep 0.5
  done
  install_fail "Menubar app (pid $pid) still running after ${seconds}s"
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
    log_line "Stopping Homebrew netllm service (avoids port conflict with menubar app)"
    brew services stop netllm 2>/dev/null || true
  fi
}

stop_menubar_apps() {
  log_line "Quitting menubar app (graceful)"
  osascript -e 'tell application "llm-swarm-router" to quit' 2>/dev/null || true
  osascript -e 'tell application "netllm-mac" to quit' 2>/dev/null || true
  wait_for_exit "Contents/MacOS/netllm-mac" 8 || true
  wait_for_exit "Contents/MacOS/llm-swarm-router" 8 || true

  if pgrep -f "Contents/MacOS/netllm-mac" >/dev/null 2>&1 \
    || pgrep -f "Contents/MacOS/llm-swarm-router" >/dev/null 2>&1; then
    log_line "Sending SIGTERM to remaining menubar processes"
    pkill -TERM -f "Contents/MacOS/netllm-mac" 2>/dev/null || true
    pkill -TERM -f "Contents/MacOS/llm-swarm-router" 2>/dev/null || true
    wait_for_exit "Contents/MacOS/netllm-mac" 5 || true
    wait_for_exit "Contents/MacOS/llm-swarm-router" 5 || true
  fi
  if pgrep -f "Contents/MacOS/netllm-mac" >/dev/null 2>&1 \
    || pgrep -f "Contents/MacOS/llm-swarm-router" >/dev/null 2>&1; then
    log_line "Sending SIGKILL to stubborn menubar processes"
    pkill -KILL -f "Contents/MacOS/netllm-mac" 2>/dev/null || true
    pkill -KILL -f "Contents/MacOS/llm-swarm-router" 2>/dev/null || true
    wait_for_exit "Contents/MacOS/netllm-mac" 3 || true
    wait_for_exit "Contents/MacOS/llm-swarm-router" 3 || true
  fi
}

stop_orphan_agents() {
  local port="$1"
  log_line "Clearing orphaned netllm agents on port $port"

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
    install_fail "Port $port still in use. Stop the process above manually, then retry."
  fi
  log_line "Port $port is free"
}

stop_netllm_stack() {
  stop_homebrew_agent
  stop_menubar_apps
  stop_orphan_agents "$(agent_listen_port)"
}

stop_for_in_app_update() {
  local port
  port="$(agent_listen_port)"
  stop_homebrew_agent
  stop_orphan_agents "$port"
  wait_for_pid "$WAIT_FOR_PID" 45
}

cleanup_update_cache() {
  [[ -n "$CACHE_CLEANUP" ]] || return 0
  [[ -d "$CACHE_CLEANUP" ]] || return 0
  log_line "Removing update cache at $CACHE_CLEANUP"
  rm -f "$CACHE_CLEANUP"/*.dmg "$CACHE_CLEANUP"/*.download 2>/dev/null || true
}

verify_bundle_contents() {
  local app="$1"
  local static="$app/Contents/Resources/netllm_packages/netllm-agent/src/netllm_agent/static"
  local index="$static/index.html"
  local version
  version="$(defaults read "$app/Contents/Info" CFBundleShortVersionString 2>/dev/null || echo unknown)"

  [[ -d "$app" ]] || install_fail "install path missing: $app"
  [[ -f "$index" ]] || install_fail "dashboard static files missing in bundle (expected $index)"
  log_line "Installed app version $version with web dashboard static assets"
}

verify_agent_endpoints() {
  local port="$1"
  local base="http://127.0.0.1:${port}"
  local i code

  log_line "Waiting for agent on $base"
  for ((i = 0; i < 40; i++)); do
    if curl -sf "${base}/health" >/dev/null 2>&1; then
      break
    fi
    sleep 0.5
  done
  curl -sf "${base}/health" >/dev/null || install_fail "agent not healthy at ${base}/health after launch"

  if ! curl -sf "${base}/" | grep -q '"dashboard"'; then
    install_fail "agent on $port is too old (root JSON lacks dashboard field)"
  fi

  code="$(curl -s -o /dev/null -w "%{http_code}" "${base}/ui/")"
  [[ "$code" == "200" ]] || install_fail "GET ${base}/ui/ returned HTTP $code (expected 200)"
  log_line "Verified ${base}/ui/ (HTTP 200)"
}

DMG_MOUNT=""
SOURCE_APP=""

resolve_source_app() {
  if [[ -n "$SOURCE" ]]; then
    [[ -d "$SOURCE" ]] || install_fail "Source app not found: $SOURCE"
    SOURCE_APP="$SOURCE"
    return 0
  fi

  [[ -f "$DMG" ]] || install_fail "DMG not found: $DMG"
  if [[ ! -x "$MOUNT_DMG" ]]; then
    install_fail "mount-dmg.sh not found next to this script ($SCRIPT_DIR). Mount the DMG manually and pass --source /Volumes/.../llm-swarm-router.app"
  fi

  log_line "Mounting DMG"
  if ! DMG_MOUNT="$("$MOUNT_DMG" "$DMG")"; then
    install_fail "Failed to mount DMG: $DMG"
  fi

  local source="$DMG_MOUNT/$APP_NAME"
  [[ -d "$source" ]] || source="$DMG_MOUNT/$LEGACY_APP_NAME"
  [[ -d "$source" ]] || {
    echo "App not found on DMG volume ($DMG_MOUNT). Contents:" >&2
    ls -la "$DMG_MOUNT" >&2 || true
    install_fail "App bundle not found on mounted DMG"
  }
  SOURCE_APP="$source"
}

if [[ "$DO_STOP" == 1 ]]; then
  if [[ "$IN_APP_UPDATE" == 1 ]]; then
    stop_for_in_app_update
  else
    stop_netllm_stack
  fi
else
  echo "WARN: --no-stop set; stale processes may cause /ui/ 404 after install" >&2
fi

resolve_source_app
[[ -z "$DMG_MOUNT" ]] || trap 'hdiutil detach "$DMG_MOUNT" -quiet 2>/dev/null || true' EXIT

if [[ -d "$LEGACY_INSTALL" && "$INSTALL_PATH" != "$LEGACY_INSTALL" ]]; then
  log_line "Removing legacy /Applications install ($LEGACY_APP_NAME)"
  rm -rf "$LEGACY_INSTALL"
fi
if [[ -d "$INSTALL_PATH" ]]; then
  log_line "Replacing $INSTALL_PATH"
  rm -rf "$INSTALL_PATH"
fi

log_line "Copying app to $INSTALL_PATH"
ditto "$SOURCE_APP" "$INSTALL_PATH"
xattr -dr com.apple.quarantine "$INSTALL_PATH" 2>/dev/null || true

verify_bundle_contents "$INSTALL_PATH"
cleanup_update_cache

if [[ "$DO_LAUNCH" == 1 ]]; then
  log_line "Launching $INSTALL_PATH"
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
