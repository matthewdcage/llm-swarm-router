#!/usr/bin/env bash
# Run from Mac: push media compose + worker to ai-server and recreate media containers.
# Does not print or overwrite hub-worker.env secrets.
set -euo pipefail

HUB="$(cd "$(dirname "$0")/.." && pwd)"
AI="${AI_SERVER:-bucket@192.168.50.65}"
SSH_OPTS=(-o ConnectTimeout=12 -o BatchMode=yes)
[[ -f "${HOME}/.ssh/id_ed25519_pi" ]] && SSH_OPTS+=(-i "${HOME}/.ssh/id_ed25519_pi")

echo "=== deploy media stack → ${AI} ==="
scp "${SSH_OPTS[@]}" \
  "${HUB}/docker-compose-media.yml" \
  "${HUB}/scripts/ensure-movies-library-dirs.sh" \
  "${HUB}/scripts/apply-aria2-hub-tuning.sh" \
  "${HUB}/hub-worker.py" \
  "${HUB}/download_destinations.py" \
  "${AI}:~/homelab/"

ssh "${SSH_OPTS[@]}" "${AI}" bash -s <<'REMOTE'
set -euo pipefail
cd ~/homelab
chmod +x ensure-movies-library-dirs.sh apply-aria2-hub-tuning.sh
mkdir -p ~/homelab/scripts
cp -f apply-aria2-hub-tuning.sh ~/homelab/scripts/
chmod +x ~/homelab/scripts/apply-aria2-hub-tuning.sh
./ensure-movies-library-dirs.sh
if [[ -f aria2/aria2.conf ]] && ! grep -q '^file-allocation=none' aria2/aria2.conf; then
  echo "file-allocation=none" >> aria2/aria2.conf
  echo "Patched aria2.conf file-allocation=none (exFAT)"
fi
if [[ ! -f docker-compose.yml.bak-media ]]; then
  cp -a docker-compose.yml docker-compose.yml.bak-media 2>/dev/null || true
fi
cp -a docker-compose-media.yml docker-compose.yml
docker compose up -d gluetun aria2 sonarr radarr jellyfin
mkdir -p ~/homelab/scripts
cp -f hub-worker.py download_destinations.py ~/homelab/scripts/
systemctl --user restart hub-worker.service 2>/dev/null || echo "WARN: restart hub-worker manually"
docker ps --format 'table {{.Names}}\t{{.Status}}' | grep -E 'aria2|sonarr|radarr|jellyfin|gluetun' || true
REMOTE

echo "=== done (verify Jellyfin/Sonarr UI per docs/MEDIA-JELLYFIN-PIPELINE.md) ==="
