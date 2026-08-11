"""Cloud credential verification — UI-7a.

The bug: the Cloud page let a provider be switched on with no API key, then
presented it exactly like a working one. The user believes they have failover;
the first request local and peer backends cannot serve fails instead of falling
through. A silent non-functional safety net is worse than none.

The fix has four moving parts and each is asserted here rather than in a UI
test, because the UI is the one surface that does not decide anything:

  * the probe            — what "verified" actually means per provider
  * the record           — where the answer lives so it outlives a reload,
                           a restart, and a different process
  * the write-path gate  — the server-side rule, on every writer
  * the projection       — the specific blocker each surface prints
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from netllm_agent.admin import config_summary
from netllm_agent.app import create_app
from netllm_core.cloud_providers import get_provider_spec
from netllm_core.cloud_verification import (
    STATUS_INCONCLUSIVE,
    STATUS_NO_KEY,
    STATUS_OK,
    STATUS_UNAUTHORIZED,
    STATUS_UNREACHABLE,
    key_fingerprint,
    probe_cloud_provider,
    record_verification,
    verification_state,
)
from netllm_core.config_guards import apply_config_guards
from netllm_core.config_merge import apply_config_patch
from netllm_core.models import (
    CloudProviderConfig,
    NetllmConfig,
    load_config,
    save_config,
)

# A provider WITH a live catalogue endpoint and one WITHOUT, so both probe
# shapes are covered. Named through the registry rather than as bare literals
# so a rename is a lookup failure here, not a silently skipped test.
CATALOGUE_PROVIDER = "moonshot"
NO_CATALOGUE_PROVIDER = "zai"


def _spec(provider_id: str):  # noqa: ANN202
    spec = get_provider_spec(provider_id)
    assert spec is not None, f"{provider_id} left the registry; repoint this test"
    return spec


def _client(handler) -> httpx.AsyncClient:  # noqa: ANN001
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _verified(api_key: str, status: str = STATUS_OK) -> dict[str, str]:
    """The four fields a passing check writes, for a given key."""
    return {
        "verified_status": status,
        "verified_at": "2026-08-10T00:00:00+00:00",
        "verified_detail": "Key accepted — 3 model(s) listed.",
        "verified_key_fingerprint": key_fingerprint(api_key),
    }


# --- the probe ------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_provider_with_no_key_is_never_probed() -> None:
    """No key means no request: there is nothing to ask the provider."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"data": []})

    result = await probe_cloud_provider(
        CloudProviderConfig(),
        _spec(CATALOGUE_PROVIDER),
        env={},
        client=_client(handler),
    )
    assert result["status"] == STATUS_NO_KEY
    assert result["ok"] is False
    assert not calls, "a keyless provider was probed anyway"
    assert _spec(CATALOGUE_PROVIDER).api_key_env in result["blocker"]


@pytest.mark.asyncio
async def test_a_live_catalogue_verifies_the_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer mk-good"
        return httpx.Response(200, json={"data": [{"id": "kimi"}, {"id": "kimi-2"}]})

    result = await probe_cloud_provider(
        CloudProviderConfig(api_key="mk-good"),
        _spec(CATALOGUE_PROVIDER),
        env={},
        client=_client(handler),
    )
    assert result["status"] == STATUS_OK
    assert result["ok"] is True
    assert "2 model(s)" in result["detail"]
    assert result["key_fingerprint"] == key_fingerprint("mk-good")


@pytest.mark.asyncio
async def test_a_401_is_reported_as_a_rejected_key_not_a_generic_error() -> None:
    """`health.status_from_response` calls a 401 "online" — correctly, for a
    reachability probe. For a credential check it is the single most
    important outcome there is, so it gets its own status and its own
    sentence."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid api key"})

    result = await probe_cloud_provider(
        CloudProviderConfig(api_key="mk-revoked"),
        _spec(CATALOGUE_PROVIDER),
        env={},
        client=_client(handler),
    )
    assert result["status"] == STATUS_UNAUTHORIZED
    assert result["ok"] is False
    assert "rejected" in result["blocker"].lower()
    assert "401" in result["detail"]


@pytest.mark.asyncio
async def test_an_unreachable_provider_is_not_a_rejected_key() -> None:
    """The distinction the whole gate rests on: a refused connection says
    nothing about the credential, so it must not read as a bad key."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope", request=request)

    result = await probe_cloud_provider(
        CloudProviderConfig(api_key="mk-good"),
        _spec(CATALOGUE_PROVIDER),
        env={},
        client=_client(handler),
    )
    assert result["status"] == STATUS_UNREACHABLE
    assert "unreachable" in result["blocker"].lower()


