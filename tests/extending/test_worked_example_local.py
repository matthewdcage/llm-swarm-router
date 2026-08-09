"""Axis B worked example: one `LocalProviderSpec`, end to end.

Discharges the central claim of `docs/extending/02-local-provider.md`:

    a fifth local provider costs one registry entry plus **two** declared
    hand-written companions, and reaches discovery URLs, config validation,
    the config schema document, the served projection, the CLI listing and
    the dashboard payload with no other source edit.

PROGRAM.md §8 wrote that claim without the companions ("zero source edits
beyond the registry entry"). Measured against this tree it is false, and the
two things it is false about are both *deliberate refusals* recorded in
PROGRAM.md itself -- §6.2 keeps `ProviderId` a hand-written `Literal`, and
§6.3 refuses to generate SwiftUI. Neither is a defect. Pretending they do not
exist would be, because the first contributor to add a provider would hit
both within a minute of trusting the guide.

See `_worked_example.py` for the three properties this file asserts and why
one of them is not enough.
"""

from __future__ import annotations

from typing import Literal, get_args

import pytest
from fastapi.testclient import TestClient
from netllm_core.local_providers import LOCAL_PROVIDERS, LocalProviderSpec

from extending._worked_example import (
    SWIFT_SETTINGS,
    CapabilityBranch,
    Companion,
    GeneratedBlock,
    Workspace,
    assert_classification_is_exhaustive,
    injected,
)

GUIDE = "docs/extending/02-local-provider.md"
FIXTURE_ID = "fixtureprov"
FIXTURE_LABEL = "FixtureLS"
FIXTURE_PORT = 59123

FIXTURE = LocalProviderSpec(
    id=FIXTURE_ID,
    display_name="Fixture Local Server",
    short_label=FIXTURE_LABEL,
    default_ports=(FIXTURE_PORT,),
    platforms=("darwin", "linux", "win32"),
    port_env="FIXTUREPROV_PORT",
    api_key_env="FIXTUREPROV_API_KEY",
    host_env="FIXTUREPROV_HOST",
    default_host_port=FIXTURE_PORT,
    offline_hint="run [cyan]fixture serve[/]",
)

#: The registry entry as a contributor would write it, for the temp tree the
#: generator runs against. Deliberately the same facts as FIXTURE above: the
#: two halves of this test (in-process and on-disk) must describe one entry.
FIXTURE_SOURCE = f'''    "{FIXTURE_ID}": LocalProviderSpec(
        id="{FIXTURE_ID}",
        display_name="Fixture Local Server",
        short_label="{FIXTURE_LABEL}",
        default_ports=({FIXTURE_PORT},),
        platforms=("darwin", "linux", "win32"),
        port_env="FIXTUREPROV_PORT",
        api_key_env="FIXTUREPROV_API_KEY",
        offline_hint="run fixture serve",
    ),
'''

#: Modules that materialize a constant from the registry at import time.
#: Reloading them stands in for a fresh process; see `injected`.
DERIVED_MODULES = ("netllm_discovery.local", "netllm_cli.ui")


# --- companion 1: the hand-written Literal --------------------------------


def _apply_provider_id(workspace: Workspace) -> None:
    """Widen `ProviderId` and rebuild the model that validates against it.

    In the repo this is a one-line edit to the `Literal` in `models.py`. Here
    it is done through `model_fields` + `model_rebuild` because pydantic
    compiled the old `Literal` into `Backend`'s core schema at class-creation
    time, which is exactly *why* the edit is load-bearing: without it,
    `Backend(provider="fixtureprov")` is a `ValidationError`, not a warning.
    """
    from netllm_core import models

    widened = Literal[tuple(get_args(models.ProviderId)) + (FIXTURE_ID,)]  # type: ignore[valid-type]
    models.ProviderId = widened
    models.Backend.model_fields["provider"].annotation = widened
    models.Backend.model_rebuild(force=True)


def _undo_provider_id(original: object) -> None:
    from netllm_core import models

    models.ProviderId = original
    models.Backend.model_fields["provider"].annotation = original
    models.Backend.model_rebuild(force=True)


# --- companion 2: the macOS offline prefill -------------------------------


def _apply_swift_bootstrap(workspace: Workspace) -> None:
    workspace.text_edits.append(
        (
            SWIFT_SETTINGS,
            "static let localProviderBootstrap",
            f'        (id: "{FIXTURE_ID}", label: "{FIXTURE_LABEL}", '
            f"port: {FIXTURE_PORT}),\n",
        )
    )


COMPANIONS: tuple[Companion, ...] = (
    Companion(
        name="ProviderId",
        path="packages/netllm-core/src/netllm_core/models.py",
        reason=(
            "a derived Literal blinds basedpyright, so PROGRAM.md §6.2 keeps it "
            "hand-written on purpose. pydantic compiles it into Backend's core "
            "schema, so the omission is a parse-time ValidationError, not a "
            "silent gap"
        ),
        enforcement="runtime",
        guard="tests/conformance/kit_local.py"
        "::test_provider_id_literal_matches_the_registry",
        apply=_apply_provider_id,
    ),
    Companion(
        name="SettingsViewModel.localProviderBootstrap",
        path=SWIFT_SETTINGS,
        reason=(
            "the macOS offline prefill carries a label and a scan port per "
            "provider, not just an id, and PROGRAM.md §6.3 refuses to generate "
            "SwiftUI. It is projection-tested against the registry instead. "
            "This is the copy that had already drifted: vLLM was prefilled on "
            "LM Studio's port"
        ),
        enforcement="projection",
        guard="tests/conformance/kit_local.py::test_swift_bootstrap_matches_the_registry",
        apply=_apply_swift_bootstrap,
    ),
)

