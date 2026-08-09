"""Forward-compatible config writes (PROGRAM.md Phase 2, Axis E items 1/5).

Every write path in the tree -- `POST /netllm/v1/admin/config`, `netllm
config import` (the macOS Settings **Save** button, a subprocess call),
`netllm join` -- ends in `save_config`, which rewrites the whole file from
`NetllmConfig.model_dump()`. Until this phase `NetllmConfig` declared no
`model_config`, so pydantic's default `extra="ignore"` applied and every
key the running agent did not know was silently deleted on the next save.

On a mixed-version mesh that is data loss in the *ordinary upgrade path*:
upgrade one machine, configure a provider there, press Save on an older
machine, the newer keys are gone. This is F-01's class generalized from
"a field we forgot to copy in the merge layer" to "every key this build
has never heard of".

The tests below are the contract: unknown sections and unknown fields
survive load -> save, survive `apply_config_patch`, and survive an older
agent saving a newer agent's file.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from netllm_core.config_merge import _CONFIG_SECTIONS, apply_config_patch
from netllm_core.config_schema import SECTIONS
from netllm_core.models import (
    NON_SECTION_FIELDS,
    CloudProviderConfig,
    NetllmConfig,
    SourceConfig,
    load_config,
    save_config,
)

# A config.toml as written by a hypothetical newer agent: two unknown
# top-level sections, unknown fields inside four known sections, and an
# unknown cloud provider subtree.
NEWER_CONFIG_TOML = """\
[agent]
listen = "127.0.0.1:11400"
future_field = "keep"

[discovery]
providers = ["ollama"]
future_discovery_knob = 7

[routing]
default_strategy = "round_robin"
future_routing_knob = true

[cloud]
enabled = true
future_cloud_knob = "keep"

[cloud.providers.openrouter]
enabled = true

[cloud.providers.future_provider]
enabled = true
api_key_env = "FUTURE_PROVIDER_API_KEY"

[future_section]
setting = "keep"
nested = { deeper = 1 }

