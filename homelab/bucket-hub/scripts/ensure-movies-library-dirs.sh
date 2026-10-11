#!/usr/bin/env bash
# Run on ai-server as bucket. Creates Expansion HDD library layout.
set -euo pipefail
ROOT="${MOVIES_USB_ROOT:-/mnt/external/movies}"
for d in torrents tv movies music; do
  mkdir -p "${ROOT}/${d}"
done
echo "OK: ${ROOT}/{torrents,tv,movies,music}"
