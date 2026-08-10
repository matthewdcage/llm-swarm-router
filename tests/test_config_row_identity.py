"""Editing a row must not erase the secret stored on it.

Two HIGH-severity data-loss bugs, one shape. `[[routing.backends]]` and
`[[routing.sources]]` had no identity of their own: the save-path merge keyed
each row on a field the *user types into* -- a backend on its `base_url`, a
source on its `id`. So an edit to that field found no prior row and built a
fresh one from defaults, and `api_key`/`secret` are write-only: the agent
never sends them to a client, so a client can never send them back. Correcting
a port typo on a backend silently erased its API key and reset its
`max_concurrency` to 0. Renaming a source erased its secret, which on a LAN
bind then fails `config_guards`'s elevated-source check on a config the user
had set up correctly.

Every test here fails on stock code. The fix is `row_id` -- see
netllm_core.config_identity for what it is and why it is derived rather than
random, and config_merge._RowIdentityIndex for how a patch entry is matched.

The tests deliberately go through the same entry point every real save uses
(`config_merge.apply_config_patch`, shared by the dashboard's
POST /netllm/v1/admin/config and by `netllm config import`, which is the
macOS app's Save button), and build their patches out of what
`admin.config_summary` actually puts on the wire, so a client that faithfully
round-trips the export is what is being tested -- not a hand-written patch
that happens to carry the right key.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import pytest
from netllm_agent.admin import _backend_override_export, _source_export
from netllm_core.config_merge import apply_config_patch
from netllm_core.config_migrations import CURRENT_SCHEMA_VERSION
from netllm_core.models import (
    BackendOverride,
    NetllmConfig,
    SourceConfig,
    load_config,
    save_config,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATIONS = REPO_ROOT / "tests/fixtures/config-generations"


def _wire_backends(cfg: NetllmConfig) -> list[dict[str, Any]]:
    """What a client is handed by GET /netllm/v1/config, JSON round-tripped.

    Going through json guarantees nothing here depends on a model instance
    leaking across the wire boundary.
    """
    return json.loads(json.dumps(_backend_override_export(cfg)))


def _wire_sources(cfg: NetllmConfig) -> list[dict[str, Any]]:
    return json.loads(json.dumps(_source_export(cfg)))


def _client_echo(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A well-behaved client's patch: the row minus the write-only value.

    `api_key_set`/`secret` are the wire-visible forms; a client sends the
    secret back only when the user typed a new one, which none of these
    tests do -- the whole question is whether omitting it preserves it.
    """
    out = []
    for row in rows:
        echo = {k: v for k, v in row.items() if k not in {"api_key_set"}}
        echo.pop("api_key", None)
        echo.pop("secret", None)
        out.append(echo)
    return out


def _stored_backend(cfg: NetllmConfig) -> BackendOverride:
    assert len(cfg.routing.backends) == 1, cfg.routing.backends
    return cfg.routing.backends[0]


def _stored_source(cfg: NetllmConfig) -> SourceConfig:
    assert len(cfg.routing.sources) == 1, cfg.routing.sources
    return cfg.routing.sources[0]


# --- bug 1: editing a backend's base_url erased its api_key ----------------


@pytest.fixture
def backend_config() -> NetllmConfig:
    cfg = NetllmConfig()
    cfg.routing.backends = [
        BackendOverride(
            row_id="b-fixture",
            base_url="http://10.0.0.5:1234/v1",
            provider="lmstudio",
            api_key="sk-do-not-lose-me",
            api_key_env="LMSTUDIO_API_KEY",
            max_concurrency=2,
        )
    ]
    return cfg


def test_editing_a_backend_base_url_keeps_its_api_key(
    backend_config: NetllmConfig,
) -> None:
    """The bug, exactly: a port typo corrected on Routing -> Backends.

    `api_key` never leaves the agent, so the only thing that can preserve it
    is the merge recognising the edited row as the row it already had.
    """
    rows = _client_echo(_wire_backends(backend_config))
    assert rows[0]["row_id"] == "b-fixture", "the export must carry the identity"
    rows[0]["base_url"] = "http://10.0.0.5:1235/v1"  # the typo, corrected

    merged = apply_config_patch(backend_config, {"routing": {"backends": rows}})

    stored = _stored_backend(merged)
    assert stored.base_url == "http://10.0.0.5:1235/v1", "the edit did not apply"
    assert stored.api_key == "sk-do-not-lose-me", (
        "editing base_url erased the stored API key -- the merge treated the "
        "edit as delete+create, and a write-only value can never come back "
        "from a client"
    )
    assert stored.row_id == "b-fixture", "the identity must survive the edit"


