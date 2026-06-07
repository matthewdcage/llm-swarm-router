#!/usr/bin/env bash
# Verify netllm agent is healthy and exposing models. For agent/setup workflows.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NETLLM="$ROOT/netllm"
BASE_URL="${NETLLM_BASE_URL:-http://127.0.0.1:11400}"

if [[ ! -x "$NETLLM" ]]; then
  echo "error: missing executable $NETLLM (run from repo root)" >&2
  exit 1
fi

echo "==> health check ($BASE_URL)"
if ! curl -sf "${BASE_URL}/health" >/dev/null; then
  echo "FAIL: agent not reachable at ${BASE_URL}/health" >&2
  echo "hint: run ./netllm serve in a dedicated terminal" >&2
  exit 1
fi
echo "OK: health"

echo "==> models"
if ! "$NETLLM" models 2>/dev/null; then
  echo "FAIL: ./netllm models returned non-zero" >&2
  echo "hint: start oMLX/Ollama/LM Studio, then ./netllm discover" >&2
  exit 1
fi

echo "OK: setup verified"
