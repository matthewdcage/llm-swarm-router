"""Axis A conformance kit: the contract every cloud provider must satisfy.

Parameterized over `CLOUD_PROVIDERS`, so a new provider acquires this suite
with no test-file edits — the same deal `kit_local` gives Axis B.

Axis A was already the better-behaved axis: `cloud_provider_registry_payload`
is a real runtime projection, and the Swift app fetches it rather than
hardcoding. What it lacked was a *contract* — three roster tests tripped, but
nothing asserted that a spec was internally coherent, that its declared
default region actually existed, or that the Keychain and env-var conventions
held. `08946b6` (DashScope) touched 13 files; the kit is what makes the next
one loud where it is incomplete rather than silent.
"""

from __future__ import annotations

import pytest
from netllm_core.cloud_providers import CLOUD_PROVIDERS, CloudProviderSpec

from conformance.projections import REPO_ROOT, string_array_after

SPECS = list(CLOUD_PROVIDERS.values())
IDS = [spec.id for spec in SPECS]


@pytest.fixture(params=SPECS, ids=IDS)
def spec(request: pytest.FixtureRequest) -> CloudProviderSpec:
    return request.param


# --- spec coherence -------------------------------------------------------


def test_spec_is_well_formed(spec: CloudProviderSpec) -> None:
    assert spec.id and spec.id.islower()
    assert spec.display_name
    assert spec.endpoints, "a provider with no endpoint cannot be routed to"
    assert spec.auth_modes
    assert spec.api_key_env
    assert spec.default_api_format in ("openai", "anthropic")


def test_registry_key_matches_spec_id() -> None:
    for key, value in CLOUD_PROVIDERS.items():
        assert key == value.id, f"registry key {key!r} != spec id {value.id!r}"


def test_default_region_resolves_to_a_real_endpoint(spec: CloudProviderSpec) -> None:
    """`default_region()` returns the first endpoint key; if that key were
    ever removed while a stale default lingered, every unqualified request to
    the provider would resolve to the wrong base URL."""
    assert spec.default_region() in spec.endpoints
    assert spec.endpoint(spec.default_region()) is spec.endpoints[spec.default_region()]


def test_an_unknown_region_falls_back_rather_than_raising(
    spec: CloudProviderSpec,
) -> None:
    """A config naming a region this build does not know must degrade to the
    default, not KeyError — Phase 2 made unknown config keys survivable, and
    this is the same contract one level down."""
    assert spec.endpoint("no-such-region") is spec.endpoints[spec.default_region()]


def test_static_models_exist_when_there_is_no_live_catalog(
    spec: CloudProviderSpec,
) -> None:
    """The Z.ai invariant. A provider with `models_endpoint=False` cannot be
    probed for its catalog, so without `static_models` it materializes zero
    models and is silently unusable."""
    if spec.models_endpoint:
        pytest.skip(f"{spec.id} serves a live /models catalog")
    assert spec.static_models, (
        f"{spec.id} has no /models endpoint and no static_models, so it can "
        "never offer a model"
    )


# --- conventions the clients rely on --------------------------------------


def test_api_key_env_follows_the_derivable_convention(spec: CloudProviderSpec) -> None:
    """Swift derives this as `id.uppercased()_API_KEY` when the registry has
    not been fetched yet (KeychainStore.CloudKeyEnv.defaultEnvVar). A spec
    that deviates would be injected under the wrong variable in degraded
    mode, so the deviation has to be deliberate and visible here."""
    assert spec.api_key_env == f"{spec.id.upper()}_API_KEY", (
        f"{spec.id} deviates from the derivable env-var convention; the Swift "
        "degraded-mode fallback computes the derived name and would inject "
        "the key under the wrong variable"
    )


def test_keychain_account_follows_the_convention(spec: CloudProviderSpec) -> None:
    """KeychainStore.accountForCloudProvider is now a one-liner returning
    `<id>_api_key`; its six per-provider switch cases were deleted in Phase 4
    because every one returned exactly that. This pins the convention so the
    deletion stays safe."""
    assert spec.resolved_keychain_account() == f"{spec.id}_api_key"


def test_registry_payload_carries_every_spec(spec: CloudProviderSpec) -> None:
    """The runtime projection the macOS app and dashboard both consume."""
    from netllm_agent.admin import cloud_provider_registry_payload

    rows = {row["id"]: row for row in cloud_provider_registry_payload()}
    assert spec.id in rows, f"{spec.id} is absent from the served registry"
    row = rows[spec.id]
    assert row["display_name"] == spec.display_name
    assert row["regions"] == list(spec.endpoints)
    assert row["api_key_env"] == spec.api_key_env
    assert row["default_api_format"] == spec.default_api_format


def test_config_round_trip_preserves_the_provider(spec: CloudProviderSpec) -> None:
    """load -> save -> load must not drop a `[cloud.providers.<id>]` subtree.

    This is `models.py`'s old filtering validator as a test: it dropped any
    provider id the running build did not know, which deleted a newer
    release's provider from an older agent's config on the next save.
    """
    from netllm_core.config_merge import apply_config_patch
    from netllm_core.models import CloudProviderConfig, NetllmConfig

    cfg = NetllmConfig()
    cfg.cloud.providers = {
        spec.id: CloudProviderConfig(enabled=True, region=spec.default_region())
    }
    merged = apply_config_patch(cfg, {"cloud": {"enabled": True}})
    assert spec.id in merged.cloud.providers, (
        f"{spec.id} was dropped by a config round-trip"
    )
    assert merged.cloud.providers[spec.id].enabled is True
    assert merged.cloud.providers[spec.id].region == spec.default_region()


# --- client projections ---------------------------------------------------


def test_swift_bootstrap_covers_every_provider() -> None:
    """The macOS degraded-mode roster: what Settings can render before it has
    ever reached an agent. A provider missing here can have its key typed and
    stored but never injected — it 401s against a credential the UI shows as
    saved."""
    found = string_array_after(
        "apps/netllm-mac/Sources/Config/KeychainStore.swift",
        "static let bootstrapProviderIDs",
    )
    found.assert_exactly(set(CLOUD_PROVIDERS))


def test_dashboard_bootstrap_covers_every_provider() -> None:
    found = string_array_after(
        "packages/netllm-agent/src/netllm_agent/static/dashboard.js",
        "CLOUD_PROVIDER_IDS_BOOTSTRAP",
    )
    found.assert_exactly(set(CLOUD_PROVIDERS))


def test_no_literal_api_key_table_survives_in_pythonruntime() -> None:
    """`PythonRuntime.injectCloudAPIKeys` held a closed `(account, env)` table
    — the one hardcode in the tree that failed silently, because a provider
    added everywhere else still stored a key that was never exported. It is
    derived now; assert the literal table cannot come back."""
    text = (REPO_ROOT / "apps/netllm-mac/Sources/Server/PythonRuntime.swift").read_text(
        encoding="utf-8"
    )
    offenders = [
        line.strip()
        for line in text.splitlines()
        if "_API_KEY" in line and not line.strip().startswith("//")
    ]
    assert not offenders, (
        "PythonRuntime.swift names an API-key env var literally again; the "
        f"injection list must stay derived from the registry: {offenders}"
    )