def test_editing_a_backend_base_url_keeps_max_concurrency_and_key_env(
    backend_config: NetllmConfig,
) -> None:
    """The compounding half: the export dropped the fields, so did the save.

    `_backend_override_export` used to emit six of the row's fields, omitting
    `api_key_env`, `max_concurrency` and `cloud_provider` entirely. A client
    could not round-trip what it was never sent, so even a merge that found
    the prior row would have had nothing to restore them from once the row
    was rebuilt.
    """
    wire = _wire_backends(backend_config)[0]
    assert wire["max_concurrency"] == 2, "max_concurrency is not on the wire"
    assert wire["api_key_env"] == "LMSTUDIO_API_KEY", "api_key_env is not on the wire"

    rows = _client_echo([wire])
    rows[0]["base_url"] = "http://10.0.0.5:1235/v1"
    merged = apply_config_patch(backend_config, {"routing": {"backends": rows}})

    stored = _stored_backend(merged)
    assert stored.max_concurrency == 2, "editing base_url reset max_concurrency"
    assert stored.api_key_env == "LMSTUDIO_API_KEY", (
        "editing base_url reset api_key_env"
    )


def test_the_backend_export_never_ships_the_key_itself(
    backend_config: NetllmConfig,
) -> None:
    """Fixing the export must not turn it into a disclosure channel.

    GET /netllm/v1/config is readable by anyone whenever `swarm.cluster_token`
    is empty -- the default even on a LAN bind. `api_key_set: bool` stays the
    only thing said about the key, and preserved-but-unmodelled extras stay
    off the wire (the F-59 allowlist rule `_source_export` already follows).
    """
    backend_config.routing.backends[0].__pydantic_extra__["future_token"] = "leak-me"
    wire = _wire_backends(backend_config)[0]

    assert wire["api_key_set"] is True
    assert wire["api_key"] == ""
    assert "sk-do-not-lose-me" not in json.dumps(wire)
    assert "future_token" not in wire, (
        "extra='allow' keys must not stream to every reader of the config view"
    )


# --- bug 2: editing a source's id erased its secret ------------------------


@pytest.fixture
def source_config() -> NetllmConfig:
    cfg = NetllmConfig()
    cfg.routing.sources = [
        SourceConfig(
            row_id="s-fixture",
            id="cursor",
            secret="cursor-secret",
            allow_cloud=True,
            max_concurrency=4,
        )
    ]
    return cfg


def test_renaming_a_source_keeps_its_secret(source_config: NetllmConfig) -> None:
    rows = _client_echo(_wire_sources(source_config))
    assert rows[0]["row_id"] == "s-fixture", "the export must carry the identity"
    rows[0]["id"] = "cursor-laptop"  # the rename

    merged = apply_config_patch(source_config, {"routing": {"sources": rows}})

    stored = _stored_source(merged)
    assert stored.id == "cursor-laptop", "the rename did not apply"
    assert stored.secret == "cursor-secret", (
        "renaming a source erased its secret. On a LAN bind this row is "
        "elevated (allow_cloud), so the next config-apply fails the "
        "elevated-source guard on a config the user had set up correctly"
    )
    assert stored.row_id == "s-fixture"
    assert stored.max_concurrency == 4


def test_renaming_a_source_still_passes_the_elevated_source_guard(
    source_config: NetllmConfig,
) -> None:
    """The consequence, not just the field. This is what the user hits."""
    from netllm_core.config_guards import apply_config_guards

    source_config.agent.listen = "0.0.0.0:11400"
    source_config.swarm.cluster_token = "cluster-token"

    rows = _client_echo(_wire_sources(source_config))
    rows[0]["id"] = "cursor-laptop"
    merged = apply_config_patch(source_config, {"routing": {"sources": rows}})

    # Raises ConfigGuardError when the rename blanked the secret.
    apply_config_guards(merged, own_agent_urls=set())


# --- back-compat: a client that does not know about row_id ----------------


