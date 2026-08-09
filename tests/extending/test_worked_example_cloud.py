"""Axis A worked example: one `CloudProviderSpec`, end to end.

Discharges the central claim of `docs/extending/01-cloud-provider.md`:

    a seventh cloud provider costs one registry entry plus **three** declared
    hand-written companions, and reaches endpoint resolution, backend
    materialization, config validation, the served projection, the CLI
    listing and the dashboard payload with no other source edit.

`08946b6` (DashScope) touched 13 files before the registry work; the axis is
measured again here, honestly, including the part PROGRAM.md §8 omitted.

One companion on this axis is weaker than its local twin and the guide has to
say so. `ProviderId` is compiled into `Backend`'s pydantic schema, so omitting
it raises. `CloudProviderId` is only ever an *annotation* -- `CloudProviderSpec`
is a plain frozen dataclass and `CloudConfig.providers` is `dict[str, ...]` --
so omitting it changes nothing at runtime and no test would notice, which is
why `test_cloud_provider_id_literal_matches_the_registry` exists in
`kit_cloud` and why this file records its enforcement as `static-only`.
"""

from __future__ import annotations

from typing import Literal, get_args

import pytest
from netllm_core.cloud_providers import (
    CLOUD_PROVIDERS,
    CloudEndpoint,
    CloudProviderSpec,
)

from extending._worked_example import (
    SWIFT_SETTINGS,
    CapabilityBranch,
    Companion,
    GeneratedBlock,
    Workspace,
    assert_classification_is_exhaustive,
    injected,
)

GUIDE = "docs/extending/01-cloud-provider.md"
FIXTURE_ID = "fixturecloud"
FIXTURE_NAME = "Fixture Cloud"
FIXTURE_BASE_URL = "https://fixture.invalid/v1"

FIXTURE = CloudProviderSpec(
    id=FIXTURE_ID,  # type: ignore[arg-type]  # the companion this test measures
    display_name=FIXTURE_NAME,
    endpoints={"global": CloudEndpoint(openai_base_url=FIXTURE_BASE_URL)},
    auth_modes=("api_key",),
    api_key_env="FIXTURECLOUD_API_KEY",
    default_api_format="openai",
    models_endpoint=False,
    static_models=("fixture-1",),
    notes="Fixture provider used by the Phase 8 worked example.",
)

FIXTURE_SOURCE = f'''    "{FIXTURE_ID}": CloudProviderSpec(
        id="{FIXTURE_ID}",
        display_name="{FIXTURE_NAME}",
        endpoints={{
            "global": CloudEndpoint(openai_base_url="{FIXTURE_BASE_URL}"),
        }},
        auth_modes=("api_key",),
        api_key_env="FIXTURECLOUD_API_KEY",
        default_api_format="openai",
        models_endpoint=False,
        static_models=("fixture-1",),
    ),
'''

#: Nothing on this axis materializes a constant from the registry at import
#: time -- every consumer looks the spec up per call. The empty tuple is a
#: finding, not an omission: it is why Axis A scored better than Axis B.
DERIVED_MODULES: tuple[str, ...] = ()


# --- companion 1: the hand-written Literal (static-only) ------------------


def _apply_cloud_provider_id(workspace: Workspace) -> None:
    from netllm_core import cloud_providers

    cloud_providers.CloudProviderId = Literal[  # type: ignore[misc]
        tuple(get_args(cloud_providers.CloudProviderId)) + (FIXTURE_ID,)
    ]


def _undo_cloud_provider_id(original: object) -> None:
    from netllm_core import cloud_providers

    cloud_providers.CloudProviderId = original


# --- companion 2: the macOS offline roster --------------------------------


