"""Anti-erosion gate for the `netllm_cli.main` -> `netllm_cli.commands` split.

Phase 1 of docs/architecture/refactor/plan-f24-f26.md moved every command body
out of `main.py`. The hazard the plan names (risk register #3) is a patch target
that keeps *resolving* against `netllm_cli.main` while no longer *intercepting*
the call site, which turns a real assertion into a silent no-op.

Two defences live here:

1. a deny-grep proving no test patches a moved symbol in the old namespace, and
2. one canary per repointed module family: a sentinel-raising fake installed at
   the new target must actually fire when the command runs.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from netllm_cli.commands import (
    _common,
    cloud,
    config_io,
    diagnose,
    init_install,
    join_swarm,
    observe,
    serve_lifecycle,
    sources,
)
from netllm_cli.main import app
from netllm_core.models import NetllmConfig, save_config
from typer.testing import CliRunner

runner = CliRunner()
TESTS_DIR = Path(__file__).resolve().parent
THIS_FILE = Path(__file__).name


class _Sentinel(Exception):
    """Raised by the canary fakes; presence proves interception."""


CANARY = "netllm-canary-sentinel"


def _boom(*_args: object, **_kwargs: object) -> None:
    raise _Sentinel(CANARY)


def _intercepted(result: object) -> bool:
    """The fake fired -- either it escaped, or the command reported its text.

    Some commands wrap upstream failures in a friendly `print_error` + exit
    (`drain` is one), so an escaping exception is not the only proof of
    interception; the sentinel's message reaching the rendered output is.
    """
    return isinstance(result.exception, _Sentinel) or CANARY in result.output


def _cfg(tmp_path: Path, cfg: NetllmConfig | None = None) -> Path:
    cfg_path = tmp_path / "config.toml"
    save_config(cfg or NetllmConfig(), cfg_path)
    return cfg_path


# --------------------------------------------------------------------------
# 1. deny-grep
# --------------------------------------------------------------------------

# Module-level names that no longer live in `netllm_cli.main`. Patching any of
# them through the `netllm_cli.main` namespace cannot intercept anything.
MOVED_SYMBOLS = (
    "asyncio",
    "httpx",
    "json",
    "os",
    "secrets",
    "subprocess",
    "sys",
    "time",
    "mdns_available",
    "control_socket_path",
    "lifecycle_command",
    "scan_local_providers",
    "merge_discovered_provider_urls",
    "global_netllm_installed",
    "global_netllm_binary",
    "global_cli_on_path",
    "path_export_line",
    "suggested_cli",
    "load_config",
    "save_config",
    "emit_export",
    "read_import",
    "_config_path_option",
    "_require_config",
    "_normalize_agent_url",
    "_listen_port_of",
    "_join_command_for",
    "_apply_open_swarm_mode",
    "_apply_secured_swarm_mode",
    "_fetch_join_status",
    "_validate_join_token",
    "_cloud_provider_id_or_exit",
    "sources_toggle",
    "doctor",
    "serve",
    "init",
)

_ALT = "|".join(re.escape(s) for s in MOVED_SYMBOLS)
# `patch("netllm_cli.main.<moved>")` and `patch.dict`/`patch.object` string forms.
_DOTTED = re.compile(rf"netllm_cli\.main\.({_ALT})\b")
# `patch.object(cli_main, "<moved>")` / `monkeypatch.setattr(cli_main, "<moved>")`
# and bare attribute access `cli_main.<moved>`.
_ALIASED = re.compile(rf"""cli_main(?:\s*,\s*["']({_ALT})["']|\.({_ALT})\b)""")


def _test_sources() -> list[tuple[Path, str]]:
    out = []
    for path in sorted(TESTS_DIR.rglob("test_*.py")):
        if path.name == THIS_FILE:
            continue
        out.append((path, path.read_text(encoding="utf-8")))
    return out


def test_no_test_patches_moved_symbols_on_netllm_cli_main() -> None:
    """Deny-grep: the old namespace must not appear for any moved symbol."""
    offenders: list[str] = []
    for path, text in _test_sources():
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _DOTTED.search(line) or _ALIASED.search(line):
                rel = path.relative_to(TESTS_DIR)
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, (
        "These sites patch/read a symbol that no longer lives in "
        "netllm_cli.main, so they no longer intercept the call site. "
        "Repoint them at netllm_cli.commands.<module>:\n" + "\n".join(offenders)
    )


def test_deny_grep_regex_would_catch_a_regression() -> None:
    """The gate above is only worth its runtime if it can actually fail."""
    assert _DOTTED.search('patch("netllm_cli.main.mdns_available")')
    assert _ALIASED.search('patch.object(cli_main, "_validate_join_token")')
    assert _ALIASED.search("src = inspect.getsource(cli_main.sources_toggle)")
    # `cli_main.app` is the one attribute that legitimately stays put.
    assert not _ALIASED.search("runner.invoke(cli_main.app, [...])")


def test_main_module_no_longer_defines_moved_symbols() -> None:
    """Belt and braces: prove the deny-list is real by checking the module."""
    import netllm_cli.main as cli_main

    still_there = [s for s in MOVED_SYMBOLS if hasattr(cli_main, s)]
    assert not still_there, f"main.py still exposes moved symbols: {still_there}"


# --------------------------------------------------------------------------
# 2. canaries -- one per repointed family
# --------------------------------------------------------------------------


def test_canary_init_install_namespace(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    with patch.object(init_install, "scan_local_providers", _boom):
        result = runner.invoke(
            app, ["init", "--config", str(cfg_path), "--no-global-cli"]
        )
    assert _intercepted(result), result.output


def test_canary_join_swarm_namespace(tmp_path: Path) -> None:
    cfg_path = _cfg(tmp_path)
    with patch.object(join_swarm, "_fetch_join_status", _boom):
        result = runner.invoke(
            app,
            ["join", "http://192.168.1.20:11400", "--config", str(cfg_path)],
        )
    assert _intercepted(result), result.output


def test_canary_observe_httpx_namespace(tmp_path: Path) -> None:
    cfg_path = _cfg(tmp_path)
    with patch.object(observe.httpx, "Client", _boom):
        result = runner.invoke(app, ["drain", "on", "--config", str(cfg_path)])
    assert _intercepted(result), result.output


def test_canary_serve_lifecycle_asyncio_namespace(tmp_path: Path) -> None:
    cfg_path = _cfg(tmp_path)
    with patch.object(serve_lifecycle.asyncio, "run", _boom):
        result = runner.invoke(app, ["serve", "--config", str(cfg_path)])
    assert _intercepted(result), result.output


def test_canary_serve_lifecycle_command_namespace() -> None:
    with patch.object(serve_lifecycle, "lifecycle_command", _boom):
        result = runner.invoke(app, ["stop"])
    assert _intercepted(result), result.output


@pytest.mark.parametrize("symbol", ["scan_local_providers", "mdns_available"])
def test_canary_diagnose_namespace(tmp_path: Path, symbol: str) -> None:
    cfg = NetllmConfig()
    cfg.swarm.mdns = True
    cfg.agent.advertise = True
    cfg_path = _cfg(tmp_path, cfg)
    with patch.object(diagnose, symbol, _boom):
        result = runner.invoke(app, ["doctor", "--config", str(cfg_path)])
    assert _intercepted(result), result.output


def test_canary_config_io_namespace(tmp_path: Path) -> None:
    cfg_path = _cfg(tmp_path)
    with patch.object(config_io, "emit_export", _boom):
        result = runner.invoke(app, ["config", "export", "--config", str(cfg_path)])
    assert _intercepted(result), result.output


def test_canary_cloud_namespace(tmp_path: Path) -> None:
    cfg_path = _cfg(tmp_path)
    with patch.object(cloud, "load_config", _boom):
        result = runner.invoke(app, ["cloud", "list", "--config", str(cfg_path)])
    assert _intercepted(result), result.output


def test_canary_sources_namespace(tmp_path: Path) -> None:
    cfg_path = _cfg(tmp_path)
    with patch.object(sources, "load_config", _boom):
        result = runner.invoke(app, ["sources", "list", "--config", str(cfg_path)])
    assert _intercepted(result), result.output


def test_canary_common_helpers_are_one_object() -> None:
    """`_common` is a from-import seam, so identity is the canary.

    Patching `_common._config_path_option` cannot intercept a consumer that
    already bound the name; asserting shared identity is what proves no module
    grew a private copy during the move.
    """
    for module in (
        init_install,
        join_swarm,
        observe,
        serve_lifecycle,
        diagnose,
        config_io,
        cloud,
        sources,
    ):
        assert module._config_path_option is _common._config_path_option, module
    for module in (join_swarm, serve_lifecycle, diagnose):
        assert module._require_config is _common._require_config, module
    assert join_swarm._normalize_agent_url is _common._normalize_agent_url
    assert join_swarm._listen_port_of is init_install._listen_port_of
    assert join_swarm._join_command_for is init_install._join_command_for


# --------------------------------------------------------------------------
# 3. entry-point smokes the plan names as Phase 1 exit criteria
# --------------------------------------------------------------------------


def test_module_entry_point_help() -> None:
    """`python -m netllm_cli.main --help` (apps/netllm-mac build.sh:120)."""
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "netllm_cli.main", "--help"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "Network LLM router" in proc.stdout
    for command in ("init", "serve", "doctor", "config", "cloud", "sources"):
        assert command in proc.stdout
