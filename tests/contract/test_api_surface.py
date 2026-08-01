"""API-surface freeze test (plan-f24-f26.md §1 "Frozen external contracts").

Later refactor phases move code between modules; these assertions pin the
externally consumed surface so a move that breaks a consumer (app.py, the
test suite's patch targets, CLI entry points) fails here by name instead of
somewhere deep in a stack trace.
"""

from __future__ import annotations

import subprocess
import sys
from unittest import mock

# The 20-attribute surface app.py consumes on an AgentService instance
# (create_app + route closures). Frozen: a service split must keep every
# name reachable as an attribute of AgentService.
APP_CONSUMED_SERVICE_SURFACE = (
    "refresh_local_backends",
    "start_background",
    "stop_background",
    "telemetry",
    "swarm",
    "pool",
    "config",
    "draining",
    "status_payload_enriched",
    "handle_heartbeat",
    "apply_config",
    "list_models_aggregated",
    "cloud_provider_models_probe",
    "proxy_chat_completion",
    "proxy_chat_completion_stream",
    "proxy_responses",
    "proxy_responses_stream",
    "proxy_embeddings",
    "proxy_messages",
    "proxy_messages_stream",
)

PATCHABLE_METHODS = (
    "proxy_chat_completion",
    "proxy_chat_completion_stream",
    "proxy_responses",
    "proxy_responses_stream",
    "proxy_embeddings",
    "proxy_messages",
    "proxy_messages_stream",
    "refresh_local_backends",
    "_source_admit",
)


def test_service_module_exports() -> None:
    import netllm_agent.service as service_mod

    assert hasattr(service_mod, "AgentService")
    assert hasattr(service_mod, "SourceCapacityExceeded")
    assert hasattr(service_mod, "LEGACY_CLOUD_BACKEND_IDS")
    assert service_mod.LEGACY_CLOUD_BACKEND_IDS == frozenset(
        {"openai-cloud", "anthropic-cloud"}
    )
    # The test suite's most common patch target must stay importable from
    # the service namespace until the Phase 9 repoint.
    assert hasattr(service_mod, "scan_local_providers")


def test_agent_service_surface_app_consumes() -> None:
    from netllm_agent.service import AgentService
    from netllm_core.models import NetllmConfig

    service = AgentService(NetllmConfig())
    missing = [
        name for name in APP_CONSUMED_SERVICE_SURFACE if not hasattr(service, name)
    ]
    assert not missing, f"AgentService lost app.py-consumed attributes: {missing}"


def test_agent_service_methods_are_class_attribute_patchable() -> None:
    """unittest.mock.patch("netllm_agent.service.AgentService.<m>") must keep
    intercepting — dozens of existing tests rely on it."""
    from netllm_agent.service import AgentService

    for name in PATCHABLE_METHODS:
        assert hasattr(AgentService, name), f"AgentService.{name} vanished"
        with mock.patch.object(AgentService, name) as patched:
            assert getattr(AgentService, name) is patched


def test_source_capacity_exceeded_contract() -> None:
    from netllm_agent.service import SourceCapacityExceeded

    exc = SourceCapacityExceeded("cli", 3)
    assert exc.source_id == "cli"
    assert exc.limit == 3
    assert "cli" in str(exc)


def test_cli_module_surface() -> None:
    import netllm_cli.main as cli_main
    import typer
    from netllm_core.version import get_version

    assert isinstance(cli_main.app, typer.Typer)
    # The version-sync literals (test_version_sync.py) must survive the split.
    assert cli_main.__version__ == get_version()


def test_cli_dash_m_runnable() -> None:
    """`python -m netllm_cli.main --help` — the build.sh smoke contract."""
    proc = subprocess.run(
        [sys.executable, "-m", "netllm_cli.main", "--help"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "netllm" in (proc.stdout + proc.stderr).lower()


def test_app_factory_signature_frozen() -> None:
    import inspect

    from netllm_agent.app import create_app

    params = inspect.signature(create_app).parameters
    assert "config" in params
    assert "config_path" in params


# --- Phase 4: the prologue lives in exactly one place ----------------------

_PROXY_ENTRY_POINTS = (
    "proxy_chat_completion",
    "proxy_chat_completion_stream",
    "proxy_embeddings",
    "proxy_messages",
    "proxy_messages_stream",
)

# Everything build_request_plan owns. A proxy entry point that calls one of
# these directly has started to grow a second prologue — the exact defect
# F-24 describes and the dependency graph counts (57 of service.py's 123
# cross-cluster call sites were these five copies).
_PLAN_OWNED = (
    "_normalize_headers",
    "_attribute_source",
    "_source_config",
    "_classify_and_record_scenario",
    "_apply_source_model_rewrite",
    "_apply_scenario_model",
    "_reject_non_chat_model",
    "_reject_non_chat_messages_model",
    "_reject_non_embedding_model",
    "_resolved_routing",
    "_source_admit",
    "extract_shard_context",
)


def _entry_point_source(name: str) -> str:
    import inspect
    import textwrap

    from netllm_agent.service import AgentService

    return textwrap.dedent(inspect.getsource(getattr(AgentService, name)))


def test_every_proxy_entry_point_builds_exactly_one_request_plan() -> None:
    for name in _PROXY_ENTRY_POINTS:
        src = _entry_point_source(name)
        assert src.count("self.build_request_plan(") == 1, (
            f"{name}: expected exactly one build_request_plan call, "
            f"found {src.count('self.build_request_plan(')}"
        )


def test_no_proxy_entry_point_reimplements_the_prologue() -> None:
    leaks: list[str] = []
    for name in _PROXY_ENTRY_POINTS:
        src = _entry_point_source(name)
        leaks += [f"{name} -> {sym}" for sym in _PLAN_OWNED if sym in src]
    assert not leaks, (
        "proxy entry points reaching past build_request_plan into the "
        f"prologue: {leaks}"
    )
