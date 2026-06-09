#!/usr/bin/env bash
# End-to-end verification for netllm-mac.app bundle and bundled CLI.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="${NETLLM_APP:-$ROOT/apps/netllm-mac/build/Stage/llm-swarm-router.app}"
CLI="$APP/Contents/MacOS/netllm-cli"
TEST_PORT="${NETLLM_TEST_PORT:-11401}"
BASE="http://127.0.0.1:${TEST_PORT}"
CFG="${NETLLM_TEST_CONFIG:-/tmp/netllm-e2e-config.toml}"
PIDFILE="/tmp/netllm-e2e-agent.pid"
LOG="/tmp/netllm-e2e-agent.log"

fail() { echo "FAIL: $*" >&2; exit 1; }
ok() { echo "OK: $*"; }

cleanup() {
  if [[ -f "$PIDFILE" ]]; then
    kill "$(cat "$PIDFILE")" 2>/dev/null || true
    rm -f "$PIDFILE"
  fi
}
trap cleanup EXIT

[[ -d "$APP" ]] || fail "Missing app bundle at $APP — run apps/netllm-mac/Scripts/build.sh release"
[[ -x "$CLI" ]] || fail "Missing bundled CLI at $CLI"

echo "==> brand icons"
bash "$ROOT/scripts/test-brand-icons.sh"

echo "==> bundled install scripts (mount-dmg path, in-app flags)"
bash "$ROOT/tests/test_bundled_install_scripts.sh"
bash "$ROOT/tests/test_macos_app_install_flags.sh"

echo "==> bundled CLI version"
VER="$("$CLI" --version 2>/dev/null | tail -1)"
[[ "$VER" == netllm\ * ]] || fail "unexpected version: $VER"
ok "version $VER"

echo "==> write test config on port $TEST_PORT"
mkdir -p "$(dirname "$CFG")"
cat > "$CFG" <<EOF
[agent]
listen = "127.0.0.1:${TEST_PORT}"
role = "peer"
advertise = false

[discovery]
providers = ["omlx", "ollama", "lmstudio"]

[swarm]
mdns = false

[routing]
default_strategy = "local_first"
EOF

echo "==> start bundled agent (background)"
"$CLI" serve -q --config "$CFG" >"$LOG" 2>&1 &
echo $! >"$PIDFILE"

for i in $(seq 1 30); do
  if curl -sf "${BASE}/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done
curl -sf "${BASE}/health" >/dev/null || fail "agent not healthy — see $LOG"
ok "health ${BASE}/health"

echo "==> logs API"
curl -sf "${BASE}/netllm/v1/logs?tail=10" | rg -q '"log_file"' || fail "logs API"
ok "GET /netllm/v1/logs"

echo "==> API smoke"
curl -sf "${BASE}/" | rg -q 'netllm-agent' || fail "root help JSON"
ok "GET /"

curl -sf "${BASE}/netllm/v1/status" | rg -q '"agent_id"' || fail "status"
ok "GET /netllm/v1/status"

curl -sf "${BASE}/metrics" | rg -q 'netllm_' || fail "metrics"
ok "GET /metrics"

echo "==> bundled CLI status"
# JSON check avoids SIGPIPE from rg -q closing the pipe while Rich still writes.
curl -sf "${BASE}/netllm/v1/status" | rg -q '"agent_id"' || fail "cli status"
"$CLI" status --url "$BASE" >/dev/null 2>&1 || fail "cli status command"
ok "netllm status"

echo "==> lifecycle dispatch"
OUT="$(cd "$ROOT" && uv run netllm start --no-wait 2>&1 || true)"
if echo "$OUT" | rg -q 'netllm serve'; then
  ok "source install lifecycle hint"
elif echo "$OUT" | rg -q 'netllm agent'; then
  ok "macOS app lifecycle dispatch (control socket active)"
else
  fail "lifecycle dispatch unrecognized: $OUT"
fi

if [[ -d "$ROOT/packaging/_export/cpython-3.11" ]]; then
  ok "venvstacks export present"
else
  fail "venvstacks export missing"
fi

echo "==> DMG packaging"
bash "$ROOT/packaging/scripts/create-dmg.sh"
[[ -f "$ROOT/dist/llm-swarm-router.dmg" ]] || fail "DMG not created"
ok "dist/llm-swarm-router.dmg"

echo "ALL E2E CHECKS PASSED"
