#!/usr/bin/env bash
# On-demand vLLM control for Ubuntu ai-server (Qwen2.5-Coder-14B AWQ).
set -euo pipefail

CONTAINER="${VLLM_CONTAINER:-vllm-coder}"
COMPOSE_FILE="${VLLM_COMPOSE_FILE:-$HOME/homelab/vllm-compose.yml}"
MODEL_ID="${VLLM_MODEL_ID:-qwen2.5-coder-14b}"
HEALTH_URL="${VLLM_HEALTH_URL:-http://127.0.0.1:8000/v1/models}"
START_TIMEOUT_S="${VLLM_START_TIMEOUT_S:-600}"
NETLLM_USER="${NETLLM_USER:-bucket}"

log() { printf '[ai-server-llm] %s\n' "$*"; }

container_running() {
  docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$CONTAINER"
}

wait_ready() {
  local deadline=$((SECONDS + START_TIMEOUT_S))
  while ((SECONDS < deadline)); do
    if curl -sf "$HEALTH_URL" 2>/dev/null | grep -q "$MODEL_ID"; then
      return 0
    fi
    sleep 3
  done
  return 1
}

gpu_summary() {
  if command -v nvidia-smi >/dev/null 2>&1; then
    nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader,nounits 2>/dev/null \
      | awk -F', ' '{printf "vram_used_mib=%s vram_total_mib=%s", $1, $2}'
  fi
}

status_cmd() {
  local state="off"
  local model=""
  local started=""
  if container_running; then
    if curl -sf "$HEALTH_URL" 2>/dev/null | grep -q "$MODEL_ID"; then
      state="ready"
    else
      state="starting"
    fi
    started="$(docker inspect -f '{{.State.StartedAt}}' "$CONTAINER" 2>/dev/null || true)"
    model="$MODEL_ID"
  fi
  printf 'state=%s model=%s container=%s started=%s %s\n' \
    "$state" "$model" "$CONTAINER" "$started" "$(gpu_summary)"
}

start_cmd() {
  if container_running; then
    if wait_ready; then
      log "Already running ($MODEL_ID)"
      status_cmd
      return 0
    fi
    log "Container up but model not ready — waiting..."
    if wait_ready; then
      status_cmd
      return 0
    fi
    log "Timed out waiting for model"
    return 1
  fi
  if [[ -f "$COMPOSE_FILE" ]]; then
    log "Starting via compose: $COMPOSE_FILE"
    docker compose -f "$COMPOSE_FILE" up -d
  else
    log "Starting container: $CONTAINER"
    docker start "$CONTAINER"
  fi
  if wait_ready; then
    log "Model ready: $MODEL_ID"
    systemctl --user restart netllm 2>/dev/null || true
    status_cmd
    return 0
  fi
  log "Start failed or timed out"
  docker logs --tail 40 "$CONTAINER" 2>&1 || true
  return 1
}

stop_cmd() {
  if container_running; then
    log "Stopping $CONTAINER"
    docker stop "$CONTAINER" >/dev/null
  else
    log "Already stopped"
  fi
  systemctl --user restart netllm 2>/dev/null || true
  status_cmd
}

pull_cmd() {
  log "Pulling weights (may take a while)..."
  docker run --rm \
    -v /models:/models \
    -e HF_HOME=/models/hf-cache \
    vllm/vllm-openai:latest \
    huggingface-cli download avyukth/Qwen3.8-27B-AWQ-INT4 \
      --local-dir /models/weights/qwen3.8-27b-awq-int4
}

case "${1:-status}" in
  start) start_cmd ;;
  stop) stop_cmd ;;
  status) status_cmd ;;
  pull) pull_cmd ;;
  *)
    echo "Usage: $0 {start|stop|status|pull}" >&2
    exit 2
    ;;
esac