@pytest.mark.asyncio
async def test_a_provider_without_a_catalogue_is_checked_with_one_token() -> None:
    """The alternative was calling a keyed-but-unchecked provider "verified",
    which is the exact claim this feature exists to stop making."""
    seen: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as json_mod

        seen.append((str(request.url), json_mod.loads(request.content)))
        return httpx.Response(200, json={"choices": []})

    spec = _spec(NO_CATALOGUE_PROVIDER)
    assert not spec.models_endpoint, f"{spec.id} grew a catalogue; repoint this test"
    result = await probe_cloud_provider(
        CloudProviderConfig(api_key="zk-good"), spec, env={}, client=_client(handler)
    )
    assert result["status"] == STATUS_OK
    assert len(seen) == 1
    url, body = seen[0]
    assert url.endswith("/chat/completions")
    assert body["max_tokens"] == 1, "a verification must not be able to cost real money"


@pytest.mark.asyncio
async def test_a_stale_model_id_is_inconclusive_not_a_failure() -> None:
    """A 400 from the one-token call is usually "that model is gone", which
    says nothing about the key. Refusing to enable on it would punish the
    user for a stale entry in *our* registry."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "model not found"})

    result = await probe_cloud_provider(
        CloudProviderConfig(api_key="zk-good"),
        _spec(NO_CATALOGUE_PROVIDER),
        env={},
        client=_client(handler),
    )
    assert result["status"] == STATUS_INCONCLUSIVE
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_an_unsaved_key_overrides_the_stored_one_and_is_not_returned() -> None:
    """The unsaved-key problem, at the layer that solves it."""
    presented: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        presented.append(request.headers.get("Authorization", ""))
        return httpx.Response(200, json={"data": [{"id": "kimi"}]})

    stored = CloudProviderConfig(api_key="mk-old")
    result = await probe_cloud_provider(
        stored,
        _spec(CATALOGUE_PROVIDER),
        api_key="mk-typed-but-unsaved",
        env={},
        client=_client(handler),
    )
    assert presented == ["Bearer mk-typed-but-unsaved"]
    assert stored.api_key == "mk-old", "the probe persisted a key it was only lent"
    assert "mk-typed-but-unsaved" not in str(result), "the probe echoed the key back"
    # The fingerprint is of the key that was CHECKED, which is what lets the
    # later save of that same key be recognised as verified.
    assert result["key_fingerprint"] == key_fingerprint("mk-typed-but-unsaved")


# --- the record and its projection ----------------------------------------


def test_a_replaced_key_invalidates_a_passing_record() -> None:
    spec = _spec(CATALOGUE_PROVIDER)
    provider = CloudProviderConfig(api_key="mk-good", **_verified("mk-good"))
    assert verification_state(provider, spec, env={})["ok"] is True

    provider.api_key = "mk-rotated"
    state = verification_state(provider, spec, env={})
    assert state["ok"] is False
    assert state["status"] == "key_changed"
    assert state["can_enable"] is False
    assert "changed" in state["blocker"]


def test_each_blocker_names_its_own_cause() -> None:
    """ "Not working" is not a diagnosis. Four states, four sentences, no two
    the same — this is the assertion behind "discrete and easy to use"."""
    spec = _spec(CATALOGUE_PROVIDER)
    cases = {
        "no key": CloudProviderConfig(),
        "never checked": CloudProviderConfig(api_key="mk-x"),
        "rejected": CloudProviderConfig(
            api_key="mk-x", **_verified("mk-x", STATUS_UNAUTHORIZED)
        ),
        "changed": CloudProviderConfig(api_key="mk-y", **_verified("mk-x")),
    }
    blockers = {
        name: verification_state(cfg, spec, env={})["blocker"]
        for name, cfg in cases.items()
    }
    assert all(blockers.values()), f"a state with no explanation: {blockers}"
    assert len(set(blockers.values())) == len(blockers), (
        f"two different problems produced the same sentence: {blockers}"
    )


def test_record_verification_writes_only_the_four_server_owned_fields() -> None:
    provider = CloudProviderConfig(enabled=True, api_key="mk-x", region="cn")
    record_verification(
        provider,
        {
            "status": STATUS_OK,
            "checked_at": "2026-08-10T00:00:00+00:00",
            "detail": "fine",
            "key_fingerprint": key_fingerprint("mk-x"),
        },
    )
    assert provider.verified_status == STATUS_OK
    assert provider.verified_key_fingerprint == key_fingerprint("mk-x")
    # Untouched: a check is not an edit.
    assert provider.enabled is True
    assert provider.region == "cn"
    assert provider.api_key == "mk-x"


def test_the_fingerprint_does_not_carry_the_key() -> None:
    key = "mk-super-secret-value"
    fingerprint = key_fingerprint(key)
    assert key not in fingerprint
    assert fingerprint == key_fingerprint(key), "not stable across calls"
    assert fingerprint != key_fingerprint(key + "x")
    assert key_fingerprint("") == "", "a keyless provider must not get a fingerprint"


# --- the write-path gate --------------------------------------------------


def _guard(cfg: NetllmConfig, previous: NetllmConfig | None = None) -> list[str]:
    warnings: list[str] = []
    apply_config_guards(cfg, previous=previous, warnings=warnings)
    return warnings


def test_a_keyless_provider_cannot_stay_enabled() -> None:
    """The reported bug, at the layer that decides."""
    cfg = NetllmConfig()
    cfg.cloud.providers[CATALOGUE_PROVIDER] = CloudProviderConfig(enabled=True)
    warnings = _guard(cfg)
    assert cfg.cloud.providers[CATALOGUE_PROVIDER].enabled is False
    assert any("no API key" in w or "No key set" in w for w in warnings), warnings


def test_a_newly_enabled_unverified_provider_is_refused() -> None:
    cfg = NetllmConfig()
    cfg.cloud.providers[CATALOGUE_PROVIDER] = CloudProviderConfig(
        enabled=True, api_key="mk-unchecked"
    )
    warnings = _guard(cfg, previous=NetllmConfig())
    assert cfg.cloud.providers[CATALOGUE_PROVIDER].enabled is False
    assert any("Never checked" in w for w in warnings), warnings


def test_a_verified_provider_enables_normally() -> None:
    cfg = NetllmConfig()
    cfg.cloud.providers[CATALOGUE_PROVIDER] = CloudProviderConfig(
        enabled=True, api_key="mk-good", **_verified("mk-good")
    )
    warnings = _guard(cfg, previous=NetllmConfig())
    assert cfg.cloud.providers[CATALOGUE_PROVIDER].enabled is True
    assert not warnings, warnings


def test_cloud_verification_skipped_when_cloud_not_in_patch() -> None:
    """Network-only dashboard saves omit ``cloud`` — must not re-warn."""
    cfg = NetllmConfig()
    cfg.cloud.providers[CATALOGUE_PROVIDER] = CloudProviderConfig(
        enabled=True, api_key="mk-unchecked"
    )
    previous = cfg.model_copy(deep=True)
    merged = apply_config_patch(cfg, {"swarm": {"mdns": False}})
    warnings: list[str] = []
    apply_config_guards(
        merged,
        previous=previous,
        warnings=warnings,
        patch={"swarm": {"mdns": False}},
    )
    assert merged.cloud.providers[CATALOGUE_PROVIDER].enabled is True
    assert not warnings, warnings


def test_a_provider_whose_key_was_rejected_cannot_be_newly_enabled() -> None:
    cfg = NetllmConfig()
    cfg.cloud.providers[CATALOGUE_PROVIDER] = CloudProviderConfig(
        enabled=True,
        api_key="mk-revoked",
        **_verified("mk-revoked", STATUS_UNAUTHORIZED),
    )
    warnings = _guard(cfg, previous=NetllmConfig())
    assert cfg.cloud.providers[CATALOGUE_PROVIDER].enabled is False
    assert any("rejected" in w.lower() for w in warnings), warnings


def test_an_already_enabled_provider_survives_the_upgrade() -> None:
    """Migration is the case that must not regress.

    A config written before this feature has `enabled = true`, a working key
    and no verification record anywhere. Switching it off because this
    release grew a field would break failover that has been serving requests
    for months — a worse bug than the one being fixed.
    """
    stored = NetllmConfig()
    stored.cloud.providers[CATALOGUE_PROVIDER] = CloudProviderConfig(
        enabled=True, api_key="mk-working"
    )
    merged = apply_config_patch(stored, {"cloud": {"fallback": "local"}})
    warnings = _guard(merged, previous=stored)
    assert merged.cloud.providers[CATALOGUE_PROVIDER].enabled is True
    assert any("left on" in w for w in warnings), warnings


def test_an_unreachable_check_does_not_disable_a_provider() -> None:
    """Ambiguity fails open: a flaky network must not be able to switch off
    someone's cloud failover."""
    cfg = NetllmConfig()
    cfg.cloud.providers[CATALOGUE_PROVIDER] = CloudProviderConfig(
        enabled=True, api_key="mk-good", **_verified("mk-good", STATUS_UNREACHABLE)
    )
    _guard(cfg, previous=NetllmConfig())
    assert cfg.cloud.providers[CATALOGUE_PROVIDER].enabled is True