GENERATED: tuple[GeneratedBlock, ...] = (
    GeneratedBlock(
        name="PROVIDERS_BOOTSTRAP",
        path="packages/netllm-agent/src/netllm_agent/static/dashboard.js",
    ),
    GeneratedBlock(name="discovery.providers", path="config.example.toml"),
)

CAPABILITY: tuple[CapabilityBranch, ...] = (
    CapabilityBranch(
        name='provider != "omlx"',
        path="packages/netllm-discovery/src/netllm_discovery/local.py",
        reason=(
            "oMLX exposes a proprietary admin/telemetry API no other local "
            "server has, so the literal marks a real capability rather than a "
            "roster copy. kit_cloud pins that it stays the only one"
        ),
    ),
)


# --- the worked example ---------------------------------------------------


@pytest.fixture
def workspace(tmp_path) -> Workspace:  # noqa: ANN001 - pytest tmp_path
    return Workspace(
        tmp_root=tmp_path,
        registry_edits={
            "packages/netllm-core/src/netllm_core/local_providers.py": (
                "LOCAL_PROVIDERS",
                FIXTURE_SOURCE,
            )
        },
    )


def _run(stage, workspace: Workspace, applied: set[str]) -> None:
    """Inject the fixture entry plus `applied` companions, then run `stage`."""
    from netllm_core import models

    original_provider_id = models.ProviderId
    try:
        for companion in COMPANIONS:
            if companion.name in applied and companion.apply is not None:
                companion.apply(workspace)
        with injected(LOCAL_PROVIDERS, FIXTURE_ID, FIXTURE, DERIVED_MODULES):
            stage(workspace)
    finally:
        _undo_provider_id(original_provider_id)


# --- stages ---------------------------------------------------------------


def stage_discovery_urls(_: Workspace) -> None:
    from netllm_core.models import NetllmConfig
    from netllm_discovery.local import candidate_urls_for_provider

    urls = candidate_urls_for_provider(FIXTURE_ID, NetllmConfig())
    for host in ("127.0.0.1", "localhost"):
        assert f"http://{host}:{FIXTURE_PORT}/v1" in urls, (
            f"discovery never probes {host}:{FIXTURE_PORT} for {FIXTURE_ID}"
        )
    from netllm_discovery.local import KNOWN_PROVIDERS

    assert FIXTURE_ID in {pid for pid, _, _ in KNOWN_PROVIDERS}


def stage_config_validation(_: Workspace) -> None:
    """The stage `ProviderId` gates. Without the companion this is a
    ValidationError naming the Literal, which is the whole point of listing
    it as a companion rather than pretending it is not there."""
    from netllm_core.config_merge import apply_config_patch
    from netllm_core.models import Backend, NetllmConfig

    config = NetllmConfig()
    config.discovery.providers = [*config.discovery.providers, FIXTURE_ID]
    merged = apply_config_patch(config, {"agent": {"port": 11400}})
    assert FIXTURE_ID in merged.discovery.providers, (
        "a config round-trip dropped the provider from discovery.providers"
    )
    backend = Backend(
        id=FIXTURE_ID,
        base_url=f"http://127.0.0.1:{FIXTURE_PORT}/v1",
        provider=FIXTURE_ID,
    )
    assert backend.provider == FIXTURE_ID


def stage_schema_document(_: Workspace) -> None:
    """`discovery.providers`' default is `default_discovery_providers()`, so
    the schema the dashboard and macOS form are built from picks the new
    provider up with no edit -- including its platform gating."""
    from netllm_core.config_schema import config_schema_document

    document = config_schema_document()
    discovery = document["sections"]["discovery"]
    providers = next(f for f in discovery["fields"] if f["name"] == "providers")
    assert FIXTURE_ID in providers["default"], (
        f"config schema document offers {providers['default']}, without "
        f"{FIXTURE_ID}: the form would not let an operator enable it"
    )


def stage_projection_endpoint(_: Workspace) -> None:
    from netllm_agent.app import create_app
    from netllm_core.models import NetllmConfig

    config = NetllmConfig()
    config.swarm.mdns = False
    config.agent.advertise = False
    with TestClient(create_app(config)) as client:
        payload = client.get("/netllm/v1/local-providers").json()
    rows = {row["id"]: row for row in payload["providers"]}
    assert FIXTURE_ID in rows, "the agent does not serve the new provider"
    row = rows[FIXTURE_ID]
    assert row["display_name"] == FIXTURE.display_name
    assert row["default_ports"] == [FIXTURE_PORT]
    assert row["platforms"] == list(FIXTURE.platforms)
    assert row["api_key_env"] == FIXTURE.api_key_env


