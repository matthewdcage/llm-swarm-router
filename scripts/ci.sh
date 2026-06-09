#!/usr/bin/env bash
# Local and CI checks. Usage: scripts/ci.sh [lint|test|sdk|packaging|all]
#   lint      — ruff check + format --check (~1s)
#   test      — pytest (~12s)
#   sdk       — vendor SDK adapter + bridge contract tests (~5s)
#   packaging — build deb/rpm (Linux) or windows zip (Windows); smoke only
#   all       — lint then test (default; run before opening a PR)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

mode="${1:-all}"

if [[ -f uv.lock ]]; then
  uv sync --frozen
else
  uv sync
fi

run_lint() {
  uv run ruff check packages/ tests/
  uv run ruff format --check packages/ tests/
  python3 scripts/generate-dashboard-tokens.py --check
}

run_test() {
  uv run pytest tests/ -v
}

run_sdk() {
  uv run pytest \
    packages/netllm-sdk-openai/tests/ \
    packages/netllm-sdk-anthropic/tests/ \
    tests/test_anthropic_bridge.py \
    tests/test_anthropic_cloud_compat.py \
    tests/test_sdk_isolation.py \
    tests/test_sdk_versions.py \
    tests/test_sdk_versions_payload.py \
    -v
}

run_packaging() {
  local version="${NETLLM_VERSION:-0.0.0-dev}"
  # CI sets NETLLM_VERSION from pyproject (e.g. 0.2.2.1.ci). Local default keeps
  # 0.0.0-dev to exercise prerelease path mapping in build-rpm.sh.
  mkdir -p dist
  case "$(uname -s)" in
    Linux)
      if ! command -v dpkg-deb >/dev/null 2>&1; then
        echo "error: dpkg-deb required (install dpkg-dev)" >&2
        exit 1
      fi
      if ! command -v rpmbuild >/dev/null 2>&1; then
        echo "error: rpmbuild required (install rpm)" >&2
        exit 1
      fi
      chmod +x packaging/linux/build-deb.sh packaging/linux/build-rpm.sh
      NETLLM_VERSION="$version" packaging/linux/build-deb.sh
      NETLLM_VERSION="$version" packaging/linux/build-rpm.sh
      test -n "$(ls -1 dist/*.deb 2>/dev/null)" || {
        echo "error: no .deb in dist/" >&2
        exit 1
      }
      test -n "$(ls -1 dist/*.rpm 2>/dev/null)" || {
        echo "error: no .rpm in dist/" >&2
        exit 1
      }
      ;;
    MINGW*|MSYS*|CYGWIN*|Windows*)
      pwsh -NoProfile -File packaging/windows/build-zip.ps1 -Version "$version"
      test -n "$(ls -1 dist/netllm-*-windows-x64.zip 2>/dev/null)" || {
        echo "error: no windows zip in dist/" >&2
        exit 1
      }
      ;;
    Darwin)
      echo "packaging smoke on macOS: build windows zip layout skipped; run on Linux/Windows CI"
      exit 0
      ;;
    *)
      echo "error: unsupported OS for packaging smoke" >&2
      exit 1
      ;;
  esac
  echo "OK: packaging smoke passed (version=$version)"
}

case "$mode" in
  lint) run_lint ;;
  test) run_test ;;
  sdk) run_sdk ;;
  packaging) run_packaging ;;
  all) run_lint && run_test ;;
  *)
    echo "usage: $0 [lint|test|sdk|packaging|all]" >&2
    exit 2
    ;;
esac
