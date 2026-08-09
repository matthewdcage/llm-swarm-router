#!/usr/bin/env bash
# Sync canonical agent skills from .agents/skills/ to tool-specific paths.
#
#   sync-agent-skills.sh           copy .agents/skills/ over each destination
#   sync-agent-skills.sh --check   fail if any destination has drifted
#
# --check runs in scripts/ci.sh lint. The copies are checked in, so editing a
# skill under .claude/ (the path an agent actually reads) and forgetting the
# canonical source silently makes .agents/skills/ the stale one — which is the
# same "stated in four places, updated in one" failure the registry ledgers
# exist to catch, one directory up.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/.agents/skills"

mode="${1:-sync}"
if [[ "$mode" != "sync" && "$mode" != "--check" ]]; then
  echo "usage: $0 [--check]" >&2
  exit 2
fi

if [[ ! -d "$SRC" ]]; then
  echo "error: missing $SRC" >&2
  exit 1
fi

DESTS=(
  "$ROOT/.claude/skills"
  "$ROOT/.cursor/skills"
  "$ROOT/.github/skills"
)

if [[ "$mode" == "--check" ]]; then
  drifted=0
  for dest in "${DESTS[@]}"; do
    if [[ ! -d "$dest" ]]; then
      echo "drift: missing $dest" >&2
      drifted=1
      continue
    fi
    if ! diff -r -q "$SRC" "$dest" >&2; then
      drifted=1
    fi
  done
  if [[ "$drifted" -ne 0 ]]; then
    echo "error: agent skills out of sync — run scripts/sync-agent-skills.sh" >&2
    exit 1
  fi
  echo "OK: agent skills in sync across ${#DESTS[@]} destinations"
  exit 0
fi

for dest in "${DESTS[@]}"; do
  mkdir -p "$dest"
  rsync -a --delete "$SRC/" "$dest/"
  echo "synced → $dest"
done

echo "done: $(find "$SRC" -name 'SKILL.md' | wc -l | tr -d ' ') skill(s) from .agents/skills/"