[another_future_section]
value = 2
"""


def _write_newer_config(tmp_path: Path) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(NEWER_CONFIG_TOML, encoding="utf-8")
    return path


# --- load -> save ----------------------------------------------------------


def test_unknown_section_survives_load_save(tmp_path: Path) -> None:
    path = _write_newer_config(tmp_path)
    save_config(load_config(path), path)
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    assert raw["future_section"] == {"setting": "keep", "nested": {"deeper": 1}}
    assert raw["another_future_section"] == {"value": 2}


def test_unknown_field_in_known_section_survives_load_save(tmp_path: Path) -> None:
    path = _write_newer_config(tmp_path)
    save_config(load_config(path), path)
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    assert raw["agent"]["future_field"] == "keep"
    assert raw["discovery"]["future_discovery_knob"] == 7
    assert raw["routing"]["future_routing_knob"] is True
    assert raw["cloud"]["future_cloud_knob"] == "keep"


def test_load_save_does_not_disturb_known_values(tmp_path: Path) -> None:
    path = _write_newer_config(tmp_path)
    save_config(load_config(path), path)
    reloaded = load_config(path)
    assert reloaded.agent.listen == "127.0.0.1:11400"
    assert reloaded.routing.default_strategy == "round_robin"
    assert reloaded.cloud.providers["openrouter"].enabled is True


def test_preserved_extra_paths_names_every_unknown_key(tmp_path: Path) -> None:
    cfg = load_config(_write_newer_config(tmp_path))
    from netllm_core.models import preserved_extra_paths

    assert preserved_extra_paths(cfg) == [
        "agent.future_field",
        "another_future_section",
        "cloud.future_cloud_knob",
        "cloud.providers.future_provider",
        "discovery.future_discovery_knob",
        "future_section",
        "routing.future_routing_knob",
    ]


def test_save_config_logs_one_warning_naming_preserved_keys(
    tmp_path: Path, caplog
) -> None:
    cfg = load_config(_write_newer_config(tmp_path))
    with caplog.at_level("WARNING", logger="netllm_core.models"):
        save_config(cfg, tmp_path / "out.toml")
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "future_section" in message
    assert "agent.future_field" in message


def test_no_warning_when_config_has_no_unknown_keys(tmp_path: Path) -> None:
    import logging

    logger = logging.getLogger("netllm_core.models")
    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append  # type: ignore[method-assign]
    logger.addHandler(handler)
    try:
        save_config(NetllmConfig(), tmp_path / "plain.toml")
    finally:
        logger.removeHandler(handler)
    assert records == []


# --- the merge path (dashboard Save / macOS Save both land here) -----------


def test_unknown_keys_survive_apply_config_patch(tmp_path: Path) -> None:
    cfg = load_config(_write_newer_config(tmp_path))
    # A patch this older agent's own UI would send: only fields it models.
    patch = {
        "agent": {"listen": "0.0.0.0:11400"},
        "routing": {"default_strategy": "least_load"},
    }
    updated = apply_config_patch(cfg, patch)
    dumped = updated.model_dump(mode="json")
    assert dumped["agent"]["listen"] == "0.0.0.0:11400"
    assert dumped["agent"]["future_field"] == "keep"
    assert dumped["routing"]["default_strategy"] == "least_load"
    assert dumped["routing"]["future_routing_knob"] is True
    assert dumped["future_section"] == {"setting": "keep", "nested": {"deeper": 1}}


def test_apply_config_patch_accepts_unknown_keys_from_a_newer_client() -> None:
    """The reverse skew: a NEWER client saving through an OLDER agent's
    admin route. Its patch carries keys this build has no field for; they
    must land in the file rather than be filtered out on the way in."""
    updated = apply_config_patch(
        NetllmConfig(),
        {
            "agent": {"future_field": "from-newer-client"},
            "future_section": {"setting": "from-newer-client"},
        },
    )
    dumped = updated.model_dump(mode="json")
    assert dumped["agent"]["future_field"] == "from-newer-client"
    assert dumped["future_section"] == {"setting": "from-newer-client"}


def test_older_agent_save_preserves_newer_agent_keys(tmp_path: Path) -> None:
    """The mixed-version mesh scenario from PROGRAM.md §4.

    Machine A is upgraded and writes keys machine B has never heard of;
    the config is synced (or the same file is edited from both). An
    operator then presses Save in machine B's Settings, which is
    `netllm config import`: load -> apply_config_patch -> save. Nothing
    machine A wrote may be lost.
    """
    path = _write_newer_config(tmp_path)
    older_agent_save_patch = {
        "agent": {"listen": "127.0.0.1:11401"},
        "swarm": {"mdns": False},
        "cloud": {"providers": {"openrouter": {"enabled": False}}},
    }
    cfg = apply_config_patch(load_config(path), older_agent_save_patch)
    save_config(cfg, path)

    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    assert raw["agent"]["listen"] == "127.0.0.1:11401"
    assert raw["swarm"]["mdns"] is False
    assert raw["cloud"]["providers"]["openrouter"]["enabled"] is False
    # ... and every key the older agent does not model is still there.
    assert raw["agent"]["future_field"] == "keep"
    assert raw["discovery"]["future_discovery_knob"] == 7
    assert raw["routing"]["future_routing_knob"] is True
    assert raw["cloud"]["future_cloud_knob"] == "keep"
    assert raw["future_section"]["setting"] == "keep"
    assert raw["another_future_section"]["value"] == 2
    future_provider = raw["cloud"]["providers"]["future_provider"]
    assert future_provider["enabled"] is True
    assert future_provider["api_key_env"] == "FUTURE_PROVIDER_API_KEY"


# --- CloudConfig's filtering validator (models.py ~470-475) ----------------


def test_unknown_cloud_provider_subtree_is_preserved(tmp_path: Path) -> None:
    path = _write_newer_config(tmp_path)
    cfg = load_config(path)
    assert "future_provider" in cfg.cloud.providers
    save_config(cfg, path)
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    assert raw["cloud"]["providers"]["future_provider"]["enabled"] is True


def test_unknown_cloud_provider_survives_the_merge_path() -> None:
    cfg = NetllmConfig.model_validate(
        {"cloud": {"providers": {"future_provider": {"enabled": True}}}}
    )
    updated = apply_config_patch(
        cfg, {"cloud": {"providers": {"openrouter": {"enabled": True}}}}
    )
    assert set(updated.cloud.providers) == {"future_provider", "openrouter"}


def test_doctor_reports_unknown_cloud_provider_instead_of_deleting_it() -> None:
    from netllm_core.config_report import unknown_cloud_provider_issues

    cfg = NetllmConfig.model_validate(
        {"cloud": {"providers": {"future_provider": {"enabled": True}}}}
    )
    issues = unknown_cloud_provider_issues(cfg)
    assert len(issues) == 1
    assert "future_provider" in issues[0]["title"]
    assert unknown_cloud_provider_issues(NetllmConfig()) == []


# --- roster / allowlist parity (item 4) ------------------------------------


def test_section_roster_three_way_equality() -> None:
    """The six editable sections are stated in three places. They are one
    fact; a seventh section added to `NetllmConfig` alone would be
    unreachable from the schema endpoint and unsavable through the merge."""
    sections = set(NetllmConfig.model_fields) - NON_SECTION_FIELDS
    assert sections == set(SECTIONS)
    assert sections == set(_CONFIG_SECTIONS)


def test_merge_sources_allowlist_matches_source_config_fields() -> None:
    # `id` is the identity key (set separately); `secret` is write-only
    # (empty patch value keeps the stored one). Everything else must be
    # copyable from a patch or it is silently unsavable on every surface.
    from netllm_core.config_merge import _MERGE_SOURCE_FIELDS

    assert set(_MERGE_SOURCE_FIELDS) == set(SourceConfig.model_fields) - {
        "id",
        "secret",
    }


def test_merge_cloud_providers_allowlist_matches_provider_config_fields() -> None:
    from netllm_core.config_merge import _MERGE_CLOUD_PROVIDER_FIELDS

    assert set(_MERGE_CLOUD_PROVIDER_FIELDS) == set(
        CloudProviderConfig.model_fields
    ) - {"api_key"}


# --- dict-field classification completeness (item 5) -----------------------


def _section_dict_fields() -> set[tuple[str, str]]:
    import types
    import typing

    found: set[tuple[str, str]] = set()
    for section, model in SECTIONS.items():
        for name, field in model.model_fields.items():
            annotation = field.annotation
            origin = typing.get_origin(annotation)
            if origin is typing.Union or origin is types.UnionType:
                args = [a for a in typing.get_args(annotation) if a is not type(None)]
                annotation = args[0] if len(args) == 1 else annotation
                origin = typing.get_origin(annotation)
            if origin is dict:
                found.add((section, name))
    return found


def test_every_section_dict_field_is_classified() -> None:
    """Full-replace vs deep-merge is a genuine semantic choice per dict
    (PROGRAM.md §6.5), so it stays hand-declared -- but an unclassified
    dict field silently inherits deep-merge, which is the bug class
    `0c4489d` was filed to fix (an entry omitted from a patch could not
    be deleted). Force the choice to be made."""
    from netllm_core.config_merge import (
        _DEEP_MERGE_DICT_PATHS,
        _FULL_REPLACE_DICT_PATHS,
    )

    classified = set(_FULL_REPLACE_DICT_PATHS) | set(_DEEP_MERGE_DICT_PATHS)
    unclassified = _section_dict_fields() - classified
    assert unclassified == set(), (
        "these dict-typed section fields are neither full-replace nor "
        f"deep-merge: {sorted(unclassified)}"
    )
    assert not (set(_FULL_REPLACE_DICT_PATHS) & set(_DEEP_MERGE_DICT_PATHS))
    # Both rosters describe real fields.
    assert classified <= _section_dict_fields()


# --- The other half of extra="allow": preserving a key must not publish it ---


def test_source_export_is_an_allowlist_not_a_denylist() -> None:
    """Preserved unknown keys must not become a new disclosure channel.

    `extra="allow"` (above) means a newer client's keys survive a save on
    an older agent. `_source_export` feeds GET /netllm/v1/config, which
    `require_read_access` leaves open whenever `swarm.cluster_token` is
    empty -- the default even on a LAN bind. It used to dump the whole
    model and blank only `secret`, so every preserved extra would stream
    to any reader, credential-shaped or not. That is F-59's class exactly,
    reintroduced by the fix one section up.
    """
    from netllm_agent.admin import _source_export

    cfg = NetllmConfig()
    cfg.routing.sources = [
        SourceConfig.model_validate(
            {
                "id": "s1",
                "secret": "sk-stored-secret",
                "future_token": "sk-a-newer-clients-credential",
                "future_flag": True,
            }
        )
    ]
    # The extras really are stored -- this is not a vacuous assertion.
    assert cfg.routing.sources[0].model_dump()["future_token"] == (
        "sk-a-newer-clients-credential"
    )

    row = _source_export(cfg)[0]
    leaked = set(row) - set(SourceConfig.model_fields)
    assert not leaked, f"unknown keys reached the wire view: {sorted(leaked)}"
    assert row["secret"] == ""
    assert "sk-" not in json.dumps(row)
    # Still the *full editable shape* -- the reason the export exists.
    assert row["id"] == "s1"


def test_a_redacted_source_row_round_trips_without_data_loss() -> None:
    """What makes the allowlist safe rather than merely quiet.

    A dashboard/Settings save POSTs back what it GET-ed. Since the wire
    view no longer carries the secret or the extras, the write path has to
    supply both from storage -- `_merge_sources` seeds each row from the
    prior model dump, and `secret` is write-only.
    """
    from netllm_agent.admin import _source_export

    cfg = NetllmConfig()
    cfg.routing.sources = [
        SourceConfig.model_validate(
            {"id": "s1", "secret": "sk-stored-secret", "future_token": "keep-me"}
        )
    ]
    edited = dict(_source_export(cfg)[0], enabled=False)
    merged = apply_config_patch(cfg, {"routing": {"sources": [edited]}})

    source = merged.routing.sources[0]
    assert source.secret == "sk-stored-secret", "read-modify-write blanked the secret"
    assert source.model_dump()["future_token"] == "keep-me", (
        "read-modify-write destroyed a newer client's key"
    )
    assert source.enabled is False, "the client's actual edit was dropped"
