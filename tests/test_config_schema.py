"""config_schema.py must stay in sync with NetllmConfig's actual fields.

This is the regression test docs/config-schema-rewrite-plan.md §5 phase 1
calls for: a model field added without a matching schema entry should
fail loudly here instead of silently being uneditable from a
schema-driven form (the failure mode this whole rewrite exists to close).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
from netllm_agent.app import create_app
from netllm_core.config_schema import (
    BOOTSTRAP_SECTIONS,
    SECTIONS,
    config_schema_document,
)
from netllm_core.models import NetllmConfig, save_config


def test_sections_match_netllm_config_fields():
    assert set(SECTIONS) == set(NetllmConfig.model_fields)


def test_every_pydantic_field_has_a_schema_entry():
    for section_key, model in SECTIONS.items():
        doc_fields = {f["name"] for f in config_schema_document()["sections"][section_key]["fields"]}
        assert doc_fields == set(model.model_fields), (
            f"section {section_key!r}: schema fields {doc_fields} != "
            f"model fields {set(model.model_fields)}"
        )


def test_secrets_are_write_only():
    doc = config_schema_document()
    swarm_fields = {f["name"]: f for f in doc["sections"]["swarm"]["fields"]}
    assert swarm_fields["cluster_token"]["write_only"] is True
    assert swarm_fields["cluster_token"]["default"] == ""

    cloud_fields = {f["name"]: f for f in doc["sections"]["cloud"]["fields"]}
    provider_item_schema = {
        f["name"]: f for f in cloud_fields["providers"]["item_schema"]
    }
    assert provider_item_schema["api_key"]["write_only"] is True

    routing_fields = {f["name"]: f for f in doc["sections"]["routing"]["fields"]}
    backend_item_schema = {
        f["name"]: f for f in routing_fields["backends"]["item_schema"]
    }
    assert backend_item_schema["api_key"]["write_only"] is True


def test_server_computed_fields_are_read_only_with_no_baked_in_default():
    doc = config_schema_document()
    agent_fields = {f["name"]: f for f in doc["sections"]["agent"]["fields"]}
    assert agent_fields["agent_id"]["read_only"] is True
    assert agent_fields["agent_id"]["default"] is None
    assert agent_fields["hostname"]["read_only"] is True

    routing_fields = {f["name"]: f for f in doc["sections"]["routing"]["fields"]}
    assert routing_fields["lan_defaults_applied"]["read_only"] is True

    cloud_fields = {f["name"]: f for f in doc["sections"]["cloud"]["fields"]}
    assert cloud_fields["cloud_defaults_applied"]["read_only"] is True


def test_document_is_deterministic_across_calls():
    # Required for the version/ETag caching contract (plan §3.2) — a
    # schema document that differs run to run (e.g. from a default_factory
    # like agent_id's uuid4) would defeat client-side caching.
    assert config_schema_document() == config_schema_document()


def test_bootstrap_sections_are_a_subset_of_sections():
    assert set(BOOTSTRAP_SECTIONS) <= set(SECTIONS)


def test_list_of_object_fields_carry_item_schema():
    doc = config_schema_document()
    routing_fields = {f["name"]: f for f in doc["sections"]["routing"]["fields"]}
    assert routing_fields["policies"]["widget"] == "list"
    assert routing_fields["policies"]["default_factory"] == "local_openai_policy"
    item_names = {f["name"] for f in routing_fields["policies"]["item_schema"]}
    assert item_names == {
        "name",
        "model_prefix",
        "api_format",
        "strategy",
        "prefer_provider",
        "allow_cloud",
        "enabled",
    }


def test_model_pools_dict_of_object_carries_item_schema():
    doc = config_schema_document()
    routing_fields = {f["name"]: f for f in doc["sections"]["routing"]["fields"]}
    assert routing_fields["model_pools"]["widget"] == "dict"
    item_names = {f["name"] for f in routing_fields["model_pools"]["item_schema"]}
    assert item_names == {"enabled", "hosts", "models"}


def test_schema_endpoint_uses_require_admin_access(monkeypatch) -> None:
    # Admin-gating itself is covered by test_agent.py's
    # test_admin_config_rejects_remote_client against require_admin_access
    # directly; here we only need to confirm this route actually calls it
    # (same pattern as every other /netllm/v1/* admin route).
    import netllm_agent.app as app_module

    calls: list[object] = []
    monkeypatch.setattr(
        app_module, "require_admin_access", lambda request, cfg: calls.append(cfg)
    )
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "config.toml"
        cfg = NetllmConfig()
        save_config(cfg, cfg_path)
        app = create_app(cfg, config_path=cfg_path)
        with TestClient(app) as client:
            resp = client.get("/netllm/v1/config/schema")
            assert resp.status_code == 200
            assert len(calls) == 1


def test_schema_endpoint_returns_the_document() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "config.toml"
        cfg = NetllmConfig()
        save_config(cfg, cfg_path)
        app = create_app(cfg, config_path=cfg_path)
        with TestClient(app) as client:
            resp = client.get("/netllm/v1/config/schema")
            assert resp.status_code == 200
            body = resp.json()
            assert body == config_schema_document()
