#!/usr/bin/env bash
# Push Caddy Jellyfin proxy + Glance iframe fix to Pi; restart gateway + glance; smoke tunnel origin.
set -euo pipefail

HUB="$(cd "$(dirname "$0")/.." && pwd)"
PI_HOST="${PI_HOST:-stackrise-pi}"
PI_FALLBACKS=(bucket@100.85.42.0 bucket@192.168.50.138)

ssh_pi() {
  if ssh -o ConnectTimeout=10 -o BatchMode=yes "$PI_HOST" "$@"; then
    return 0
  fi
  for fb in "${PI_FALLBACKS[@]}"; do
    if ssh -o ConnectTimeout=10 -o BatchMode=yes "$fb" "$@"; then
      PI_HOST="$fb"
      return 0
    fi
  done
  echo "FAIL: cannot SSH Pi (tried $PI_HOST and ${PI_FALLBACKS[*]})"
  exit 1
}

echo "=== deploy Hub Jellyfin proxy → ${PI_HOST} ==="
scp -o ConnectTimeout=10 \
  "${HUB}/Caddyfile" "${HUB}/glance.yml" \
  "${PI_HOST}:~/homelab/glance/"

ssh_pi bash -s <<'REMOTE'
set -euo pipefail
echo "--- cloudflared ---"
systemctl is-active cloudflared 2>/dev/null || true
systemctl --user is-active cloudflared 2>/dev/null || true
docker ps --format '{{.Names}}' | grep -i cloudflared || true
cd ~/homelab/glance
docker restart hub-gateway glance 2>/dev/null || docker compose restart hub-gateway glance 2>/dev/null || true
if systemctl is-active cloudflared >/dev/null 2>&1; then
  sudo systemctl restart cloudflared
elif systemctl --user is-active cloudflared >/dev/null 2>&1; then
  systemctl --user restart cloudflared
fi
cf=$(docker ps --format '{{.Names}}' | grep -i cloudflared | head -1 || true)
if [[ -n "${cf}" ]]; then
  docker restart "${cf}"
fi
code=$(curl -sf -o /dev/null -w '%{http_code}' http://127.0.0.1:3000/ || echo 000)
echo "origin :3000 HTTP ${code}"
curl -sf -o /dev/null -w 'jellyfin-proxy HTTP %{http_code}\n' http://127.0.0.1:3000/jellyfin/ || echo "jellyfin-proxy failed (wake ai-server + set Jellyfin Base URL /jellyfin)"
REMOTE

echo "=== done — set Jellyfin Base URL /jellyfin on ai-server if not already ==="