def stage_cli_listing(_: Workspace) -> None:
    from netllm_cli.ui import (
        _PROVIDER_LABELS,
        default_provider_port_hint,
        offline_provider_hints,
    )

    assert _PROVIDER_LABELS.get(FIXTURE_ID) == FIXTURE_LABEL
    hint = default_provider_port_hint()
    assert FIXTURE_LABEL in hint and f":{FIXTURE_PORT}" in hint, hint
    hints = offline_provider_hints([{"id": FIXTURE_ID, "status": "offline"}])
    assert hints and "fixture serve" in hints[0], hints


def stage_dashboard_payload(workspace: Workspace) -> None:
    """Two surfaces, two mechanisms, both proven on real files.

    The dashboard's degraded-mode roster and `config.example.toml` are
    *generated*: the fixture reaches them by running the checked-in
    generator, unmodified, against a tree whose registry has one more entry.
    The macOS prefill is *hand-written* and reaches them only because
    companion 2 made the edit.
    """
    dashboard = workspace.read(
        "packages/netllm-agent/src/netllm_agent/static/dashboard.js"
    )
    assert f'"{FIXTURE_ID}"' in dashboard.split("PROVIDERS_BOOTSTRAP")[1][:600], (
        "generate-registry-artifacts.py did not carry the new provider into "
        "the dashboard bootstrap"
    )
    example = workspace.read("config.example.toml")
    assert f'"{FIXTURE_ID}"' in example
    swift = workspace.read(SWIFT_SETTINGS)
    marker = swift.find("static let localProviderBootstrap")
    block = swift[marker : swift.find("\n    ]", marker)]
    assert f'id: "{FIXTURE_ID}"' in block, (
        "the macOS offline prefill does not list the new provider"
    )
    assert f'label: "{FIXTURE_LABEL}"' in block
    assert f"port: {FIXTURE_PORT}" in block


STAGES = {
    "discovery-urls": stage_discovery_urls,
    "config-validation": stage_config_validation,
    "schema-document": stage_schema_document,
    "projection-endpoint": stage_projection_endpoint,
    "cli-listing": stage_cli_listing,
    "dashboard-payload": stage_dashboard_payload,
}

ALL_COMPANIONS = {companion.name for companion in COMPANIONS}


# --- property 1: sufficiency ----------------------------------------------


@pytest.mark.parametrize("stage_name", list(STAGES), ids=list(STAGES))
def test_registry_entry_plus_declared_companions_flows_end_to_end(
    stage_name: str, workspace: Workspace
) -> None:
    """One registry entry, two declared companions, nothing else.

    If a *sixth* hand-edit ever becomes necessary, the stage that needs it
    fails here by name -- which is the guarantee worth having, and the reason
    this test is not just a happy-path smoke test.
    """
    _run(STAGES[stage_name], workspace, ALL_COMPANIONS)


# --- property 2: necessity ------------------------------------------------


@pytest.mark.parametrize(
    "companion", COMPANIONS, ids=[companion.name for companion in COMPANIONS]
)
def test_every_declared_companion_is_still_necessary(
    companion: Companion, workspace: Workspace
) -> None:
    """Omitting a companion must break at least one stage.

    A companion that no longer breaks anything is an over-claim in the other
    direction: the guide would be telling contributors to make an edit the
    machinery now derives. Enforcement kinds differ, so the failure differs
    too -- `runtime` raises `ValidationError`, `projection` raises
    `AssertionError` -- and both count.
    """
    without = ALL_COMPANIONS - {companion.name}
    failures: list[str] = []
    for name, stage in STAGES.items():
        try:
            _run(
                stage,
                Workspace(
                    tmp_root=workspace.tmp_root / name,
                    registry_edits=workspace.registry_edits,
                ),
                without,
            )
        except Exception as error:  # noqa: BLE001 - the failure IS the assertion
            failures.append(f"{name}: {type(error).__name__}")
    assert failures, (
        f"companion {companion.name!r} ({companion.path}) is declared as a "
        "required hand-edit, but every stage passes without it. Either the "
        "machinery now derives it -- delete it from COMPANIONS and from "
        f"{GUIDE} -- or a stage that depends on it is missing from STAGES."
    )


def test_static_only_companions_are_declared_as_such() -> None:
    """A companion nothing at runtime enforces must say so.

    `ProviderId` is caught by pydantic; `CloudProviderId` (the cloud axis's
    twin) is caught by nothing but basedpyright and a `get_args` assertion.
    Recording the enforcement kind per companion is what stops the guides
    claiming the same strength for both.
    """
    for companion in COMPANIONS:
        assert companion.enforcement in {"runtime", "projection", "static-only"}
        assert companion.guard, f"{companion.name} names no guard"
        assert companion.reason, f"{companion.name} has no stated reason"


# --- property 3: classification -------------------------------------------


def test_the_companion_list_is_exhaustive() -> None:
    assert_classification_is_exhaustive(
        "local-provider-id", COMPANIONS, GENERATED, CAPABILITY, GUIDE
    )