def _apply_swift_bootstrap(workspace: Workspace) -> None:
    workspace.text_edits.append(
        (
            SWIFT_SETTINGS,
            "static let cloudProvidersBootstrap",
            "        CloudProviderInfo(\n"
            f'            id: "{FIXTURE_ID}",\n'
            f'            displayName: "{FIXTURE_NAME}",\n'
            '            notes: "Fixture provider.",\n'
            '            regions: ["global"],\n'
            "            keychainAccount: "
            f'KeychainStore.accountForCloudProvider("{FIXTURE_ID}")\n'
            "        ),\n",
        )
    )


COMPANIONS: tuple[Companion, ...] = (
    Companion(
        name="CloudProviderId",
        path="packages/netllm-core/src/netllm_core/cloud_providers.py",
        reason=(
            "a derived Literal blinds basedpyright, so PROGRAM.md §6.2 keeps it "
            "hand-written. Unlike ProviderId it is only an annotation -- nothing "
            "validates against it at runtime -- so its omission is invisible "
            "except to the type checker and to the kit's get_args assertion"
        ),
        enforcement="static-only",
        guard="tests/conformance/kit_cloud.py"
        "::test_cloud_provider_id_literal_matches_the_registry",
        apply=_apply_cloud_provider_id,
    ),
    Companion(
        name="SettingsViewModel.cloudProvidersBootstrap",
        path=SWIFT_SETTINGS,
        reason=(
            "the macOS offline roster carries display name, notes, regions and "
            "auth modes, not just ids. PROGRAM.md §6.3 refuses to generate "
            "SwiftUI and generating prose would be worse than checking it, so "
            "it is projection-tested against the registry instead"
        ),
        enforcement="projection",
        guard="tests/conformance/kit_cloud.py::test_settings_bootstrap_covers_every_provider",
        apply=_apply_swift_bootstrap,
    ),
    Companion(
        name="[cloud.providers.<id>] example stanza",
        path="config.example.toml",
        reason=(
            "the stanza carries per-provider commentary (which auth modes are "
            "real, which regions exist, which model ids are sunsetting) that is "
            "documentation rather than a roster copy, so it is hand-written. "
            "Only its *presence* is checked, never its prose"
        ),
        enforcement="projection",
        guard="tests/conformance/kit_cloud.py::test_config_example_documents_every_provider",
        # Applied by `_stanza_edit` rather than through `text_edits`: the
        # stanzas are a commented prose block, not an array literal, so there
        # is no terminator to insert before.
        apply=None,
    ),
)

GENERATED: tuple[GeneratedBlock, ...] = (
    GeneratedBlock(
        name="CLOUD_PROVIDER_IDS_BOOTSTRAP",
        path="packages/netllm-agent/src/netllm_agent/static/dashboard.js",
    ),
    GeneratedBlock(
        name="bootstrapProviderIDs",
        path="apps/netllm-mac/Sources/Config/KeychainStore.swift",
    ),
)

NON_ROSTER: tuple[CapabilityBranch, ...] = (
    CapabilityBranch(
        name="ApiFormat",
        path="packages/netllm-core/src/netllm_core/models.py",
        reason=(
            'the `Literal["openai", "anthropic"]` api-format type collides with '
            "two provider ids by coincidence of naming; it is a wire-format "
            "vocabulary, not a roster copy, and adding a provider never touches it"
        ),
    ),
)


# --- workspace ------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path) -> Workspace:  # noqa: ANN001 - pytest tmp_path
    return Workspace(
        tmp_root=tmp_path,
        registry_edits={
            "packages/netllm-core/src/netllm_core/cloud_providers.py": (
                "CLOUD_PROVIDERS",
                FIXTURE_SOURCE,
            )
        },
    )


def _stanza_edit(workspace: Workspace) -> None:
    """Append the hand-written example stanza to the temp config.example.toml.

    Done as a whole-file append rather than through `text_edits` because the
    stanzas are a commented prose block, not an array literal.
    """
    path = workspace.tree() / "config.example.toml"
    path.write_text(
        path.read_text(encoding="utf-8")
        + f"\n# [cloud.providers.{FIXTURE_ID}]\n# enabled = true\n",
        encoding="utf-8",
    )


