#!/usr/bin/env bash
# Torrent → Expansion HDD → Sonarr/Radarr → Jellyfin smoke checks (LAN).
# Run from Mac or Pi. Optional download: SMOKE_MAGNET=1 SMOKE_MAGNET_URL='magnet:...'
set -euo pipefail

AI_SERVER="${AI_SERVER:-192.168.50.65}"
HUB_API="${HUB_API:-http://192.168.50.138:3020}"
JELLYFIN_URL="${JELLYFIN_URL:-http://${AI_SERVER}:8096}"
SSH_USER="${SSH_USER:-bucket}"
SSH_OPTS=(-o ConnectTimeout=8 -o BatchMode=yes)
[[ -f "${HOME}/.ssh/id_ed25519_pi" ]] && SSH_OPTS+=(-i "${HOME}/.ssh/id_ed25519_pi")

fail=0
ok() { echo "OK: $*"; }
warn() { echo "WARN: $*"; }
die() { echo "FAIL: $*"; fail=1; }

echo "=== Torrent → Jellyfin smoke ==="
echo "ai-server=${AI_SERVER} hub=${HUB_API}"

if ssh "${SSH_OPTS[@]}" "${SSH_USER}@${AI_SERVER}" 'findmnt -n /mnt/external/movies' 2>/dev/null; then
  ok "movies USB mounted"
else
  die "movies USB not mounted at /mnt/external/movies"
fi

if ssh "${SSH_OPTS[@]}" "${SSH_USER}@${AI_SERVER}" \
  'docker exec aria2 ls /mnt/torrents 2>/dev/null | head -5'; then
  ok "aria2 /mnt/torrents visible"
else
  die "aria2 container or /downloads not reachable"
fi

if curl -sf --connect-timeout 8 "${JELLYFIN_URL}/health" >/dev/null; then
  ok "Jellyfin health"
else
  die "Jellyfin not reachable at ${JELLYFIN_URL}"
fi

dest_json="$(curl -sf --connect-timeout 8 "${HUB_API}/api/worker/aria2-destinations" || true)"
if [[ -n "${dest_json}" ]] && echo "${dest_json}" | grep -q 'movies_usb'; then
  ok "Hub aria2-destinations includes movies_usb"
else
  die "Hub /api/worker/aria2-destinations missing movies_usb"
fi

for svc in sonarr radarr jellyfin gluetun; do
  if ssh "${SSH_OPTS[@]}" "${SSH_USER}@${AI_SERVER}" \
    "docker ps --format '{{.Names}}' | grep -qx '${svc}'" 2>/dev/null; then
    ok "container ${svc} running"
  else
    warn "container ${svc} not running (check compose)"
  fi
done

if [[ "${SMOKE_MAGNET:-0}" == "1" ]]; then
  magnet="${SMOKE_MAGNET_URL:-}"
  if [[ -z "${magnet}" ]]; then
    die "SMOKE_MAGNET=1 requires SMOKE_MAGNET_URL"
  else
    payload="$(SMOKE_MAGNET_URL="${magnet}" python3 -c 'import json,os; print(json.dumps({"kind":"magnet","url":os.environ["SMOKE_MAGNET_URL"],"destination_path":"/mnt/external/movies/torrents","title":"smoke-magnet","tags":["smoke-test"]}))')"
    job="$(curl -sf -X POST "${HUB_API}/api/jobs" -H 'Content-Type: application/json' -d "${payload}")"
    jid="$(echo "${job}" | python3 -c "import sys,json; print(json.load(sys.stdin)['job']['id'])")"
    ok "queued smoke job ${jid} (poll Hub UI or /api/jobs)"
  fi
fi

if [[ "${fail}" -ne 0 ]]; then
  echo "=== smoke FAILED ==="
  exit 1
fi
echo "=== smoke PASSED ==="