def test_a_patch_with_no_row_ids_still_merges_onto_the_stored_rows() -> None:
    """An older client must not orphan, duplicate or blank anything.

    This is the case that makes removing the legacy `base_url`/`id` fallback
    unacceptable: without it, every row an older client sends would be an
    orphan and every secret in the config would blank at once -- a far worse
    bug than the one being fixed.
    """
    cfg = NetllmConfig()
    cfg.routing.backends = [
        BackendOverride(
            row_id="b-one", base_url="http://a:1/v1", api_key="key-a", max_concurrency=1
        ),
        BackendOverride(
            row_id="b-two", base_url="http://b:2/v1", api_key="key-b", max_concurrency=2
        ),
    ]
    cfg.routing.sources = [
        SourceConfig(row_id="s-one", id="cursor", secret="secret-a"),
        SourceConfig(row_id="s-two", id="codex", secret="secret-b"),
    ]

    old_backends = [
        {"base_url": "http://a:1/v1", "provider": "custom", "enabled": True},
        {"base_url": "http://b:2/v1", "provider": "custom", "enabled": True},
    ]
    old_sources = [{"id": "cursor", "enabled": True}, {"id": "codex", "enabled": True}]

    merged = apply_config_patch(
        cfg, {"routing": {"backends": old_backends, "sources": old_sources}}
    )

    assert [b.base_url for b in merged.routing.backends] == [
        "http://a:1/v1",
        "http://b:2/v1",
    ], "an id-less patch duplicated or reordered rows"
    assert [b.api_key for b in merged.routing.backends] == ["key-a", "key-b"]
    assert [b.max_concurrency for b in merged.routing.backends] == [1, 2]
    assert [b.row_id for b in merged.routing.backends] == ["b-one", "b-two"], (
        "the stored identities must survive a patch that does not carry them"
    )
    assert [s.secret for s in merged.routing.sources] == ["secret-a", "secret-b"]
    assert [s.row_id for s in merged.routing.sources] == ["s-one", "s-two"]


def test_a_row_with_no_stored_id_gains_one_on_the_first_save() -> None:
    """Self-healing, so the id-less window is exactly one save wide.

    A row hand-added to config.toml (or written by a build older than the
    2 -> 3 migration and never re-saved) has no id. It merges by the legacy
    key this once, and comes out with an id -- so the *next* edit, the one
    that might rename it, is already protected.
    """
    cfg = NetllmConfig()
    cfg.routing.backends = [
        BackendOverride(base_url="http://hand-edited:1/v1", api_key="key")
    ]
    cfg.routing.sources = [SourceConfig(id="hand-edited", secret="secret")]

    merged = apply_config_patch(
        cfg,
        {
            "routing": {
                "backends": [{"base_url": "http://hand-edited:1/v1"}],
                "sources": [{"id": "hand-edited"}],
            }
        },
    )

    assert merged.routing.backends[0].row_id.startswith("b-")
    assert merged.routing.backends[0].api_key == "key"
    assert merged.routing.sources[0].row_id.startswith("s-")
    assert merged.routing.sources[0].secret == "secret"


def test_a_client_cannot_invent_an_id_to_steal_another_rows_secret() -> None:
    """`row_id` selects a row, it never assigns one.

    A patch naming an id the agent has never issued is a new row, not a
    hijack: it gets its own freshly minted id and no inherited secret.
    """
    cfg = NetllmConfig()
    cfg.routing.backends = [
        BackendOverride(row_id="b-real", base_url="http://a:1/v1", api_key="key-a")
    ]

    merged = apply_config_patch(
        cfg,
        {
            "routing": {
                "backends": [
                    {"row_id": "b-real", "base_url": "http://a:1/v1"},
                    {"row_id": "b-attacker-chose-this", "base_url": "http://evil:1/v1"},
                ]
            }
        },
    )

    assert merged.routing.backends[0].api_key == "key-a"
    new_row = merged.routing.backends[1]
    assert new_row.api_key == ""
    assert new_row.row_id != "b-attacker-chose-this", (
        "a client assigned its own identity; ids are server-minted so that a "
        "patch cannot collide with, or rename, a row it does not own"
    )
    assert len({b.row_id for b in merged.routing.backends}) == 2