def _run(stage, workspace: Workspace, applied: set[str]) -> None:
    from netllm_core import cloud_providers

    original = cloud_providers.CloudProviderId
    try:
        for companion in COMPANIONS:
            if companion.name in applied and companion.apply is not None:
                companion.apply(workspace)
        with injected(CLOUD_PROVIDERS, FIXTURE_ID, FIXTURE, DERIVED_MODULES):
            if "[cloud.providers.<id>] example stanza" in applied:
                _stanza_edit(workspace)
            stage(workspace)
    finally:
        _undo_cloud_provider_id(original)


# --- stages ---------------------------------------------------------------


def stage_endpoint_resolution(_: Workspace) -> None:
    """The cloud twin of "discovery URLs": which base URL a request goes to."""
    from netllm_core.cloud_providers import get_provider_spec

    spec = get_provider_spec(FIXTURE_ID)
    assert spec is not None, "the registry lookup does not see the new provider"
    assert spec.default_region() == "global"
    assert spec.endpoint().openai_base_url == FIXTURE_BASE_URL
    # An unknown region degrades to the default rather than raising -- the
    # same forward-compatibility contract Phase 2 gave unknown config keys.
    assert spec.endpoint("no-such-region").openai_base_url == FIXTURE_BASE_URL
    assert spec.resolved_keychain_account() == f"{FIXTURE_ID}_api_key"


def stage_config_validation(_: Workspace) -> None:
    """Config round-trip, plus the static-only Literal.

    The `get_args` assertion is here rather than in a stage of its own
    because it is the *only* thing that notices companion 1 is missing:
    `CloudProviderSpec` is a frozen dataclass and `CloudConfig.providers` is
    keyed by `str`, so pydantic never sees the Literal on this axis.
    """
    from netllm_core.cloud_providers import CloudProviderId
    from netllm_core.config_merge import apply_config_patch
    from netllm_core.models import (
        CloudProviderConfig,
        NetllmConfig,
        unknown_cloud_provider_ids,
    )

    assert FIXTURE_ID in get_args(CloudProviderId), (
        f"CloudProviderId does not carry {FIXTURE_ID!r}; nothing fails at "
        "runtime, but basedpyright and editor completion are now wrong about "
        "every provider id in the tree"
    )
    config = NetllmConfig()
    config.cloud.enabled = True
    config.cloud.providers = {
        FIXTURE_ID: CloudProviderConfig(enabled=True, region="global")
    }
    assert unknown_cloud_provider_ids(config) == [], (
        "doctor reports the new provider as an unknown config key"
    )
    merged = apply_config_patch(config, {"cloud": {"fallback": "cloud"}})
    assert FIXTURE_ID in merged.cloud.providers
    assert merged.cloud.providers[FIXTURE_ID].enabled is True


def stage_backend_materialization(_: Workspace) -> None:
    """The registry entry becomes a routable pool row with no new code."""
    import os

    from netllm_agent.service import AgentService
    from netllm_core.models import CloudProviderConfig, NetllmConfig

    config = NetllmConfig()
    config.swarm.mdns = False
    config.agent.advertise = False
    config.cloud.enabled = True
    config.cloud.providers = {FIXTURE_ID: CloudProviderConfig(enabled=True)}
    previous = os.environ.get("FIXTURECLOUD_API_KEY")
    os.environ["FIXTURECLOUD_API_KEY"] = "sentinel-key"
    try:
        service = AgentService(config)
        service._materialize_cloud_provider_backends()
        row = service.pool.backend_by_id(f"cloud-{FIXTURE_ID}")
    finally:
        if previous is None:
            os.environ.pop("FIXTURECLOUD_API_KEY", None)
        else:
            os.environ["FIXTURECLOUD_API_KEY"] = previous
    assert row is not None, "no routable backend row was materialized"
    assert row.base_url == FIXTURE_BASE_URL
    assert row.cloud_provider == FIXTURE_ID
    assert row.resolve_api_key() == "sentinel-key"


