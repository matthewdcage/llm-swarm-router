#!/usr/bin/env bash
# Bump Formula/netllm.rb url + sha256 for a published GitHub release tag.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
FORMULA="$ROOT/Formula/netllm.rb"
TAG="${1:?usage: update-homebrew-formula.sh vX.Y.Z}"

if [[ ! "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([-.][0-9A-Za-z.]+)?$ ]]; then
  echo "error: tag must look like v0.2.1 (got: $TAG)" >&2
  exit 1
fi

REPO="${GITHUB_REPOSITORY:-matthewdcage/llm-swarm-router}"
URL="https://github.com/${REPO}/archive/refs/tags/${TAG}.tar.gz"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

echo "==> Downloading $URL"
curl -fsSL "$URL" -o "$TMP"
SHA="$(shasum -a 256 "$TMP" | awk '{print $1}')"
echo "==> sha256: $SHA"

python3 - "$FORMULA" "$URL" "$SHA" <<'PY'
import pathlib
import re
import sys

path, url, sha = sys.argv[1:4]
text = pathlib.Path(path).read_text()
text, n_url = re.subn(
    r'^\s*url "https://github\.com/[^"]+/archive/refs/tags/v[^"]+\.tar\.gz"$',
    f'  url "{url}"',
    text,
    count=1,
    flags=re.MULTILINE,
)
text, n_sha = re.subn(
    r'^\s*sha256 "[a-f0-9]{64}"$',
    f'  sha256 "{sha}"',
    text,
    count=1,
    flags=re.MULTILINE,
)
if n_url != 1 or n_sha != 1:
    raise SystemExit(f"formula update failed (url={n_url}, sha256={n_sha})")
pathlib.Path(path).write_text(text)
print(f"==> Updated {path}")
PY