def test_a_key_held_only_in_the_agents_environment_is_not_treated_as_missing() -> None:
    """`netllm config import` is the macOS Save button, and that app keeps
    keys in the login Keychain and injects them into the *agent*, not into
    the subprocess doing the save. A guard that read "I cannot see this key"
    as "there is no key" would switch off every provider a macOS user has."""
    cfg = NetllmConfig()
    cfg.cloud.providers[CATALOGUE_PROVIDER] = CloudProviderConfig(
        enabled=True,
        api_key_env="A_VAR_THIS_PROCESS_DOES_NOT_HAVE",
        **_verified(""),
    )
    _guard(cfg, previous=NetllmConfig())
    assert cfg.cloud.providers[CATALOGUE_PROVIDER].enabled is True


def test_a_client_cannot_forge_a_verification_record() -> None:
    """The gate reads config, so a patch that could write the record could
    certify its own credentials and walk straight through it."""
    cfg = NetllmConfig()
    cfg.cloud.providers[CATALOGUE_PROVIDER] = CloudProviderConfig(api_key="mk-x")
    merged = apply_config_patch(
        cfg,
        {
            "cloud": {
                "providers": {
                    CATALOGUE_PROVIDER: {
                        "enabled": True,
                        "verified_status": "ok",
                        "verified_key_fingerprint": key_fingerprint("mk-x"),
                        "verified_at": "2026-08-10T00:00:00+00:00",
                    }
                }
            }
        },
    )
    assert merged.cloud.providers[CATALOGUE_PROVIDER].verified_status == ""
    warnings = _guard(merged, previous=cfg)
    assert merged.cloud.providers[CATALOGUE_PROVIDER].enabled is False, (
        "a forged verification record enabled a provider"
    )
    assert warnings


