#!/usr/bin/env bash
# mount-watchdog.sh — detect and unmount stale USB/external drives
# Runs every 60s via systemd timer. Prevents D-state cascades that
# brick SSH and all services when a USB drive disconnects.
#
# Root cause (2026-09-30): USB drives intermittently disconnect at
# controller level (kernel error -110). The mount stays active but
# any process that touches it enters uninterruptible D-state.
# This watchdog force-unmounts stale drives before they cause harm.

set -euo pipefail

LOG="/home/bucket/homelab/logs/mount-watchdog.log"
DEVOPS_LOG="/home/bucket/homelab/logs/dev-ops.log"
EXTERNAL_BASE="/mnt/external"
TNAS_MOUNT="/mnt/tnas"
TIMEOUT_S=3

mkdir -p "$(dirname "$LOG")"
mkdir -p "$(dirname "$DEVOPS_LOG")"

ts() { date '+%Y-%m-%d %H:%M:%S'; }

log() {
    echo "[$(ts)] $*" >> "$LOG"
}

devops() {
    local level="$1"; shift
    echo "[$(ts)] [watchdog] [$level] $*" >> "$DEVOPS_LOG"
}

# Determine unmount command (sudo if available, else try without)
do_unmount() {
    local mnt="$1"
    if sudo -n true 2>/dev/null; then
        sudo umount -l "$mnt" 2>/dev/null || true
        sudo fuser -km "$mnt" 2>/dev/null || true
    else
        umount -l "$mnt" 2>/dev/null || fusermount -u "$mnt" 2>/dev/null || true
    fi
}

# Check and handle stale external mounts
for mount_dir in "$EXTERNAL_BASE"/*/; do
    [ -d "$mount_dir" ] || continue
    mount_dir="${mount_dir%/}"
    name="$(basename "$mount_dir")"

    # Skip if not actually a mountpoint
    if ! mountpoint -q "$mount_dir" 2>/dev/null; then
        continue
    fi

    # Test the mount with a timeout
    if ! timeout "$TIMEOUT_S" stat "$mount_dir" >/dev/null 2>&1; then
        log "STALE: $mount_dir ($name) — force unmounting"
        devops "WARN" "Stale USB mount detected: $mount_dir — force unmounting (lazy)"

        do_unmount "$mount_dir"

        log "UNMOUNTED: $mount_dir"
        devops "INFO" "Force unmounted stale drive: $mount_dir"
    fi
done

# Check TNAS CIFS mount health (don't unmount, just log)
if mountpoint -q "$TNAS_MOUNT" 2>/dev/null; then
    if ! timeout "$TIMEOUT_S" stat "$TNAS_MOUNT" >/dev/null 2>&1; then
        log "WARNING: TNAS mount stale at $TNAS_MOUNT"
        devops "WARN" "TNAS CIFS mount stale — may need remount"
    fi
fi

# Check for kernel USB errors in the last 2 minutes
usb_errors=$(dmesg -T 2>/dev/null | grep -c "device descriptor read.*error -110" || true)
if [ "$usb_errors" -gt 5 ]; then
    recent=$(dmesg -T 2>/dev/null | grep "device descriptor read.*error -110" | tail -1)
    log "USB ERRORS: $usb_errors total — latest: $recent"
    devops "WARN" "USB controller errors: $usb_errors total"
fi
