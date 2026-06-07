#!/usr/bin/env bash
# Sync canonical agent skills from .agents/skills/ to tool-specific paths.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/.agents/skills"

if [[ ! -d "$SRC" ]]; then
  echo "error: missing $SRC" >&2
  exit 1
fi

DESTS=(
  "$ROOT/.claude/skills"
  "$ROOT/.cursor/skills"
  "$ROOT/.github/skills"
)

for dest in "${DESTS[@]}"; do
  mkdir -p "$dest"
  rsync -a --delete "$SRC/" "$dest/"
  echo "synced → $dest"
done

echo "done: $(find "$SRC" -name 'SKILL.md' | wc -l | tr -d ' ') skill(s) from .agents/skills/"
