#!/usr/bin/env bash
# Menubar supervisor lifecycle: quit cleanup, control socket, --replace, port release.
# macOS only. Requires release Stage app (build.sh release).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="${NETLLM_APP:-$ROOT/apps/netllm-mac/build/Stage/llm-swarm-router.app}"
CLI="$APP/Contents/MacOS/netllm-cli"
PORT="${NETLLM_LIFECYCLE_PORT:-11400}"
BASE="http://127.0.0.1:${PORT}"
CFG="${HOME}/.config/netllm/config.toml"
CFG_BAK=""
ORPHAN_PID=""
APP_LAUNCHED=0

fail() { echo "FAIL: $*" >&2; exit 1; }
ok() { echo "OK: $*"; }

[[ "$(uname -s)" == "Darwin" ]] || fail "macOS only"
[[ -d "$APP" ]] || fail "Missing app at $APP — run apps/netllm-mac/Scripts/build.sh release"
[[ -x "$CLI" ]] || fail "Missing bundled CLI at $CLI"

port_pids() {
  lsof -ti ":${PORT}" 2>/dev/null || true
}

port_empty() {
  [[ -z "$(port_pids)" ]]
}

wait_port_empty() {
  local attempts="${1:-120}"
  local i
  for i in $(seq 1 "$attempts"); do
    port_empty && return 0
    sleep 0.25
  done
  return 1
}

wait_single_listener() {
  local attempts="${1:-40}"
  local i pid_count
  for i in $(seq 1 "$attempts"); do
    pid_count="$(port_pids | wc -l | tr -d ' ')"
    [[ "$pid_count" -eq 1 ]] && return 0
    sleep 0.25
  done
  return 1
}

health_ok() {
  curl -sf "${BASE}/health" >/dev/null 2>&1
}

app_control() {
  local cmd="$1"
  local launch="${2:-1}"
  (
    cd "$ROOT"
    APP_CONTROL_LAUNCH="$launch" uv run python - <<'PY' "$cmd"
import json
import os
import sys

from netllm_cli.lifecycle.darwin import send_app_control, send_app_control_with_launch

cmd = sys.argv[1]
launch = os.environ.get("APP_CONTROL_LAUNCH", "1") == "1"
try:
    if launch:
        resp = send_app_control_with_launch(cmd, timeout=60.0)
    else:
        resp = send_app_control(cmd, timeout=10.0)
    print(json.dumps(resp))
except Exception as exc:
    print(json.dumps({"ok": False, "state": "unknown", "message": str(exc)}))
PY
  )
}

wait_control_state() {
  local want="$1"
  local deadline=$((SECONDS + 45))
  while (( SECONDS < deadline )); do
    local resp state
    resp="$(app_control status 0 2>/dev/null || echo '{}')"
    state="$(echo "$resp" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('state',''))" 2>/dev/null || echo "")"
    if [[ "$state" == "$want" ]]; then
      echo "$resp"
      return 0
    fi
    sleep 0.5
  done
  fail "timed out waiting for control state '$want' (last: $resp)"
}

menubar_app_pgrep() {
  pgrep -f "llm-swarm-router.app/Contents/MacOS/" 2>/dev/null || true
}

quit_menubar_app() {
  # Use NSApp.terminate path (AppleScript quit). SIGTERM skips RunLoop and can
  # deadlock applicationWillTerminate before server.stop() runs.
  if [[ -n "$(menubar_app_pgrep)" ]]; then
    osascript -e 'tell application "llm-swarm-router" to quit' 2>/dev/null || true
  fi
  # AppDelegate allows up to 15s for server.stop() + port release.
  local deadline=$((SECONDS + 35))
  while (( SECONDS < deadline )); do
    [[ -z "$(menubar_app_pgrep)" ]] && return 0
    sleep 0.5
  done
  pkill -f "llm-swarm-router.app/Contents/MacOS/" 2>/dev/null || true
  sleep 2
}

launch_menubar_app() {
  /usr/bin/open -gj "$APP"
  APP_LAUNCHED=1
  sleep 2
}

backup_config() {
  [[ -n "$CFG_BAK" ]] && return 0
  if [[ -f "$CFG" ]]; then
    CFG_BAK="$(mktemp /tmp/netllm-config.bak.XXXXXX)"
    cp "$CFG" "$CFG_BAK"
  fi
}

restore_config() {
  if [[ -n "$CFG_BAK" && -f "$CFG_BAK" ]]; then
    mkdir -p "$(dirname "$CFG")"
    cp "$CFG_BAK" "$CFG"
    rm -f "$CFG_BAK"
    CFG_BAK=""
  fi
}

