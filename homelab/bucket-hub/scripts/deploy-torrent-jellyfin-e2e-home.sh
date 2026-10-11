#!/usr/bin/env bash
# Run from your Mac on LAN or Tailscale — full Pi + ai-server deploy for Torrent→Jellyfin E2E.
set -euo pipefail

HUB="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="$(cd "${HUB}/../.." && pwd)"
SSH_OPTS=(-o ConnectTimeout=15 -o BatchMode=yes)
[[ -f "${HOME}/.ssh/id_ed25519_pi" ]] && SSH_OPTS+=(-i "${HOME}/.ssh/id_ed25519_pi")

PI_CANDIDATES=(
  "${PI_HOST:-}"
  stackrise-pi
  bucket@192.168.50.138
  bucket@100.85.42.0
)
AI_CANDIDATES=(
  "${AI_SERVER:-}"
  bucket@192.168.50.65
  bucket@100.112.168.40
)

resolve_ssh() {
  local host="$1"
  shift
  for h in "$@"; do
    [[ -z "${h}" ]] && continue
    if ssh "${SSH_OPTS[@]}" "${h}" "$@"; then
      echo "${h}"
      return 0
    fi
  done
  return 1
}

echo "=== Resolving Pi SSH ==="
PI=""
for h in "${PI_CANDIDATES[@]}"; do
  [[ -z "${h}" ]] && continue
  if ssh "${SSH_OPTS[@]}" "${h}" 'hostname' >/dev/null 2>&1; then
    PI="${h}"
    break
  fi
  echo "  skip ${h}"
done
if [[ -z "${PI}" ]]; then
  echo "FAIL: no Pi SSH. Set PI_HOST=bucket@<ip> and ensure Tailscale/LAN."
  exit 1
fi
echo "Pi: ${PI}"

echo "=== Resolving ai-server SSH ==="
AI=""
for h in "${AI_CANDIDATES[@]}"; do
  [[ -z "${h}" ]] && continue
  if ssh "${SSH_OPTS[@]}" "${h}" 'hostname' >/dev/null 2>&1; then
    AI="${h}"
    break
  fi
  echo "  skip ${h}"
done
if [[ -z "${AI}" ]]; then
  echo "FAIL: no ai-server SSH. Wake machine or set AI_SERVER=bucket@<ip>"
  exit 1
fi
echo "ai-server: ${AI}"

echo ""
echo "=== [1/4] Pi — Caddy, Glance, hub-form ==="
scp "${SSH_OPTS[@]}" \
  "${HUB}/Caddyfile" "${HUB}/glance.yml" \
  "${PI}:~/homelab/glance/"
scp "${SSH_OPTS[@]}" \
  "${HUB}/app.py" "${HUB}/form.html" \
  "${PI}:~/homelab/hub-form/"
ssh "${SSH_OPTS[@]}" "${PI}" bash -s <<'REMOTE'
set -euo pipefail
cd ~/homelab/glance
docker restart hub-gateway glance 2>/dev/null || docker compose restart hub-gateway glance
if systemctl is-active cloudflared >/dev/null 2>&1; then
  sudo systemctl restart cloudflared
elif systemctl --user is-active cloudflared >/dev/null 2>&1; then
  systemctl --user restart cloudflared
fi
cf=$(docker ps --format '{{.Names}}' | grep -i cloudflared | head -1 || true)
[[ -n "${cf}" ]] && docker restart "${cf}"
systemctl --user restart hub-form.service
curl -sf -o /dev/null -w 'Pi :3000 %{http_code}\n' http://127.0.0.1:3000/
curl -sf -o /dev/null -w 'Pi /jellyfin/ %{http_code}\n' http://127.0.0.1:3000/jellyfin/ || echo "Pi /jellyfin/ not OK yet (Jellyfin base URL on ai-server)"
REMOTE

echo ""
echo "=== [2/4] ai-server — compose + worker ==="
scp "${SSH_OPTS[@]}" \
  "${HUB}/docker-compose-media.yml" \
  "${HUB}/scripts/ensure-movies-library-dirs.sh" \
  "${HUB}/hub-worker.py" \
  "${HUB}/media_pipeline.py" \
  "${HUB}/hub_torrent_parse.py" \
  "${HUB}/torrent_indexer_catalog.py" \
  "${HUB}/torrent-indexers-catalog.json" \
  "${HUB}/download_destinations.py" \
  "${AI}:~/homelab/"
ssh "${SSH_OPTS[@]}" "${AI}" bash -s <<'REMOTE'
set -euo pipefail
cd ~/homelab
chmod +x ensure-movies-library-dirs.sh
./ensure-movies-library-dirs.sh
if [[ -f aria2/aria2.conf ]] && ! grep -q '^file-allocation=none' aria2/aria2.conf; then
  echo "file-allocation=none" >> aria2/aria2.conf
fi
[[ ! -f docker-compose.yml.bak-media ]] && cp -a docker-compose.yml docker-compose.yml.bak-media 2>/dev/null || true
cp -a docker-compose-media.yml docker-compose.yml
docker compose up -d gluetun aria2 sonarr radarr jellyfin
mkdir -p ~/homelab/scripts ~/homelab/hub-form
cp -f hub-worker.py download_destinations.py hub_torrent_parse.py media_pipeline.py torrent_indexer_catalog.py ~/homelab/scripts/
cp -f torrent-indexers-catalog.json ~/homelab/hub-form/
systemctl --user restart hub-worker.service
curl -sf http://127.0.0.1:8096/health && echo " jellyfin health OK"
REMOTE

echo ""
echo "=== [3/4] Smoke (from Mac) ==="
HUB_API="http://192.168.50.138:3020"
if [[ "${PI}" == *100.85* ]]; then
  HUB_API="http://100.85.42.0:3020"
fi
AI_IP="192.168.50.65"
[[ "${AI}" == *100.112* ]] && AI_IP="100.112.168.40"
export HUB_API JELLYFIN_URL="http://${AI_IP}:8096" SSH_USER=bucket
"${HUB}/scripts/hub-torrent-jellyfin-smoke.sh" || true

echo ""
echo "=== [4/4] Manual (one-time UI) ==="
echo "  Jellyfin → Networking → Base URL: /jellyfin → restart Jellyfin"
echo "  Libraries: Shows /tv, Movies /movies"
echo "  Sonarr /data/tv, Radarr /data/movies, aria2 client"
echo "  hub-worker.env: JELLYFIN_*, SONARR_*, RADARR_*"
echo "  Guide: ${HUB}/docs/MEDIA-JELLYFIN-PIPELINE.md"
echo "=== deploy script finished ==="
