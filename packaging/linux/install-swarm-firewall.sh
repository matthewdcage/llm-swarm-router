#!/usr/bin/env bash
# Open LAN ports for netllm swarm: agent HTTP (11400/tcp) and mDNS (5353/udp).
# Supports ufw and firewalld. Idempotent — safe to re-run.
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

NETLLM_PORT="${NETLLM_PORT:-11400}"

apply_ufw() {
  command -v ufw >/dev/null 2>&1 || return 1
  ufw allow "${NETLLM_PORT}/tcp" comment 'netllm swarm agent'
  ufw allow 5353/udp comment 'mDNS (netllm swarm discovery)'
  ufw status numbered | grep -E "${NETLLM_PORT}/tcp|5353/udp" || true
  return 0
}

apply_firewalld() {
  command -v firewall-cmd >/dev/null 2>&1 || return 1
  firewall-cmd --permanent --add-port="${NETLLM_PORT}/tcp"
  firewall-cmd --permanent --add-service=mdns
  firewall-cmd --reload
  firewall-cmd --list-ports
  firewall-cmd --list-services | tr ' ' '\n' | grep -E '^mdns$' || true
  return 0
}

if apply_ufw; then
  echo "ufw: allowed TCP ${NETLLM_PORT} and UDP 5353 (mDNS)."
elif apply_firewalld; then
  echo "firewalld: allowed TCP ${NETLLM_PORT} and mdns service."
else
  echo "No supported firewall found (ufw or firewalld)." >&2
  echo "Open manually: TCP ${NETLLM_PORT}, UDP 5353 (mDNS)." >&2
  exit 1
fi

echo "Verify from a peer/gateway: curl -sf http://<this-host-LAN-IP>:${NETLLM_PORT}/health"