# --- the HTTP surface -----------------------------------------------------


@pytest.fixture
def agent(tmp_path: Path):  # noqa: ANN201
    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    cfg_path = tmp_path / "config.toml"
    save_config(cfg, cfg_path)
    return cfg, cfg_path, create_app(cfg, config_path=cfg_path)


def test_the_config_summary_carries_the_blocker_for_every_provider() -> None:
    cfg = NetllmConfig()
    cfg.cloud.providers[CATALOGUE_PROVIDER] = CloudProviderConfig(enabled=True)
    providers = config_summary(cfg)["cloud"]["providers"]
    for provider_id, row in providers.items():
        verification = row["verification"]
        assert set(verification) >= {"status", "ok", "blocker", "can_enable"}, (
            provider_id
        )
        if not verification["ok"]:
            assert verification["blocker"], f"{provider_id} has no explanation"


def test_admin_config_refuses_to_enable_a_keyless_provider(agent) -> None:  # noqa: ANN001
    """Server-side, not UI-side: this is the same POST the dashboard sends,
    with the client-side gate bypassed entirely."""
    cfg, cfg_path, app = agent
    with TestClient(app) as client:
        resp = client.post(
            "/netllm/v1/admin/config",
            json={"cloud": {"providers": {CATALOGUE_PROVIDER: {"enabled": True}}}},
        )
        assert resp.status_code == 200, resp.text
        assert any("was not enabled" in w for w in resp.json().get("warnings", []))
        assert (
            load_config(cfg_path).cloud.providers[CATALOGUE_PROVIDER].enabled is False
        )


