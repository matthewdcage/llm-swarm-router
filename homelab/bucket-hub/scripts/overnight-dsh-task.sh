#!/usr/bin/env bash
# Run a one-shot DeepSeek Harness headless task on Ubuntu (netllm → Qwen3.8).
set -euo pipefail

TASK="${1:-}"
TIMEOUT_S="${2:-600}"
OUT="${3:-$HOME/homelab/data/logs/overnight-dsh-last.txt}"
NODE_DIR="${NODE_DIR:-$HOME/node-v22.19.0-linux-x64}"
export PATH="${NODE_DIR}/bin:${PATH}"
export DSH_HOME="${DSH_HOME:-$HOME/.dsh}"
export NETLLM_LOCAL_KEY="${NETLLM_LOCAL_KEY:-netllm-local}"

mkdir -p "$(dirname "$OUT")"

prompt() {
  case "$TASK" in
    research)
      cat <<'EOF'
You are the Bucket-HUB overnight research agent. Summarise what is new and worth watching in AI/ML homelab tooling from the last day. Be concise: 5-10 bullet points, each with a one-line why-it-matters. If you lack live data, state assumptions clearly.
EOF
      ;;
    logs)
      cat <<'EOF'
You are the Bucket-HUB overnight ops reviewer. Read the provided log summary and list: failures, VPN flaps, retry candidates, and one recommended action for tomorrow. Keep it under 15 bullets.
EOF
      ;;
    *)
      echo "Unknown task: $TASK" >&2
      exit 2
      ;;
  esac
}

PROMPT="$(prompt)"
if [[ -f "${4:-}" ]]; then
  PROMPT="${PROMPT}

--- DATA ---
$(cat "${4}")"
fi

log() { printf '[overnight-dsh] %s\n' "$*" >&2; }

log "Task=$TASK timeout=${TIMEOUT_S}s"

if ! curl -sf http://127.0.0.1:11400/health >/dev/null 2>&1; then
  log "netllm not reachable — is vLLM started?"
  exit 1
fi

# DeepSeek Harness headless profile: one task, print result, exit.
timeout "${TIMEOUT_S}" dsh --profile headless "$PROMPT" >"$OUT" 2>>"${OUT}.err" || {
  rc=$?
  if [[ $rc -eq 124 ]]; then
    log "Timed out after ${TIMEOUT_S}s (partial output may exist)"
    exit 124
  fi
  log "dsh headless failed (rc=$rc)"
  cat "${OUT}.err" >&2 || true
  exit "$rc"
}

log "Wrote $OUT"
head -c 200 "$OUT" >&2 || true
echo >&2
