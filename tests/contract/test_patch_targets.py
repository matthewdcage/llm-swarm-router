"""Phase 9 patch-target repoint: deny-greps + one canary per (name, module).

The F-26 split moved module globals out of ``netllm_agent.service`` into the
module that *uses* them. ``mock.patch`` binds by module namespace, so a test
still patching the old path would silently stop intercepting and keep
passing — it would just be testing the real code path with a mock nobody
calls. That is the exact failure plan-f24-f26.md §7.3 names as risk 3.

Two gates, because either one alone is insufficient:

1. **Deny-grep** — no test may name a moved symbol at its pre-split path.
   Catches the sites; says nothing about whether the new path works.
2. **Canary** — for every ``(name, module)`` *pair*, patch the new path with
   a sentinel-raising fake and assert the sentinel actually fires. Catches
   a repoint aimed at a module that imported the name but never calls it.

The pair granularity matters and is the lesson the dependency graph carried
over from the CLI split (dependency-graph.md §2.4: ``scan_local_providers``
is imported by five of the CLI's command modules). Patching *a* module that
holds the name proves nothing about the module whose call site you care
about; only the pair does.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from unittest import mock

import pytest
from netllm_core.models import NetllmConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS = REPO_ROOT / "tests"

# Symbols that used to be reachable as ``netllm_agent.service.<name>`` and
# are now owned by a submodule. Patching the old path is a silent no-op.
MOVED_PATCH_TARGETS: tuple[tuple[str, str], ...] = (
    ("scan_local_providers", "netllm_agent.service.backends"),
)

# Import paths that moved wholesale in Phase 9 (module, not just a symbol).
MOVED_MODULE_PATHS: tuple[str, str] = (
    r"netllm_agent\.engine\b",
    r"netllm_agent\.surfaces\b",
)


def _test_sources() -> list[Path]:
    return sorted(TESTS.rglob("*.py"))


@pytest.mark.parametrize(("name", "module"), MOVED_PATCH_TARGETS)
def test_deny_grep_old_service_patch_path(name: str, module: str) -> None:
    """No test may still spell the pre-split patch target."""
    needle = f"netllm_agent.service.{name}"
    offenders = [
        f"{p.relative_to(REPO_ROOT)}:{i}"
        for p in _test_sources()
        for i, line in enumerate(p.read_text().splitlines(), 1)
        if needle in line
    ]
    assert not offenders, (
        f"{needle!r} no longer intercepts — it moved to {module}.{name}. "
        f"Repoint: {offenders}"
    )


@pytest.mark.parametrize("pattern", MOVED_MODULE_PATHS)
def test_deny_grep_old_module_paths(pattern: str) -> None:
    """``engine`` and ``surfaces`` live under ``service/`` now."""
    rx = re.compile(pattern + r"(?!\w)")
    offenders = [
        f"{p.relative_to(REPO_ROOT)}:{i}"
        for p in _test_sources()
        for i, line in enumerate(p.read_text().splitlines(), 1)
        if rx.search(line) and "netllm_agent.service." not in line
    ]
    assert not offenders, (
        f"{pattern} moved under netllm_agent.service — repoint: {offenders}"
    )


def test_deny_grep_no_service_namespace_shim() -> None:
    """The split must not grow a re-export that makes both spellings 'work'.

    A shim would resurrect exactly the module-global indirection the plan
    refuses (§3 Phase 9), and would make the deny-grep above pass while the
    old patch path silently intercepts nothing.
    """
    init = (
        REPO_ROOT / "packages/netllm-agent/src/netllm_agent/service/__init__.py"
    ).read_text()
    for name, _module in MOVED_PATCH_TARGETS:
        assert f"import {name}" not in init and f" {name}," not in init, (
            f"service/__init__.py re-exports {name} — no shims (plan §3 Phase 9)"
        )


class _Canary(RuntimeError):
    """Fired by the fake standing in for a repointed patch target."""


async def test_canary_scan_local_providers_in_backends() -> None:
    """(scan_local_providers, netllm_agent.service.backends) really intercepts.

    ``_scan_local_backends`` resolves the name through *its own* module
    globals, so this is the pair that matters — not merely "some module has
    the attribute".
    """
    from netllm_agent.service import AgentService

    service = AgentService(NetllmConfig())

    async def _boom(*args: object, **kwargs: object) -> None:
        raise _Canary("scan_local_providers intercepted")

    with mock.patch("netllm_agent.service.backends.scan_local_providers", new=_boom):
        with pytest.raises(_Canary):
            await service.refresh_local_backends(force_scan=True)


def test_canary_agent_service_class_attribute_patch_still_binds() -> None:
    """Mixin composition must not break ``patch(...AgentService.<method>)``.

    Every method now arrives from a mixin, so ``AgentService.__dict__`` is
    nearly empty. ``mock.patch`` sets the attribute on ``AgentService``
    itself, which shadows the mixin — assert that, because the alternative
    (patching the mixin and the instance resolving elsewhere) would be
    invisible in a passing test.
    """
    from netllm_agent.service import AgentService

    service = AgentService(NetllmConfig())
    for name in ("proxy_messages", "_source_admit", "refresh_local_backends"):
        with mock.patch.object(AgentService, name) as patched:
            assert getattr(AgentService, name) is patched
            assert getattr(service, name) is not None
            assert getattr(type(service), name) is patched


def test_repo_wide_grep_for_old_paths() -> None:
    """Belt and braces: nothing anywhere (docs excepted) names the old paths.

    ``docs/architecture/refactor/`` describes the *pre*-split state on
    purpose, so it is excluded rather than rewritten.
    """
    proc = subprocess.run(
        [
            "git",
            "grep",
            "-n",
            "-E",
            r"netllm_agent\.service\.scan_local_providers"
            r"|netllm_agent\.(engine|surfaces)\b",
            "--",
            ".",
            ":(exclude)docs/architecture/refactor",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    hits = [
        line
        for line in proc.stdout.splitlines()
        if "netllm_agent.service.engine" not in line
        and "netllm_agent.service.surfaces" not in line
        and not line.startswith("tests/contract/test_patch_targets.py")
    ]
    assert not hits, "stale pre-split import/patch paths: " + "\n".join(hits)
