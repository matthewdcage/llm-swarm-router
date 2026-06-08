#!/usr/bin/env bash
# Local and CI checks. Usage: scripts/ci.sh [lint|test|all]
#   lint — ruff check + format --check (~1s)
#   test — pytest (~12s)
#   all  — lint then test (default; run before opening a PR)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

mode="${1:-all}"

uv sync

run_lint() {
  uv run ruff check packages/ tests/
  uv run ruff format --check packages/ tests/
}

run_test() {
  uv run pytest tests/ -v
}

case "$mode" in
  lint) run_lint ;;
  test) run_test ;;
  all) run_lint && run_test ;;
  *)
    echo "usage: $0 [lint|test|all]" >&2
    exit 2
    ;;
esac
