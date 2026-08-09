"""Axis B conformance kit: the contract every local provider must satisfy.

Parameterized over `LOCAL_PROVIDERS`, so adding a provider acquires this
whole suite with **zero test-file edits** — that is the point of the kit, and
the reason the registry is worth having at all. Before it, `grep -rn
KNOWN_PROVIDERS tests/` returned nothing: eleven parallel maps keyed on the
same id and not one test that the roster was consistent.

Note the deliberate asymmetry (PROGRAM.md §3 Axis B): label coverage
parameterizes over `LOCAL_PROVIDERS | NON_DISCOVERABLE_LABELS` because
`custom` is a labelled routing id with no ports and no probe, while
everything port- or key-shaped parameterizes over `LOCAL_PROVIDERS` alone.
"""

from __future__ import annotations

import sys
from typing import get_args

import pytest
from netllm_core.local_providers import (
    LOCAL_PROVIDERS,
    NON_DISCOVERABLE_LABELS,
    LocalProviderSpec,
    api_key_env_for,
    default_api_key_for,
    get_local_provider_spec,
    providers_for_platform,
)
from netllm_core.models import Backend, NetllmConfig
from netllm_discovery.local import (
    _api_key_for_provider,
    candidate_urls_for_provider,
)

SPECS = list(LOCAL_PROVIDERS.values())
IDS = [spec.id for spec in SPECS]


@pytest.fixture(params=SPECS, ids=IDS)
def spec(request: pytest.FixtureRequest) -> LocalProviderSpec:
    return request.param


# --- spec well-formedness -------------------------------------------------


def test_spec_is_well_formed(spec: LocalProviderSpec) -> None:
    assert spec.id and spec.id.islower()
    assert spec.display_name and spec.short_label
    assert spec.default_ports, "a discoverable provider needs at least one port"
    assert all(0 < p < 65536 for p in spec.default_ports)
    assert spec.platforms, "a provider enabled nowhere is unreachable"
    assert spec.hint_port() in spec.default_ports


def test_registry_key_matches_spec_id() -> None:
    for key, value in LOCAL_PROVIDERS.items():
        assert key == value.id, f"registry key {key!r} != spec id {value.id!r}"


def test_provider_id_literal_matches_the_registry() -> None:
    """`ProviderId` stays hand-written (a derived Literal blinds basedpyright),
    so its agreement with the registry is asserted rather than assumed."""
    from netllm_core.models import ProviderId

    literal = set(get_args(ProviderId))
    missing = set(LOCAL_PROVIDERS) - literal
    assert not missing, f"ProviderId is missing {sorted(missing)}"
    # Equality, not containment, over the discoverable half: ProviderId also
    # carries routing-only ids (custom, anthropic, openai) that are not local
    # servers, so the exact contract is "every extra is declared somewhere".
    routing_only = literal - set(LOCAL_PROVIDERS)
    declared = set(NON_DISCOVERABLE_LABELS) | {"anthropic", "openai"}
    assert routing_only <= declared, (
        f"ProviderId has undeclared ids {sorted(routing_only - declared)} -- "
        "add them to the registry or to the declared routing-only set"
    )


# --- candidate URLs -------------------------------------------------------


def test_every_default_port_is_probed_on_both_hosts(spec: LocalProviderSpec) -> None:
    urls = candidate_urls_for_provider(spec.id, NetllmConfig())
    for port in spec.default_ports:
        for host in ("127.0.0.1", "localhost"):
            assert f"http://{host}:{port}/v1" in urls, (
                f"{spec.id}: {host}:{port} is in the registry but never probed"
            )


