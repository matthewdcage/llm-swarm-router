#!/usr/bin/env bash
# Compare sha256 of deploy-manifest files with the live Pi or worker copy.
set -euo pipefail

ROLE="${1:-}"
if [[ "$ROLE" != "pi" && "$ROLE" != "worker" ]]; then
  echo "usage: $0 pi|worker" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MANIFEST="$ROOT/scripts/deploy-manifest.txt"
if [[ ! -f "$MANIFEST" ]]; then
  echo "missing $MANIFEST" >&2
  exit 1
fi

if [[ "$ROLE" == "pi" ]]; then
  HOST="${HUB_PI_SSH:-stackrise-pi}"
  REMOTE_ROOT="${HUB_PI_HUB_FORM:-~/homelab/hub-form}"
else
  HOST="${HUB_WORKER_SSH:-ai-server-ts}"
  REMOTE_ROOT="${HUB_WORKER_SCRIPTS:-~/homelab/scripts}"
fi

section=""
fail=0
while IFS= read -r line || [[ -n "$line" ]]; do
  line="${line%%#*}"
  line="$(echo "$line" | sed 's/[[:space:]]*$//')"
  [[ -z "$line" ]] && continue
  if [[ "$line" == "[pi]" ]]; then
    section="pi"
    continue
  fi
  if [[ "$line" == "[worker]" ]]; then
    section="worker"
    continue
  fi
  [[ "$section" == "$ROLE" ]] || continue
  local_path="$ROOT/$line"
  if [[ ! -f "$local_path" ]]; then
    echo "MISSING_LOCAL $line"
    fail=1
    continue
  fi
  local_hash="$(sha256sum "$local_path" | awk '{print $1}')"
  remote_file="$REMOTE_ROOT/$line"
  remote_out="$(ssh -o BatchMode=yes -o ConnectTimeout=8 "$HOST" "if [ -f $remote_file ]; then sha256sum $remote_file; else echo MISSING; fi" 2>&1)" || {
    echo "SSH_FAIL $HOST $line: $remote_out"
    fail=1
    continue
  }
  if [[ "$remote_out" == MISSING* ]]; then
    echo "MISSING_REMOTE $line ($HOST:$remote_file)"
    fail=1
    continue
  fi
  remote_hash="$(echo "$remote_out" | awk '{print $1}')"
  if [[ "$local_hash" != "$remote_hash" ]]; then
    echo "MISMATCH $line local=$local_hash remote=$remote_hash"
    fail=1
  else
    echo "OK $line"
  fi
done < "$MANIFEST"

if [[ "$fail" -ne 0 ]]; then
  echo "parity failed for $ROLE" >&2
  exit 1
fi
echo "parity ok $ROLE"
exit 0
