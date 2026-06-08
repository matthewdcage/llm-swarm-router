#!/usr/bin/env bash
# agent-verify-setup.sh — verify netllm agent health and models (all platforms).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NETLLM="$ROOT/netllm"
BASE_URL="${NETLLM_BASE_URL:-http://127.0.0.1:11400}"

if command -v netllm >/dev/null 2>&1; then
  NETLLM_CMD=(netllm)
elif [[ -x "$NETLLM" ]]; then
  NETLLM_CMD=("$NETLLM")
else
  echo "error: netllm not on PATH and missing executable $NETLLM (run from repo root)" >&2
  exit 1
fi

echo "==> health check ($BASE_URL)"
if ! curl -sf "${BASE_URL}/health" >/dev/null; then
  echo "FAIL: agent not reachable at ${BASE_URL}/health" >&2
  echo "hint: run ./netllm serve in a dedicated terminal" >&2
  echo "hint: Linux package — systemctl --user enable --now netllm" >&2
  echo "hint: Windows zip — .\\install-service.ps1 then netllm start" >&2
  echo "hint: macOS — launch menubar app or brew services start netllm" >&2
  if [[ -d "/Applications/netllm-mac.app" ]] || [[ -d "/Applications/llm-swarm-router.app" ]]; then
    echo "hint: netllm menubar app found in Applications" >&2
  fi
  exit 1
fi
echo "OK: health"

echo "==> models"
if ! "${NETLLM_CMD[@]}" models 2>/dev/null; then
  echo "FAIL: netllm models returned non-zero" >&2
  echo "hint: start Ollama/LM Studio/vLLM, then netllm discover" >&2
  exit 1
fi

echo "OK: setup verified"
