"""The anti-erosion gate, as a test (plan-f24-f26.md §1, §3 Phase 6).

``scripts/check-engine-erosion.py`` is the CI-runnable form (wired into
``scripts/ci.sh lint``); this module runs the same check inside the suite so
a local ``pytest`` catches the regression too, and adds the assertions that
belong with the vector corpus: that the checker actually rejects the shapes
it claims to, and that the loop really is the only one left for the
migrated surfaces.
"""

from __future__ import annotations

import importlib.util
import inspect
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER = REPO_ROOT / "scripts/check-engine-erosion.py"
ENGINE = REPO_ROOT / "packages/netllm-agent/src/netllm_agent/service/engine.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("engine_erosion_check", CHECKER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_engine_is_surface_agnostic() -> None:
    """The gate itself: engine.py names no Surface and imports no adapter."""
    violations = _load_checker().check(ENGINE)
    assert not violations, "\n".join(violations)


def test_checker_rejects_each_erosion_shape(tmp_path: Path) -> None:
    """A gate nobody has seen fail is a gate nobody knows works."""
    check = _load_checker().check
    cases = {
        "concrete adapter import": "from .surfaces.chat import ChatAdapter\n",
        "surfaces package import": (
            "from netllm_agent.service.surfaces import adapter_for\n"
        ),
        "adapter name from base": "from .surfaces.base import ChatAdapter\n",
        "surface enum member": (
            "from .taxonomy import Surface\ndef f(plan):\n    return Surface.MESSAGES\n"
        ),
        "plan.surface branch": (
            "def f(plan):\n"
            "    if plan.surface == 'messages':\n"
            "        return 1\n"
            "    return 0\n"
        ),
        "isinstance dispatch": (
            "def f(adapter, ChatAdapter):\n"
            "    return isinstance(adapter, ChatAdapter)\n"
        ),
    }
    for label, snippet in cases.items():
        probe = tmp_path / "engine.py"
        probe.write_text(snippet)
        assert check(probe), f"gate failed to reject: {label}"


def test_checker_accepts_the_protocol_import(tmp_path: Path) -> None:
    probe = tmp_path / "engine.py"
    probe.write_text(
        "from .surfaces.base import SurfaceAdapter\n"
        "async def run(adapter: SurfaceAdapter, plan):\n"
        "    return await adapter.invoke(plan, adapter.build_invocation(plan, None))\n"
    )
    assert not _load_checker().check(probe)


# --- the migrated surfaces really do share the loop -------------------------

MIGRATED_ENTRY_POINTS = (
    "proxy_chat_completion",
    "proxy_embeddings",
    "proxy_messages",
)

# Phase 7. RESP-S is absent on purpose: it is pure edge translation over
# `proxy_chat_completion_stream` and never touched a backend itself.
MIGRATED_STREAM_ENTRY_POINTS = (
    "proxy_chat_completion_stream",
    "proxy_messages_stream",
)


def test_migrated_entry_points_hold_no_failover_loop() -> None:
    """Each migrated surface delegates; none keeps a private loop.

    Phase 7 completed the set — every proxy entry point in the service is
    now a plan plus a call into the engine.
    """
    from netllm_agent.service import AgentService

    for name in MIGRATED_ENTRY_POINTS + MIGRATED_STREAM_ENTRY_POINTS:
        src = textwrap.dedent(inspect.getsource(getattr(AgentService, name)))
        expected = "open_stream(" if name.endswith("_stream") else "run_with_failover("
        assert expected in src, f"{name} no longer uses the engine"
        for banned in (
            "self.pool.acquire",
            "self.pool.release",
            "_select_backend_for_request",
            "_source_release",
            "while attempt",
        ):
            assert banned not in src, (
                f"{name} has grown its own failover loop again ({banned!r}); "
                "the engine is supposed to be the only one"
            )


def test_no_service_entry_point_still_walks_candidates() -> None:
    """The census, not the spot check: after Phase 7 no *code* in the service
    package selects, acquires or releases a backend for a request.

    Scanned over the token stream with strings and comments removed, so the
    prose that explains what was deleted does not itself trip the gate —
    and so a loop cannot hide inside a docstring-shaped edit either.

    [Phase 9] ``service.py`` became the ``service/`` package, so the census
    walks every module in it (``engine.py`` excepted — it *is* the loop).
    Scanning only ``__init__.py`` would have quietly reduced this gate to
    nothing the moment the split landed.
    """
    import io
    import tokenize

    import netllm_agent.service as service_package

    pkg_root = Path(inspect.getfile(service_package)).parent
    engine = pkg_root / "engine.py"
    modules = sorted(p for p in pkg_root.rglob("*.py") if p != engine)
    assert len(modules) >= 12, f"service package census found only {modules}"

    for path in modules:
        source = path.read_text()
        code = "".join(
            tok.string + " "
            for tok in tokenize.generate_tokens(io.StringIO(source).readline)
            if tok.type not in (tokenize.STRING, tokenize.COMMENT)
        )
        for banned in (
            "pool . acquire",
            "pool . release",
            "yielded_any",
            "fallback_iter",
            "candidates_exhausted",
        ):
            assert banned not in code, (
                f"{path.relative_to(pkg_root)} contains {banned!r} in code — "
                "a failover loop has grown back outside engine.py"
            )
