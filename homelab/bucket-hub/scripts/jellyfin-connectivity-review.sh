#!/usr/bin/env bash
# Run on ai-server (bucket@192.168.50.65) or: ssh bucket@192.168.50.65 'bash -s' < scripts/jellyfin-connectivity-review.sh
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'
ok() { echo -e "${GREEN}OK${NC} $*"; }
fail() { echo -e "${RED}FAIL${NC} $*"; }

echo "=== Jellyfin connectivity review ==="
echo "host: $(hostname)  date: $(date -Iseconds)"
echo

# Container
if docker ps --format '{{.Names}}' | grep -qx jellyfin; then
  ok "jellyfin container running"
  docker ps --filter name=jellyfin --format '  {{.Status}}  ports {{.Ports}}'
else
  fail "jellyfin container not running — try: cd ~/homelab && docker compose up -d jellyfin"
fi

# HTTP
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8096/ || echo 000)
if [[ "$code" =~ ^(200|302)$ ]]; then
  ok "HTTP localhost:8096 → $code"
else
  fail "HTTP localhost:8096 → $code"
fi

# Mounts inside container
echo
echo "--- Jellyfin volume mounts (expected: /movies /tv /torrents on Expansion HDD) ---"
docker inspect jellyfin --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}' 2>/dev/null | grep -E 'movies|tv|torrents|media' || fail "could not read jellyfin mounts"

for host in /mnt/external/movies/torrents /mnt/external/movies/tv /mnt/external/movies/movies; do
  if mountpoint -q "$(dirname "$host" 2>/dev/null)" 2>/dev/null || [[ -d "$host" ]]; then
    if [[ -d "$host" ]]; then
      ok "host path exists: $host ($(du -sh "$host" 2>/dev/null | cut -f1))"
    else
      fail "missing: $host"
    fi
  else
    if [[ -d /mnt/external/movies ]]; then
      [[ -d "$host" ]] && ok "$host" || fail "missing $host (run ensure-movies-library-dirs.sh)"
    else
      fail "/mnt/external/movies not mounted — Expansion HDD?"
    fi
  fi
done

# Office sample
office=$(find /mnt/external/movies/torrents -maxdepth 1 -type d -name '*Office*' 2>/dev/null | head -1)
if [[ -n "$office" ]]; then
  n=$(find "$office" -type f \( -name '*.mkv' -o -name '*.mp4' \) 2>/dev/null | wc -l | tr -d ' ')
  ok "The Office torrent folder: $office ($n video files)"
else
  echo "  (no Office folder under torrents — skip or download still elsewhere)"
fi

echo
echo "--- Reach from other devices ---"
lan_ip=$(hostname -I 2>/dev/null | awk '{print $1}')
echo "  LAN:  http://${lan_ip:-192.168.50.65}:8096"
echo "  TS:   http://100.112.168.40:8096  (if Tailscale up on ai-server)"
echo "  Hub Glance Jellyfin tab uses LAN 192.168.50.65 — off-LAN use Tailscale URL above"
echo
echo "Jellyfin libraries should include container paths: /tv, /movies, /torrents (see docs/MEDIA-JELLYFIN-PIPELINE.md)"
