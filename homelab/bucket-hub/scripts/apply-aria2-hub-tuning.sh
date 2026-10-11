#!/usr/bin/env bash
# Idempotent aria2.conf tuning for Bucket-HUB (ai-server).
# - Backs up the config before any change
# - Does not delete downloads or restart aria2 unless --recreate is passed
#
# Usage (on ai-server):
#   ~/homelab/scripts/apply-aria2-hub-tuning.sh
#   ~/homelab/scripts/apply-aria2-hub-tuning.sh --recreate   # docker compose recreate aria2
set -euo pipefail

CONF="${ARIA2_CONF:-$HOME/homelab/aria2/aria2.conf}"
RECREATE=0
for arg in "$@"; do
  case "$arg" in
    --recreate) RECREATE=1 ;;
    -h|--help)
      sed -n '1,12p' "$0"
      exit 0
      ;;
  esac
done

if [[ ! -f "$CONF" ]]; then
  echo "missing $CONF" >&2
  exit 1
fi

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
bak="${CONF}.bak.hub-tuning.${stamp}"
cp -a "$CONF" "$bak"
echo "backup → $bak"

set_kv() {
  local key="$1" val="$2"
  if grep -q "^${key}=" "$CONF"; then
    sed -i "s|^${key}=.*|${key}=${val}|" "$CONF"
  else
    echo "${key}=${val}" >> "$CONF"
  fi
}

# Hub concurrency (aria2 was 5 while worker allows 16)
set_kv max-concurrent-downloads 16

# Leech-only: no post-complete seeding (Hub worker also removes gid on done)
set_kv seed-ratio 0.0
set_kv seed-time 0
set_kv bt-seed-unverified false

# Small upload cap during download (BitTorrent still needs some uplink for peers)
set_kv max-overall-upload-limit 128K

# Keep exFAT-safe + unlimited download (do not touch if already set)
grep -q '^file-allocation=none' "$CONF" || set_kv file-allocation none
grep -q '^max-overall-download-limit=' "$CONF" || set_kv max-overall-download-limit 0

echo "applied Hub tuning keys in $CONF"
grep -E '^max-concurrent-downloads=|^seed-ratio=|^seed-time=|^max-overall-upload-limit=' "$CONF" || true

if [[ "$RECREATE" -eq 1 ]]; then
  cd "${HOME}/homelab"
  docker compose up -d --force-recreate aria2
  echo "aria2 container recreated (session volume preserved)"
else
  echo "note: restart aria2 to load conf — run with --recreate or: docker compose up -d --force-recreate aria2"
fi
