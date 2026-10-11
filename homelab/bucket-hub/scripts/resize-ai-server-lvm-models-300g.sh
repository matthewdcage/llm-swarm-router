#!/usr/bin/env bash
# ai-server: shrink /models LV to 300G, give freed space to / (ubuntu--lv).
# Run ON ai-server as bucket with sudo (interactive password OK).
#
# Why: ~780G /models had ~700G unused; root ~100G is tight for Docker + Jellyfin transcode cache.
# LLM weights ~30G today; 300G /models leaves room for Qwen-class AWQ + HF cache.
#
# Media stack: Jellyfin transcode stays on ~/homelab/data/jellyfin/cache (on /).
# Requires brief vLLM downtime while /models is unmounted.
set -euo pipefail

# VG name is ubuntu-vg (mapper paths look like ubuntu--vg-models)
MODELS_LV="/dev/ubuntu-vg/models"
ROOT_LV="/dev/ubuntu-vg/ubuntu-lv"
MODELS_MOUNT="/models"
TARGET_MODELS_LV_SIZE="300G"
# Filesystem size slightly under LV to avoid edge cases
TARGET_MODELS_FS_SIZE="295G"

if [[ "$(id -u)" -ne 0 ]] && ! sudo -n true 2>/dev/null; then
  echo "This script needs sudo on ai-server. Run:"
  echo "  bash $0"
  echo "and enter your password when prompted."
fi

run() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

echo "=== Preflight ==="
df -h / "${MODELS_MOUNT}" || true
run lvs -o lv_name,lv_size,vg_name
if mountpoint -q "${MODELS_MOUNT}"; then
  USED_MODELS="$(run df -BM --output=used "${MODELS_MOUNT}" | tail -1 | tr -d ' M')"
  if [[ "${USED_MODELS}" -gt 250000 ]]; then
    echo "FAIL: ${MODELS_MOUNT} uses more than ~250G; cannot shrink to 300G LV safely."
    exit 1
  fi
else
  echo "Note: ${MODELS_MOUNT} already unmounted; skipping used-space precheck."
fi

echo "=== Stop vLLM (uses ${MODELS_MOUNT}) ==="
if docker ps --format '{{.Names}}' | grep -qx vllm-coder; then
  docker stop vllm-coder
fi
if systemctl is-active --quiet netllm 2>/dev/null; then
  echo "netllm agent left running (does not need /models)."
fi

echo "=== Optional: remove stale Jellyfin cache copy on /models ==="
if [[ -d "${MODELS_MOUNT}/jellyfin-cache" ]]; then
  run rm -rf "${MODELS_MOUNT}/jellyfin-cache"
fi

echo "=== Unmount ${MODELS_MOUNT} ==="
if mountpoint -q "${MODELS_MOUNT}"; then
  run umount "${MODELS_MOUNT}"
else
  echo "Already unmounted."
fi

echo "=== Shrink ext4 then LV (${TARGET_MODELS_FS_SIZE} / ${TARGET_MODELS_LV_SIZE}) ==="
run e2fsck -fy "${MODELS_LV}"
run resize2fs "${MODELS_LV}" "${TARGET_MODELS_FS_SIZE}"
run lvreduce -L "${TARGET_MODELS_LV_SIZE}" -fy "${MODELS_LV}"
run resize2fs "${MODELS_LV}"

echo "=== Grow root LV (all free space in VG) ==="
run lvextend -l +100%FREE "${ROOT_LV}"
run resize2fs "${ROOT_LV}"

echo "=== Remount ${MODELS_MOUNT} ==="
run mount "${MODELS_MOUNT}"

echo "=== Start vLLM ==="
if docker ps -a --format '{{.Names}}' | grep -qx vllm-coder; then
  docker start vllm-coder || true
  echo "Check: ~/homelab/scripts/ai-server-llm.sh status"
fi

echo "=== Done ==="
df -h / "${MODELS_MOUNT}"
run lvs -o lv_name,lv_size,vg_name
