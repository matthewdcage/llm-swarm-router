#!/usr/bin/env bash
# Live routing smoke + pressure tests against a running netllm gateway.
set -euo pipefail

BASE="${NETLLM_BASE:-http://127.0.0.1:11400}"
TOKEN="${NETLLM_CLUSTER_TOKEN:-1134}"
AUTH=()
if [[ -n "$TOKEN" ]]; then
  AUTH=(-H "Authorization: Bearer $TOKEN")
fi

PASS=0
FAIL=0
ISSUES=()

# Menubar / bundled-app PYTHONHOME in the shell breaks system python3.
pyjson() { env -u PYTHONHOME -u PYTHONPATH python3 "$@"; }

log() { printf '%s\n' "$*"; }
pass() { PASS=$((PASS + 1)); log "  PASS: $1"; }
fail() { FAIL=$((FAIL + 1)); ISSUES+=("$1"); log "  FAIL: $1"; }

chat() {
  local model="$1"
  shift
  curl -sf --max-time 180 "${AUTH[@]}" -H "Content-Type: application/json" \
    -d "{\"model\":\"$model\",\"messages\":[{\"role\":\"user\",\"content\":\"Say hi in 3 words\"}],\"max_tokens\":12$*}" \
    "$BASE/v1/chat/completions" 2>&1
}

chat_with_headers() {
  local model="$1"
  local extra_hdr="$2"
  shift 2
  curl -sf --max-time 180 "${AUTH[@]}" -H "Content-Type: application/json" -H "$extra_hdr" \
    -d "{\"model\":\"$model\",\"messages\":[{\"role\":\"user\",\"content\":\"Say hi\"}],\"max_tokens\":8$*}" \
    "$BASE/v1/chat/completions" 2>&1
}

status_routed() {
  curl -sf "$BASE/netllm/v1/telemetry?scopes=router" | pyjson -c "
import sys, json
d = json.load(sys.stdin).get('router', {})
routed = d.get('routed_requests', {})
for bid, n in sorted(routed.items(), key=lambda x: -x[1]):
    print(f'{bid}\trouted={n}')
print(f\"session_requests={d.get('session',{}).get('requests',0)}\")
"
}

log "=== netllm live routing smoke ==="
log "Base: $BASE  Token: ${TOKEN:+set}"

log ""
log "--- health / version ---"
ver=$(defaults read /Applications/llm-swarm-router.app/Contents/Info CFBundleShortVersionString 2>/dev/null || echo unknown)
curl -sf "$BASE/health" >/dev/null && pass "health ok (app $ver)" || fail "health unreachable"

log ""
log "--- qwen3-next-80b basic ---"
if out=$(chat "qwen3-next-80b" 2>&1); then
  model_used=$(echo "$out" | pyjson -c "import sys,json; print(json.load(sys.stdin).get('model','?'))" 2>/dev/null || echo "?")
  pass "qwen3-next-80b chat (model=$model_used)"
else
  fail "qwen3-next-80b chat: $out"
fi

log ""
log "--- qwen3-next-80b + top_k (provider payload fix) ---"
if out=$(chat "qwen3-next-80b" ',"top_k":40' 2>&1); then
  pass "qwen3-next-80b + top_k"
else
  if echo "$out" | rg -q "top_k|unexpected|extra"; then
    fail "qwen3-next-80b + top_k SDK reject: $out"
  else
    fail "qwen3-next-80b + top_k: $out"
  fi
fi

log ""
log "--- qwen-next-80b alias (expect success when alias configured) ---"
code=$(curl -s -o /tmp/qwen-alias.json -w '%{http_code}' --max-time 180 "${AUTH[@]}" -H "Content-Type: application/json" \
  -d '{"model":"qwen-next-80b","messages":[{"role":"user","content":"hi"}],"max_tokens":5}' \
  "$BASE/v1/chat/completions" || true)
if [[ "$code" == "200" ]]; then
  model_used=$(pyjson -c "import json; print(json.load(open('/tmp/qwen-alias.json')).get('model','?'))" 2>/dev/null || echo "?")
  pass "qwen-next-80b alias resolves (HTTP 200, model=$model_used)"
elif [[ "$code" == "404" ]] || [[ "$code" == "400" ]]; then
  pass "qwen-next-80b returns $code (no alias — add routing.model_aliases)"
else
  fail "qwen-next-80b unexpected HTTP $code"
fi

log ""
log "--- gemma4:26b (must not substitute qwen on wrong host) ---"
if out=$(chat "gemma4:26b" 2>&1); then
  model_used=$(echo "$out" | pyjson -c "import sys,json; print(json.load(sys.stdin).get('model','?'))" 2>/dev/null || echo "?")
  if echo "$model_used" | rg -qi "qwen"; then
    fail "gemma4:26b routed to qwen model ($model_used) — pool steal bug"
  else
    pass "gemma4:26b chat (model=$model_used)"
  fi
else
  fail "gemma4:26b chat: $out"
fi

log ""
log "--- pin peer:93595960 for qwen3-next-80b ---"
if out=$(chat_with_headers "qwen3-next-80b" "x-netllm-backend: peer:93595960" 2>&1); then
  pass "pinned qwen to Linux peer"
else
  fail "pin peer:93595960: $out"
fi

log ""
log "--- routed counts after smoke ---"
status_routed

log ""
log "--- pressure: 6 concurrent (3 qwen + 3 gemma, max_tokens=5) ---"
tmpdir=$(mktemp -d)
pressure_chat() {
  local model="$1" out="$2"
  curl -sf --max-time 300 "${AUTH[@]}" -H "Content-Type: application/json" \
    -d "{\"model\":\"$model\",\"messages\":[{\"role\":\"user\",\"content\":\"ok\"}],\"max_tokens\":5}" \
    "$BASE/v1/chat/completions" >"$out" 2>/dev/null || echo fail >"$out"
}
for i in 1 2 3; do
  ( pressure_chat "qwen3-next-80b" "$tmpdir/qwen-$i.json" ) &
  ( pressure_chat "gemma4:26b" "$tmpdir/gemma-$i.json" ) &
done
wait
qwen_fail=$(rg -l '^fail$' "$tmpdir"/qwen-*.json 2>/dev/null | wc -l | tr -d ' ' || true)
gemma_fail=$(rg -l '^fail$' "$tmpdir"/gemma-*.json 2>/dev/null | wc -l | tr -d ' ' || true)
if [[ "$qwen_fail" -eq 0 ]] && [[ "$gemma_fail" -eq 0 ]]; then
  pass "pressure 6/6 ok"
else
  fail "pressure failures qwen=$qwen_fail gemma=$gemma_fail"
fi
rm -rf "$tmpdir"

log ""
log "--- routed counts after pressure ---"
status_routed

log ""
log "=== SUMMARY: $PASS passed, $FAIL failed ==="
if [[ ${#ISSUES[@]} -gt 0 ]]; then
  log "Issues:"
  for i in "${ISSUES[@]}"; do log "  - $i"; done
  exit 1
fi
