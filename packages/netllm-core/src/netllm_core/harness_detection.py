"""PATH-only detection for known harness CLIs.

Deliberately narrower than the buzz.xyz reference's resolver (no
login-shell PATH resurrection, no hardcoded vendor install-dir scan, no
subprocess spawn) -- see docs/cli-source-routing-plan.md Phase 4c. Revisit
only if real usage shows PATH gaps (e.g. GUI-launched processes with a
sanitized PATH, the same caveat `netllm doctor` already has to reason
about for the agent itself).
"""

from __future__ import annotations

import shutil
import time

from netllm_core.known_harnesses import KnownHarness

_CACHE_TTL_S = 300.0
_cache: dict[str, tuple[float, bool]] = {}


def detect(known: KnownHarness) -> bool:
    """True if any of `known.cli_commands` resolves on PATH.

    In-process TTL cache keyed by harness id -- cheap enough to call on
    every /netllm/v1/harnesses request without re-probing PATH each time.
    """
    now = time.monotonic()
    cached = _cache.get(known.id)
    if cached is not None and now - cached[0] < _CACHE_TTL_S:
        return cached[1]
    found = any(shutil.which(cmd) is not None for cmd in known.cli_commands)
    _cache[known.id] = (now, found)
    return found


def clear_cache() -> None:
    """Test-only hook."""
    _cache.clear()