def test_two_new_rows_on_the_same_url_do_not_share_an_identity() -> None:
    cfg = NetllmConfig()
    merged = apply_config_patch(
        cfg,
        {
            "routing": {
                "backends": [
                    {"base_url": "http://same:1/v1"},
                    {"base_url": "http://same:1/v1"},
                ]
            }
        },
    )
    ids = [b.row_id for b in merged.routing.backends]
    assert len(ids) == 2
    assert len(set(ids)) == 2, f"duplicate row identity: {ids}"


# --- a config written before this change ----------------------------------


def test_a_pre_change_config_loads_migrates_and_round_trips(tmp_path: Path) -> None:
    """The whole path a real user takes: old file -> load -> edit -> save.

    Uses the generation-2 golden fixture, which is a config written before
    row_id existed, then drives the same edit that used to destroy data. The
    file must gain ids without the user doing anything, and both secrets must
    still be there afterwards.
    """
    path = tmp_path / "config.toml"
    path.write_text(
        (GENERATIONS / "v2-to-v3/before.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    with pytest.warns(DeprecationWarning):
        cfg = load_config(path)

    assert cfg.schema_version == CURRENT_SCHEMA_VERSION
    assert all(b.row_id for b in cfg.routing.backends), "a backend was left id-less"
    assert all(s.row_id for s in cfg.routing.sources), "a source was left id-less"
    # An id minted elsewhere is never re-minted.
    assert cfg.routing.backends[2].row_id == "b-already-minted"

    save_config(cfg, path)
    on_disk = tomllib.loads(path.read_text(encoding="utf-8"))
    assert on_disk["schema_version"] == CURRENT_SCHEMA_VERSION
    assert [row["row_id"] for row in on_disk["routing"]["backends"]] == [
        b.row_id for b in cfg.routing.backends
    ]
    # Migrating twice must not change anything (the second load is a no-op).
    reloaded = load_config(path)
    assert [b.row_id for b in reloaded.routing.backends] == [
        b.row_id for b in cfg.routing.backends
    ]

    # ...and now the edit that used to destroy data.
    rows = _client_echo(_wire_backends(reloaded))
    rows[0]["base_url"] = "http://10.0.0.5:9999/v1"
    source_rows = _client_echo(_wire_sources(reloaded))
    source_rows[0]["id"] = "cursor-renamed"
    merged = apply_config_patch(
        reloaded, {"routing": {"backends": rows, "sources": source_rows}}
    )

    assert merged.routing.backends[0].api_key == "sk-lmstudio-do-not-lose-me"
    assert merged.routing.backends[0].max_concurrency == 2
    assert merged.routing.sources[0].id == "cursor-renamed"
    assert merged.routing.sources[0].secret == "cursor-secret-do-not-lose-me"


def test_the_cli_export_import_round_trip_carries_row_ids(tmp_path: Path) -> None:
    """`netllm config export | netllm config import`, the macOS Save path.

    The Swift app has no HTTP save: it shells out to these two commands. If
    the export omitted `row_id`, or the import refused it, the macOS surface
    would keep the bug all by itself.
    """
    from netllm_cli.config_json import export_config, import_config

    cfg = NetllmConfig()
    cfg.routing.backends = [
        BackendOverride(row_id="b-cli", base_url="http://a:1/v1", api_key="key-a")
    ]
    cfg.routing.sources = [SourceConfig(row_id="s-cli", id="cursor", secret="sec")]
    path = tmp_path / "config.toml"
    save_config(cfg, path)

    exported = export_config(path)
    assert exported["routing"]["backends"][0]["row_id"] == "b-cli"
    assert exported["routing"]["sources"][0]["row_id"] == "s-cli"

    # Edit both identity-bearing fields, drop both secrets (the macOS app
    # blanks api_key on load), and import.
    exported["routing"]["backends"][0]["base_url"] = "http://a:2/v1"
    exported["routing"]["backends"][0]["api_key"] = ""
    exported["routing"]["sources"][0]["id"] = "cursor-renamed"
    exported["routing"]["sources"][0]["secret"] = ""
    import_config(exported, path)

    reloaded = load_config(path)
    assert reloaded.routing.backends[0].base_url == "http://a:2/v1"
    assert reloaded.routing.backends[0].api_key == "key-a"
    assert reloaded.routing.backends[0].row_id == "b-cli"
    assert reloaded.routing.sources[0].id == "cursor-renamed"
    assert reloaded.routing.sources[0].secret == "sec"
    assert reloaded.routing.sources[0].row_id == "s-cli"