def stage_projection_endpoint(_: Workspace) -> None:
    from netllm_agent.admin import cloud_provider_registry_payload

    rows = {row["id"]: row for row in cloud_provider_registry_payload()}
    assert FIXTURE_ID in rows, "the agent does not serve the new provider"
    assert rows[FIXTURE_ID]["display_name"] == FIXTURE_NAME
    assert rows[FIXTURE_ID]["regions"] == ["global"]
    assert rows[FIXTURE_ID]["api_key_env"] == "FIXTURECLOUD_API_KEY"


def stage_cli_listing(_: Workspace) -> None:
    from netllm_cli.main import app
    from typer.testing import CliRunner

    result = CliRunner().invoke(app, ["cloud", "list"])
    assert result.exit_code == 0, result.output
    assert FIXTURE_NAME in result.output, result.output


def stage_dashboard_payload(workspace: Workspace) -> None:
    dashboard = workspace.read(
        "packages/netllm-agent/src/netllm_agent/static/dashboard.js"
    )
    block = dashboard.split("CLOUD_PROVIDER_IDS_BOOTSTRAP")[1][:600]
    assert f'"{FIXTURE_ID}"' in block, (
        "generate-registry-artifacts.py did not carry the new provider into "
        "the dashboard bootstrap"
    )
    keychain = workspace.read("apps/netllm-mac/Sources/Config/KeychainStore.swift")
    assert f'"{FIXTURE_ID}"' in keychain
    swift = workspace.read(SWIFT_SETTINGS)
    marker = swift.find("static let cloudProvidersBootstrap")
    settings_block = swift[marker : swift.find("\n    ]", marker)]
    assert f'id: "{FIXTURE_ID}"' in settings_block, (
        "the macOS offline roster does not list the new provider"
    )
    example = workspace.read("config.example.toml")
    assert f"[cloud.providers.{FIXTURE_ID}]" in example, (
        "config.example.toml documents no stanza for the new provider"
    )


STAGES = {
    "endpoint-resolution": stage_endpoint_resolution,
    "config-validation": stage_config_validation,
    "backend-materialization": stage_backend_materialization,
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
    _run(STAGES[stage_name], workspace, ALL_COMPANIONS)


# --- property 2: necessity ------------------------------------------------


@pytest.mark.parametrize(
    "companion", COMPANIONS, ids=[companion.name for companion in COMPANIONS]
)
def test_every_declared_companion_is_still_necessary(
    companion: Companion, workspace: Workspace
) -> None:
    without = ALL_COMPANIONS - {companion.name}
    failures: list[str] = []
    for name, stage in STAGES.items():
        scratch = Workspace(
            tmp_root=workspace.tmp_root / name,
            registry_edits=workspace.registry_edits,
        )
        try:
            _run(stage, scratch, without)
        except Exception as error:  # noqa: BLE001 - the failure IS the assertion
            failures.append(f"{name}: {type(error).__name__}")
    assert failures, (
        f"companion {companion.name!r} ({companion.path}) is declared as a "
        "required hand-edit, but every stage passes without it. Either the "
        "machinery now derives it -- delete it from COMPANIONS and from "
        f"{GUIDE} -- or a stage that depends on it is missing from STAGES."
    )


def test_each_companion_declares_a_reason_and_a_guard() -> None:
    for companion in COMPANIONS:
        assert companion.enforcement in {"runtime", "projection", "static-only"}
        assert companion.guard, f"{companion.name} names no guard"
        assert companion.reason, f"{companion.name} has no stated reason"


# --- property 3: classification -------------------------------------------


def test_the_companion_list_is_exhaustive() -> None:
    assert_classification_is_exhaustive(
        "cloud-provider-id", COMPANIONS, GENERATED, NON_ROSTER, GUIDE
    )