def test_port_env_reaches_the_candidate_list(
    spec: LocalProviderSpec, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not spec.port_env:
        pytest.skip(f"{spec.id} declares no port_env")
    monkeypatch.setenv(spec.port_env, "9999")
    urls = candidate_urls_for_provider(spec.id, NetllmConfig())
    assert "http://127.0.0.1:9999/v1" in urls, (
        f"{spec.id}: {spec.port_env} is declared but not honoured"
    )


def test_host_env_reaches_the_candidate_list(
    spec: LocalProviderSpec, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not spec.host_env:
        pytest.skip(f"{spec.id} declares no host_env")
    monkeypatch.setenv(spec.host_env, "10.0.0.7:4321")
    urls = candidate_urls_for_provider(spec.id, NetllmConfig())
    assert "http://10.0.0.7:4321/v1" in urls
    monkeypatch.setenv(spec.host_env, ":4321")
    assert "http://127.0.0.1:4321/v1" in candidate_urls_for_provider(
        spec.id, NetllmConfig()
    )
    monkeypatch.setenv(spec.host_env, "10.0.0.8")
    assert f"http://10.0.0.8:{spec.default_host_port}/v1" in (
        candidate_urls_for_provider(spec.id, NetllmConfig())
    )


def test_candidates_are_deduped_and_normalized(spec: LocalProviderSpec) -> None:
    urls = candidate_urls_for_provider(spec.id, NetllmConfig())
    assert len(urls) == len(set(urls))
    assert all(u.endswith("/v1") for u in urls)


# --- credentials: the two-copy drift this kit exists to kill --------------


def test_api_key_env_resolves_through_both_paths(
    spec: LocalProviderSpec, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The env var must work through discovery *and* through `Backend`.

    These were two independent hand-written maps (`local.py` and
    `models.py`), free to drift; a provider present in one and absent from
    the other authenticated on one path and 401'd on the other.
    """
    if not spec.api_key_env:
        pytest.skip(f"{spec.id} declares no api_key_env")
    monkeypatch.setenv(spec.api_key_env, "sentinel-key")
    assert _api_key_for_provider(spec.id, NetllmConfig()) == "sentinel-key"
    backend = Backend(id=spec.id, base_url="http://127.0.0.1:1/v1", provider=spec.id)
    assert backend.resolve_api_key() == "sentinel-key"


def test_default_api_key_applies_on_both_paths(spec: LocalProviderSpec) -> None:
    if not spec.default_api_key:
        pytest.skip(f"{spec.id} has no built-in default key")
    assert _api_key_for_provider(spec.id, NetllmConfig()) == spec.default_api_key
    backend = Backend(id=spec.id, base_url="http://127.0.0.1:1/v1", provider=spec.id)
    assert backend.resolve_api_key() == spec.default_api_key


@pytest.mark.parametrize("unknown", ["custom", "peer:node-1", "definitely-not-real"])
def test_non_registry_providers_get_no_env_hint(unknown: str) -> None:
    """The dict-miss is load-bearing, not an oversight.

    `netllm doctor` names an env var in its 401 fix hint only when one really
    exists. Deriving the name as `PROVIDER.upper()_API_KEY` would tell an
    operator to set `CUSTOM_API_KEY`, which nothing reads.
    """
    assert api_key_env_for(unknown) == ""
    assert default_api_key_for(unknown) == ""
    assert get_local_provider_spec(unknown) is None


# --- platform gating ------------------------------------------------------


def test_platform_membership_follows_the_spec(spec: LocalProviderSpec) -> None:
    for platform in ("darwin", "linux", "win32"):
        enabled = spec.id in providers_for_platform(platform)
        assert enabled is (platform in spec.platforms), (
            f"{spec.id} on {platform}: registry says "
            f"{platform in spec.platforms}, resolver says {enabled}"
        )


def test_default_discovery_providers_matches_this_platform() -> None:
    from netllm_core.platform import default_discovery_providers

    assert default_discovery_providers() == providers_for_platform(sys.platform)


# --- CLI-visible projections ---------------------------------------------


def test_every_labelled_id_is_in_a_roster() -> None:
    from netllm_cli.ui import _PROVIDER_LABELS

    expected = set(LOCAL_PROVIDERS) | set(NON_DISCOVERABLE_LABELS)
    assert set(_PROVIDER_LABELS) == expected


def test_label_matches_the_spec(spec: LocalProviderSpec) -> None:
    from netllm_cli.ui import _PROVIDER_LABELS

    assert _PROVIDER_LABELS[spec.id] == spec.short_label


def test_offline_hint_names_this_platforms_providers_and_ports() -> None:
    from netllm_cli.ui import default_provider_port_hint

    hint = default_provider_port_hint()
    for spec_ in SPECS:
        present = spec_.short_label in hint
        assert present is (sys.platform in spec_.platforms), (
            f"{spec_.id}: hint membership disagrees with platform gating"
        )
        if present:
            assert f":{spec_.hint_port()}" in hint, (
                f"{spec_.id}: hint quotes a port that is not the scanned one"
            )


def test_discovery_roster_is_derived_not_mirrored() -> None:
    """`KNOWN_PROVIDERS` keeps its public shape but not its own copy."""
    from netllm_discovery.local import DEFAULT_API_KEYS, KNOWN_PROVIDERS

    assert [row[0] for row in KNOWN_PROVIDERS] == list(LOCAL_PROVIDERS)
    for pid, name, ports in KNOWN_PROVIDERS:
        assert name == LOCAL_PROVIDERS[pid].display_name
        assert ports == list(LOCAL_PROVIDERS[pid].default_ports)
    assert DEFAULT_API_KEYS == {
        s.id: s.default_api_key for s in SPECS if s.default_api_key
    }


# --- regressions caught by the Phase 1+3 validation swarm -----------------


def test_offline_hint_does_not_repeat_a_port(spec: LocalProviderSpec) -> None:
    """Every port is probed on BOTH 127.0.0.1 and localhost.

    A naive slice of `probed_urls` therefore rendered
    "(scanned ports: 1234, 1234, 41334, 41334)" to the operator. This was
    shipped and had zero coverage -- the whole hint function did, which is why
    the rewrite was invisible.
    """
    from netllm_cli.ui import offline_provider_hints

    if sys.platform not in spec.platforms or len(spec.default_ports) <= 1:
        pytest.skip(f"{spec.id} renders no port list on this platform")
    probed = [
        f"http://{host}:{port}/v1"
        for port in spec.default_ports
        for host in ("127.0.0.1", "localhost")
    ]
    hints = offline_provider_hints(
        [{"id": spec.id, "status": "offline", "probed_urls": probed}]
    )
    assert hints, f"{spec.id}: offline provider produced no hint at all"
    listed = hints[0].split("scanned ports: ")[-1].rstrip(")").split(", ")
    assert listed == list(dict.fromkeys(listed)), (
        f"{spec.id}: duplicate ports in operator output: {hints[0]}"
    )


def test_offline_hint_falls_back_to_registry_ports(spec: LocalProviderSpec) -> None:
    """With no probe record, the hint still quotes the real scan ports."""
    from netllm_cli.ui import offline_provider_hints

    if sys.platform not in spec.platforms or len(spec.default_ports) <= 1:
        pytest.skip(f"{spec.id} renders no port list on this platform")
    hints = offline_provider_hints([{"id": spec.id, "status": "offline"}])
    for port in spec.default_ports:
        assert str(port) in hints[0]


def test_an_unknown_platform_still_gets_cross_platform_providers() -> None:
    """The allowlist must not strand an unenumerated platform.

    The behaviour this replaced was "everything except oMLX unless darwin" --
    an else-branch covering freebsd, aix, cygwin and anything else. A bare
    allowlist silently returns [], turning "works, minus oMLX" into
    "discovers nothing", with no error to explain why.
    """
    for exotic in ("freebsd14", "aix", "cygwin", "sunos5"):
        got = providers_for_platform(exotic)
        assert got, f"{exotic} was stranded with no default providers"
        assert "omlx" not in got, (
            f"{exotic} was offered oMLX, which is Apple-Silicon only"
        )


# --- client projections (PROGRAM.md Axis B: "all five client/doc") --------


def _swift_bootstrap() -> dict[str, tuple[str, int]]:
    """Parse SettingsViewModel.localProviderBootstrap into {id: (label, port)}."""
    import re

    from conformance.projections import REPO_ROOT

    path = REPO_ROOT / "apps/netllm-mac/Sources/AppView/SettingsViewModel.swift"
    text = path.read_text(encoding="utf-8")
    start = text.find("static let localProviderBootstrap")
    assert start != -1, f"{path.name}: localProviderBootstrap not found"
    # Skip the type annotation's own brackets: the declaration reads
    # `... : [(id: String, ...)] = [` and a naive find("]") stops inside it.
    open_at = text.find("= [", start)
    assert open_at != -1, f"{path.name}: localProviderBootstrap has no literal"
    body = text[open_at : text.find("\n    ]", open_at)]
    out: dict[str, tuple[str, int]] = {}
    for pid, label, port in re.findall(
        r'id:\s*"([^"]+)",\s*label:\s*"([^"]+)",\s*port:\s*(\d+)', body
    ):
        out[pid] = (label, int(port))
    return out


def test_swift_bootstrap_matches_the_registry(spec: LocalProviderSpec) -> None:
    """The macOS prefill must agree with what discovery actually scans.

    It did not: the prefill URL came from a ternary that gave every provider
    except oMLX and Ollama port 1234, so vLLM was offered LM Studio's port --
    a user accepting the default configured a URL nothing serves. Labels came
    from `.capitalized`, rendering "Omlx", "Lmstudio", "Vllm".
    """
    bootstrap = _swift_bootstrap()
    assert spec.id in bootstrap, (
        f"SettingsViewModel.localProviderBootstrap is missing {spec.id!r}"
    )
    label, port = bootstrap[spec.id]
    assert label == spec.short_label, (
        f"{spec.id}: Swift label {label!r} != registry {spec.short_label!r}"
    )
    assert port in spec.default_ports, (
        f"{spec.id}: Swift prefills port {port}, which discovery never scans "
        f"(registry ports: {list(spec.default_ports)})"
    )


def test_swift_bootstrap_has_no_extra_providers() -> None:
    assert set(_swift_bootstrap()) == set(LOCAL_PROVIDERS)


def test_the_agent_serves_the_registry_to_its_clients() -> None:
    """The route whose absence forced every client to hand-mirror the roster.

    The cloud tab could derive itself from GET /netllm/v1/cloud/providers; the
    discovery tab had no equivalent, which is why dashboard.js and three Swift
    files each kept their own copy -- and why those copies drifted.
    """
    from netllm_agent.admin import local_provider_registry_payload

    rows = {row["id"]: row for row in local_provider_registry_payload()}
    assert set(rows) == set(LOCAL_PROVIDERS)
    for pid, spec_ in LOCAL_PROVIDERS.items():
        assert rows[pid]["display_name"] == spec_.display_name
        assert rows[pid]["default_ports"] == list(spec_.default_ports)
        assert rows[pid]["platforms"] == list(spec_.platforms)
        assert rows[pid]["api_key_env"] == spec_.api_key_env


def test_the_local_provider_route_is_registered() -> None:
    import json

    from conformance.projections import REPO_ROOT

    manifest = json.loads(
        (REPO_ROOT / "tests/contract/routes.json").read_text(encoding="utf-8")
    )
    paths = {row["path"] for row in manifest["routes"]}
    assert "/netllm/v1/local-providers" in paths


def test_dashboard_bootstrap_matches_the_registry() -> None:
    """The dashboard's degraded-mode roster must still list every provider.

    It now fetches GET /netllm/v1/local-providers, but the bootstrap is what
    renders before that returns or when the agent is unreachable -- so a
    provider missing here is simply absent from the discovery checkboxes,
    silently, exactly the class this program exists to make loud.
    """
    from conformance.projections import string_array_after

    found = string_array_after(
        "packages/netllm-agent/src/netllm_agent/static/dashboard.js",
        "const PROVIDERS_BOOTSTRAP",
    )
    found.assert_exactly(set(LOCAL_PROVIDERS))
