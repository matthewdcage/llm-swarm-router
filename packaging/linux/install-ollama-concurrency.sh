#!/usr/bin/env bash
# Install a systemd drop-in for Ollama parallel request slots.
# Align OLLAMA_NUM_PARALLEL with netllm routing.max_in_flight_per_backend (default 8).
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

PARALLEL="${OLLAMA_NUM_PARALLEL:-8}"
MAX_QUEUE="${OLLAMA_MAX_QUEUE:-512}"
DROPIN_DIR="/etc/systemd/system/ollama.service.d"
DROPIN_FILE="${DROPIN_DIR}/netllm-concurrency.conf"

if ! systemctl list-unit-files ollama.service >/dev/null 2>&1; then
  echo "ollama.service not found — install Ollama first." >&2
  exit 1
fi

mkdir -p "${DROPIN_DIR}"
cat >"${DROPIN_FILE}" <<EOF
[Service]
Environment=OLLAMA_NUM_PARALLEL=${PARALLEL}
Environment=OLLAMA_MAX_QUEUE=${MAX_QUEUE}
EOF
chmod 644 "${DROPIN_FILE}"

systemctl daemon-reload
if systemctl is-active --quiet ollama.service; then
  systemctl restart ollama.service
fi

echo "Installed ${DROPIN_FILE}"
echo "  OLLAMA_NUM_PARALLEL=${PARALLEL}"
echo "  OLLAMA_MAX_QUEUE=${MAX_QUEUE}"
echo "Match netllm config routing.max_in_flight_per_backend to ${PARALLEL} if you use admission caps."
echo "Restart netllm after changing either side: netllm restart"