ensure_config_listen() {
  backup_config
  mkdir -p "$(dirname "$CFG")"
  if [[ -f "$CFG" ]]; then
    if grep -q '^\[ui\]' "$CFG"; then
      if grep -q 'auto_start_on_launch' "$CFG"; then
        sed -i '' 's/^[[:space:]]*auto_start_on_launch.*/auto_start_on_launch = false/' "$CFG" || true
      else
        awk '/^\[ui\]/{print; print "auto_start_on_launch = false"; next}1' "$CFG" >"${CFG}.tmp" && mv "${CFG}.tmp" "$CFG"
      fi
    fi
    if grep -q '^listen' "$CFG"; then
      sed -i '' "s|^[[:space:]]*listen.*|listen = \"127.0.0.1:${PORT}\"|" "$CFG" || true
    fi
  else
    cat >"$CFG" <<EOF
[agent]
listen = "127.0.0.1:${PORT}"
role = "peer"
advertise = false

[discovery]
providers = ["omlx", "ollama", "lmstudio"]

[swarm]
mdns = false

[routing]
default_strategy = "local_first"

[ui]
auto_start_on_launch = false
check_for_updates_automatically = true
EOF
  fi
}

start_orphan_agent() {
  stop_orphan_agent
  "$CLI" serve -q --config "$CFG" >/tmp/netllm-lifecycle-orphan.log 2>&1 &
  ORPHAN_PID=$!
  local i
  for i in $(seq 1 40); do
    health_ok && return 0
    sleep 0.25
  done
  fail "orphan agent did not become healthy — log: /tmp/netllm-lifecycle-orphan.log"
}

stop_orphan_agent() {
  if [[ -n "${ORPHAN_PID:-}" ]]; then
    kill "$ORPHAN_PID" 2>/dev/null || true
    wait "$ORPHAN_PID" 2>/dev/null || true
    ORPHAN_PID=""
  fi
  if ! port_empty; then
    lsof -ti ":${PORT}" | xargs kill -9 2>/dev/null || true
    wait_port_empty || true
  fi
}

cleanup() {
  quit_menubar_app
  stop_orphan_agent
  restore_config
  wait_port_empty || true
}
trap cleanup EXIT

echo "==> L1 clean quit after running"
stop_orphan_agent
ensure_config_listen
sed -i '' 's/^[[:space:]]*auto_start_on_launch.*/auto_start_on_launch = true/' "$CFG" || true
launch_menubar_app
resp="$(app_control start)"
echo "$resp" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d.get('ok'), d" || fail "start: $resp"
wait_control_state running >/dev/null
health_ok || fail "health not OK after start"
quit_menubar_app
APP_LAUNCHED=0
wait_port_empty 160 || fail "L1: port ${PORT} still in use after quit ($(port_pids))"
ok "L1 quit releases port after running"

echo "==> L2 quit with orphan (supervisor stopped)"
stop_orphan_agent
ensure_config_listen
start_orphan_agent
health_ok || fail "L2 setup: orphan not healthy"
launch_menubar_app
sleep 2
resp="$(app_control status 0)"
state="$(echo "$resp" | python3 -c "import json,sys; print(json.load(sys.stdin).get('state',''))")"
[[ "$state" == "stopped" || "$state" == "failed" ]] || fail "L2: expected stopped/failed with orphan, got state=$state resp=$resp"
quit_menubar_app
APP_LAUNCHED=0
wait_port_empty 160 || fail "L2: port ${PORT} still in use after quit with orphan ($(port_pids))"
ok "L2 quit releases orphan on port"

echo "==> L3 stop via control socket"
stop_orphan_agent
launch_menubar_app
resp="$(app_control start)"
echo "$resp" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d.get('ok'), d" || fail "L3 start: $resp"
wait_control_state running >/dev/null
resp="$(app_control stop 0)"
echo "$resp" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d.get('ok'), d" || fail "L3 stop: $resp"
wait_port_empty || fail "L3: port not free after stop ($(port_pids))"
ok "L3 control socket stop frees port"

echo "==> L4 restart via control socket"
resp="$(app_control restart 0)"
echo "$resp" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d.get('ok'), d" || fail "L4 restart: $resp"
wait_control_state running >/dev/null
health_ok || fail "L4: health after restart"
wait_single_listener 60 || fail "L4: expected 1 listener, got pids: $(port_pids)"
ok "L4 restart leaves single listener"

echo "==> L5 serve --replace adopts occupied port"
app_control stop 0 >/dev/null || true
wait_port_empty || true
start_orphan_agent
launch_menubar_app
resp="$(app_control start)"
echo "$resp" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d.get('ok'), d" || fail "L5 start: $resp"
wait_control_state running >/dev/null
health_ok || fail "L5: health after replace start"
wait_single_listener 60 || fail "L5: expected 1 listener after --replace, got pids: $(port_pids)"
ok "L5 --replace leaves single healthy listener"

quit_menubar_app
APP_LAUNCHED=0
wait_port_empty || fail "post-L5 cleanup"

echo "ALL LIFECYCLE CHECKS PASSED"
