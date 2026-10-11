#!/usr/bin/env bash
# Install DeepSeek Harness (dsh web) on Ubuntu ai-server (user-local Node, no sudo).
set -euo pipefail

DSH_HOME="${DSH_HOME:-$HOME/.dsh}"
NODE_DIR="${NODE_DIR:-$HOME/node-v22.19.0-linux-x64}"
NODE_VERSION="${NODE_VERSION:-22.19.0}"
TRUSTED_HOST="${DSH_TRUSTED_HOST:-chat.bucket-hub.stackrise.com.au}"
EXTERNAL_URL="${DSH_EXTERNAL_URL:-https://chat.bucket-hub.stackrise.com.au}"
NETLLM_KEY="${NETLLM_LOCAL_KEY:-netllm-local}"

log() { printf '[dsh-setup] %s\n' "$*"; }

if [[ ! -x "$NODE_DIR/bin/node" ]]; then
  log "Installing Node.js ${NODE_VERSION} to ${NODE_DIR}..."
  curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" -o /tmp/node.tar.xz
  tar -xJf /tmp/node.tar.xz -C "$HOME"
fi
export PATH="$NODE_DIR/bin:$PATH"
node --version | grep -q '^v22\.' || { log "Node 22+ required"; exit 1; }

log "Installing @deepseek-ai/dsh globally..."
npm install -g @deepseek-ai/dsh@latest

mkdir -p "$DSH_HOME"
if [[ ! -f "$DSH_HOME/settings.yaml" ]]; then
  cat >"$DSH_HOME/settings.yaml" <<EOF
llm-pi-ai:
  providers:
    netllm-local:
      apiKeyEnv: NETLLM_LOCAL_KEY
      api: openai-completions
      baseURL: http://127.0.0.1:11400/v1
      models:
        - id: qwen3.8-27b
web-runtime:
  config:
    externalUrl: ${EXTERNAL_URL}
EOF
fi

touch "$DSH_HOME/.env"
grep -q NETLLM_LOCAL_KEY "$DSH_HOME/.env" 2>/dev/null || echo "NETLLM_LOCAL_KEY=${NETLLM_KEY}" >>"$DSH_HOME/.env"

UNIT="$HOME/.config/systemd/user/dsh-web.service"
mkdir -p "$(dirname "$UNIT")"
cat >"$UNIT" <<EOF
[Unit]
Description=DeepSeek Harness Web UI
After=network-online.target

[Service]
Type=simple
Environment=PATH=${NODE_DIR}/bin:/usr/local/bin:/usr/bin:/bin
Environment=DSH_HOME=${DSH_HOME}
Environment=NETLLM_LOCAL_KEY=${NETLLM_KEY}
ExecStart=${NODE_DIR}/bin/dsh web --host 127.0.0.1 --port 3080 --trusted-host ${TRUSTED_HOST} --no-open
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable dsh-web.service
systemctl --user restart dsh-web.service
log "dsh web on http://127.0.0.1:3080 (expose via Cloudflare tunnel: ${EXTERNAL_URL})"
