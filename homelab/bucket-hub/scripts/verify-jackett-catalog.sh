#!/usr/bin/env bash
# Compare torrent-indexers-catalog.json jackett_id values to Jackett definition files.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CATALOG="${CATALOG:-$ROOT/torrent-indexers-catalog.json}"

if [[ ! -f "$CATALOG" ]]; then
  echo "Catalog not found: $CATALOG" >&2
  exit 1
fi

DEF_DIR="${JACKETT_DEFINITIONS_DIR:-}"
if [[ -z "$DEF_DIR" ]]; then
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx jackett; then
    mapfile -t CATALOG_IDS < <(
      python3 - "$CATALOG" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
for row in data.get("indexers") or []:
    jid = (row.get("jackett_id") or "").strip()
    if jid:
        print(jid)
PY
    )
    echo "Using Jackett container definitions (docker exec jackett)..."
    missing=()
    for id in "${CATALOG_IDS[@]}"; do
      if ! docker exec jackett test -f "/app/Jackett/Definitions/${id}.yml" 2>/dev/null; then
        missing+=("$id")
      fi
    done
    if ((${#missing[@]})); then
      echo "In catalog but NO Jackett definition:"
      printf '  %s\n' "${missing[@]}"
      exit 2
    fi
    echo "OK: all ${#CATALOG_IDS[@]} catalog jackett_id values have definitions."
    exit 0
  fi
  echo "Set JACKETT_DEFINITIONS_DIR to a folder of *.yml definitions, or run on a host with docker jackett." >&2
  exit 1
fi

python3 - "$CATALOG" "$DEF_DIR" <<'PY'
import json
import sys
from pathlib import Path

catalog_path = Path(sys.argv[1])
def_dir = Path(sys.argv[2])
data = json.loads(catalog_path.read_text(encoding="utf-8"))
ids = [
    str(r.get("jackett_id") or "").strip()
    for r in (data.get("indexers") or [])
    if r.get("jackett_id")
]
defs = {p.stem for p in def_dir.glob("*.yml")}
missing = sorted(i for i in ids if i not in defs)
configured_dir = Path.home() / "homelab/data/jackett/Jackett/Indexers"
configured = set()
if configured_dir.is_dir():
    configured = {p.stem for p in configured_dir.glob("*.json")}

if missing:
    print("In catalog but NO definition .yml:")
    for m in missing:
        print(f"  {m}")
    sys.exit(2)
print(f"OK: all {len(ids)} catalog jackett_id values have definitions in {def_dir}")
if configured:
    not_cfg = sorted(i for i in ids if i not in configured)
    if not_cfg:
        print(f"\nConfigured on this host ({len(configured)}): {', '.join(sorted(configured))}")
        print(f"In catalog but not configured ({len(not_cfg)}): first 10: {', '.join(not_cfg[:10])}...")
PY