def test_verify_route_checks_an_unsaved_key_and_persists_only_the_outcome(
    agent,
    monkeypatch,  # noqa: ANN001
) -> None:
    cfg, cfg_path, app = agent
    seen: list[str] = []

    async def fake_probe(provider_cfg, spec, *, api_key=None, **kwargs):  # noqa: ANN001
        seen.append(api_key or "")
        return {
            "status": STATUS_OK,
            "ok": True,
            "checked_at": "2026-08-10T00:00:00+00:00",
            "detail": "Key accepted — 1 model(s) listed.",
            "blocker": "",
            "http_status": 200,
            "key_fingerprint": key_fingerprint(api_key or ""),
        }

    monkeypatch.setattr(
        "netllm_core.cloud_verification.probe_cloud_provider", fake_probe
    )
    with TestClient(app) as client:
        resp = client.post(
            f"/netllm/v1/cloud/providers/{CATALOGUE_PROVIDER}/verify",
            json={"api_key": "mk-typed"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["persisted"] is True
        assert "mk-typed" not in resp.text, "the verify route echoed the key back"
        assert "key_fingerprint" not in body

    assert seen == ["mk-typed"]
    stored = load_config(cfg_path).cloud.providers[CATALOGUE_PROVIDER]
    assert stored.verified_status == STATUS_OK
    assert stored.api_key == "", "an unsaved key was persisted by the check"
    assert stored.verified_key_fingerprint == key_fingerprint("mk-typed")

    # And that record is exactly what lets the very next save turn the
    # provider on — the verify-then-save flow, end to end.
    with TestClient(app) as client:
        resp = client.post(
            "/netllm/v1/admin/config",
            json={
                "cloud": {
                    "providers": {
                        CATALOGUE_PROVIDER: {"enabled": True, "api_key": "mk-typed"}
                    }
                }
            },
        )
        assert resp.status_code == 200, resp.text
        assert not resp.json().get("warnings")
    assert load_config(cfg_path).cloud.providers[CATALOGUE_PROVIDER].enabled is True


def test_verify_route_404s_on_an_unknown_provider(agent) -> None:  # noqa: ANN001
    _cfg, _cfg_path, app = agent
    with TestClient(app) as client:
        resp = client.post("/netllm/v1/cloud/providers/not-a-provider/verify", json={})
        assert resp.status_code == 404


def test_verification_survives_a_reload_because_the_agent_holds_it(
    agent,
    monkeypatch,  # noqa: ANN001
) -> None:
    """A page reload throws away everything the browser knew. The check is
    only useful if the answer outlives that, which is why it is written to
    the agent's config rather than kept in page state."""
    _cfg, cfg_path, app = agent

    async def fake_probe(provider_cfg, spec, *, api_key=None, **kwargs):  # noqa: ANN001
        return {
            "status": STATUS_OK,
            "ok": True,
            "checked_at": "2026-08-10T00:00:00+00:00",
            "detail": "ok",
            "blocker": "",
            "http_status": 200,
            "key_fingerprint": key_fingerprint("mk-saved"),
        }

    monkeypatch.setattr(
        "netllm_core.cloud_verification.probe_cloud_provider", fake_probe
    )
    with TestClient(app) as client:
        client.post(
            "/netllm/v1/admin/config",
            json={
                "cloud": {"providers": {CATALOGUE_PROVIDER: {"api_key": "mk-saved"}}}
            },
        )
        client.post(f"/netllm/v1/cloud/providers/{CATALOGUE_PROVIDER}/verify", json={})
        # A fresh GET is what a reloaded page issues.
        summary = client.get("/netllm/v1/config").json()
    verification = summary["cloud"]["providers"][CATALOGUE_PROVIDER]["verification"]
    assert verification["checked_at"] == "2026-08-10T00:00:00+00:00"
    # And on disk, so it outlives the process too.
    assert load_config(cfg_path).cloud.providers[CATALOGUE_PROVIDER].verified_at


def test_an_exported_config_arrives_disabled_on_a_machine_that_never_checked() -> None:
    """`cloud.providers[].enabled` is deliberately not portable.

    A config that could carry "enabled, and take my word for it that the key
    works" across machines would be a way to assert your own verification.
    The provider arrives with every other field intact and the switch off.
    """
    from netllm_cli.config_json import import_config

    source = NetllmConfig()
    source.cloud.providers[CATALOGUE_PROVIDER] = CloudProviderConfig(
        enabled=True, api_key="mk-good", region="cn", **_verified("mk-good")
    )
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "config.toml"
        save_config(NetllmConfig(), target)
        import_config(source.model_dump(mode="json"), target)
        landed = load_config(target).cloud.providers[CATALOGUE_PROVIDER]
    assert landed.enabled is False
    assert landed.region == "cn"
    assert landed.api_key == "mk-good"
    assert landed.verified_status == "", "a verification record crossed machines"
