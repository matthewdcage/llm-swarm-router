#!/usr/bin/env bash
# Write "<file>.sha256" sidecar and print "hash  filename" for SHA256SUMS.
set -euo pipefail

usage() {
  echo "Usage: write-sha256-sidecar.sh <file> [file ...]" >&2
  exit 1
}

[[ $# -ge 1 ]] || usage

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT_DIR="${SHA256_OUT_DIR:-$ROOT/dist}"
mkdir -p "$OUT_DIR"

for file in "$@"; do
  [[ -f "$file" ]] || {
    echo "File not found: $file" >&2
    exit 1
  }
  base="$(basename "$file")"
  hash="$(shasum -a 256 "$file" | awk '{print $1}')"
  sidecar="$OUT_DIR/${base}.sha256"
  printf '%s  %s\n' "$hash" "$base" > "$sidecar"
  echo "$hash  $base"
done
