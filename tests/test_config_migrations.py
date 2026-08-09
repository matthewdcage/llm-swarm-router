"""Migrations run on every user's real config. These are the safety proofs.

Five properties, each with a named test:

1. The no-op migration is a **genuine** no-op on `config.example.toml`.
2. It is a genuine no-op on a config carrying keys this build does not model
   (Phase 2's forward-compatibility property must survive the rail).
3. The backup is written **before** the first migrated write, not after.
4. A config stamped newer than this build is never downgraded and never
   "migrated" by a step that was not written for it.
5. A corrupt or partially written config cannot be made worse.

Everything here is dict -> dict except the backup and load/save tests, which
is the point of `config_migrations` being pure.
"""

from __future__ import annotations

import copy
import tomllib
from pathlib import Path

import pytest
from netllm_core.config_migrations import (
    CURRENT_SCHEMA_VERSION,
    LEGACY_SCHEMA_VERSION,
    MIGRATIONS,
    SCHEMA_VERSION_KEY,
    document_schema_version,
    migrate_document,
    pending_migrations,
)
from netllm_core.models import (
    NetllmConfig,
    load_config,
    pre_migration_backup,
    save_config,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATIONS = REPO_ROOT / "tests/fixtures/config-generations"
EXAMPLE_CONFIG = REPO_ROOT / "config.example.toml"


# --- the rail itself ------------------------------------------------------


def test_migrations_form_one_unbroken_chain() -> None:
    """A gap or a duplicate step silently skips a migration on real data."""
    assert MIGRATIONS, "the rail must carry at least the no-op step"
    assert MIGRATIONS[0].from_version == LEGACY_SCHEMA_VERSION
    for step in MIGRATIONS:
        assert step.to_version == step.from_version + 1, (
            f"migration {step.from_version}->{step.to_version} is not a single "
            "step; the runner applies them in order and cannot bridge a gap"
        )
        assert step.notes.strip(), (
            f"migration {step.from_version}->{step.to_version} has no notes, "
            "so `netllm config migrate --dry-run` cannot say what it does"
        )
    assert MIGRATIONS[-1].to_version == CURRENT_SCHEMA_VERSION, (
        "CURRENT_SCHEMA_VERSION and the last migration disagree — a config "
        "would be stamped a generation the rail never produced"
    )


def test_absent_schema_version_is_generation_one() -> None:
    assert document_schema_version({}) == LEGACY_SCHEMA_VERSION
    assert document_schema_version({"agent": {}}) == LEGACY_SCHEMA_VERSION


@pytest.mark.parametrize("junk", ["2", 2.0, True, None, -5, [2]])
def test_unreadable_schema_version_degrades_to_generation_one(junk: object) -> None:
    """Refusing to start is a worse outcome than running no-op migrations."""
    assert document_schema_version({SCHEMA_VERSION_KEY: junk}) == LEGACY_SCHEMA_VERSION


def test_pending_is_empty_at_current_generation() -> None:
    assert pending_migrations(CURRENT_SCHEMA_VERSION) == ()


def test_migrate_never_mutates_its_argument() -> None:
    document = {"agent": {"hostname": "a"}, "future_section": {"k": 1}}
    before = copy.deepcopy(document)
    migrate_document(document)
    assert document == before


# --- proof 1: genuine no-op on the real example config --------------------


def test_no_op_migration_is_genuine_on_config_example_toml() -> None:
    """The shipped example config, through the rail, unchanged but stamped.

    `config.example.toml` is the closest thing in the tree to a real user's
    file. If the "no-op" migration touched anything, it would show here.
    """
    document = tomllib.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    result = migrate_document(document)

    assert result.from_version == LEGACY_SCHEMA_VERSION
    assert result.to_version == CURRENT_SCHEMA_VERSION
    assert result.document[SCHEMA_VERSION_KEY] == CURRENT_SCHEMA_VERSION

    without_stamp = dict(result.document)
    del without_stamp[SCHEMA_VERSION_KEY]
    assert without_stamp == document, (
        "the 1->2 migration changed something in config.example.toml. It is "
        "declared to be an identity function on the document; if that is no "
        "longer true, it is not a no-op and needs a golden pair proving what "
        "it does."
    )


def test_every_migration_step_is_declared_pure_of_the_stamp() -> None:
    """No migration may stamp `schema_version` itself.

    The runner owns the stamp. A migration that writes it can half-advance a
    document — leave it claiming generation N while only part of N's transform
    ran — and nothing downstream could tell.
    """
    document = {"agent": {"hostname": "a"}}
    for step in MIGRATIONS:
        produced = step.apply(copy.deepcopy(document))
        assert SCHEMA_VERSION_KEY not in produced, (
            f"migration {step.from_version}->{step.to_version} stamps "
            "schema_version; leave that to migrate_document"
        )


# --- proof 2: no-op on a config full of keys this build does not model ----


def test_no_op_migration_preserves_unknown_keys(tmp_path: Path) -> None:
    """Phase 2's forward-compat property, re-proved through the rail."""
    before = tomllib.loads(
        (GENERATIONS / "v1-to-v2/before.toml").read_text(encoding="utf-8")
    )
    expected = tomllib.loads(
        (GENERATIONS / "v1-to-v2/after.toml").read_text(encoding="utf-8")
    )
    result = migrate_document(before)
    assert result.document == expected, (
        "the golden pair in tests/fixtures/config-generations/v1-to-v2 no "
        "longer describes what the migration does"
    )

    # And the same document through the real load path keeps its unknowns.
    path = tmp_path / "config.toml"
    path.write_text(
        (GENERATIONS / "v1-to-v2/before.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    with pytest.warns(DeprecationWarning):
        cfg = load_config(path)
    assert cfg.schema_version == CURRENT_SCHEMA_VERSION
    save_config(cfg, path)
    round_tripped = tomllib.loads(path.read_text(encoding="utf-8"))
    assert round_tripped["future_section"] == {"knob": 3}
    assert round_tripped["agent"]["future_field"] == "written by a newer netllm"
    assert round_tripped["cloud"]["providers"]["future_provider"]["region"] == "moon-1"
    assert round_tripped[SCHEMA_VERSION_KEY] == CURRENT_SCHEMA_VERSION


def test_a_config_the_user_explicitly_set_a_deprecated_key_on_keeps_it(
    tmp_path: Path,
) -> None:
    """`require_same_model_for_shard = false` is a choice, not a leftover.

    `save_config` drops the key when it sits at its default, so the warning is
    about something the user can act on. It must NOT drop a value they set.
    """
    path = tmp_path / "config.toml"
    path.write_text(
        (GENERATIONS / "v1-to-v2/before.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    with pytest.warns(DeprecationWarning):
        cfg = load_config(path)
    save_config(cfg, path)
    reloaded = tomllib.loads(path.read_text(encoding="utf-8"))
    assert reloaded["routing"]["require_same_model_for_shard"] is False


def test_a_defaulted_deprecated_key_stops_being_rewritten(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    save_config(NetllmConfig(), path)
    written = tomllib.loads(path.read_text(encoding="utf-8"))
    assert "require_same_model_for_shard" not in written["routing"], (
        "save_config re-emits a deprecated key at its default, so the "
        "DeprecationWarning fires on something the user cannot remove"
    )


# --- proof 3: the backup happens BEFORE the migrated write ----------------


def test_backup_is_written_before_the_first_migrated_write(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    original = (GENERATIONS / "v1-to-v2/before.toml").read_text(encoding="utf-8")
    path.write_text(original, encoding="utf-8")

    with pytest.warns(DeprecationWarning):
        cfg = load_config(path)
    save_config(cfg, path)

    backup = tmp_path / "config.toml.bak-v1"
    assert backup.is_file(), "no pre-migration backup was taken"
    assert backup.read_text(encoding="utf-8") == original, (
        "the backup is not byte-identical to the pre-migration file"
    )
    assert SCHEMA_VERSION_KEY not in tomllib.loads(
        backup.read_text(encoding="utf-8")
    ), "the backup is of the MIGRATED file — the ordering is wrong"


def test_the_backup_is_taken_only_once(tmp_path: Path) -> None:
    """A second save must not overwrite the pristine copy with a migrated one."""
    path = tmp_path / "config.toml"
    original = (GENERATIONS / "v1-to-v2/before.toml").read_text(encoding="utf-8")
    path.write_text(original, encoding="utf-8")

    with pytest.warns(DeprecationWarning):
        cfg = load_config(path)
    save_config(cfg, path)
    backup = tmp_path / "config.toml.bak-v1"
    first = backup.read_text(encoding="utf-8")

    save_config(load_config(path), path)
    assert backup.read_text(encoding="utf-8") == first
    assert not (tmp_path / "config.toml.bak-v2").exists(), (
        "a backup was taken of an already-current config"
    )


def test_no_backup_when_nothing_is_being_migrated(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    save_config(NetllmConfig(), path)
    save_config(load_config(path), path)
    assert not list(tmp_path.glob("config.toml.bak-*")), (
        "a backup file appeared for a config that needed no migration"
    )


def test_backup_of_a_missing_file_is_a_no_op(tmp_path: Path) -> None:
    assert pre_migration_backup(tmp_path / "nope.toml") is None


# --- proof 4: a newer config is never downgraded --------------------------


def test_a_newer_generation_is_returned_untouched() -> None:
    document = {
        SCHEMA_VERSION_KEY: CURRENT_SCHEMA_VERSION + 7,
        "agent": {"hostname": "from-the-future"},
        "unheard_of": {"k": 1},
    }
    result = migrate_document(document)
    assert result.from_the_future is True
    assert result.applied == ()
    assert result.document is document
    assert result.document[SCHEMA_VERSION_KEY] == CURRENT_SCHEMA_VERSION + 7


def test_a_newer_generation_survives_load_and_save(tmp_path: Path) -> None:
    """The mixed-version mesh case: an older machine must not stamp a newer
    config back down to its own generation."""
    path = tmp_path / "config.toml"
    future = CURRENT_SCHEMA_VERSION + 7
    path.write_text(
        f"schema_version = {future}\n\n[agent]\nhostname = 'later'\n\n"
        "[unheard_of]\nk = 1\n",
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.schema_version == future
    save_config(cfg, path)
    reloaded = tomllib.loads(path.read_text(encoding="utf-8"))
    assert reloaded[SCHEMA_VERSION_KEY] == future, (
        "an older netllm lowered a newer config's schema_version — the next "
        "newer agent to read it would skip a migration it needs"
    )
    assert reloaded["unheard_of"] == {"k": 1}
    assert not list(tmp_path.glob("config.toml.bak-*")), (
        "a from-the-future config was backed up as if it were being migrated"
    )


# --- proof 5: a corrupt config cannot be made worse -----------------------

CORRUPT = [
    pytest.param("[agent\nhostname = 'x'\n", id="unclosed-table-header"),
    pytest.param("[agent]\nhostname = \n", id="truncated-mid-assignment"),
    pytest.param("[agent]\nhostname = 'x'\n[agent]\n", id="duplicate-table"),
    pytest.param("\x00\x01\x02 not toml at all", id="binary-garbage"),
    pytest.param("", id="empty-file"),
]


@pytest.mark.parametrize("text", CORRUPT)
def test_a_corrupt_config_fails_at_parse_not_inside_a_migration(
    tmp_path: Path, text: str
) -> None:
    """Migrations run AFTER tomllib, so they never see a malformed file.

    The error the user gets is the TOML parser's, naming the line — not a
    traceback out of a migration that half-understood the document.
    """
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    if text == "":
        assert load_config(path).schema_version == CURRENT_SCHEMA_VERSION
        return
    with pytest.raises(tomllib.TOMLDecodeError):
        load_config(path)


@pytest.mark.parametrize("text", CORRUPT)
def test_a_corrupt_config_is_copied_aside_rather_than_silently_overwritten(
    tmp_path: Path, text: str
) -> None:
    """The adversarial case: something has already broken the file on disk and
    a save path runs anyway (a dashboard POST against a config a text editor
    left half-written).

    Before this phase the corrupt bytes were simply overwritten. Now they are
    copied first. The copy may be mislabelled `-v1` when the version cannot be
    read, which is stated in `pre_migration_backup` and is strictly better than
    losing it.
    """
    path = tmp_path / "config.toml"
    path.write_bytes(text.encode("utf-8", "surrogateescape"))
    save_config(NetllmConfig(), path)
    backups = list(tmp_path.glob("config.toml.bak-*"))
    assert len(backups) == 1, f"expected exactly one backup, got {backups}"
    assert backups[0].read_bytes() == text.encode("utf-8", "surrogateescape")


def test_a_document_whose_sections_are_the_wrong_type_is_not_touched() -> None:
    """A migration must not "repair" a document. Pydantic reports the real
    error; a migration that tried to coerce would hide it."""
    document = {"agent": "this should be a table", "routing": [1, 2, 3]}
    result = migrate_document(document)
    assert result.document["agent"] == "this should be a table"
    assert result.document["routing"] == [1, 2, 3]
    with pytest.raises(Exception):
        NetllmConfig.model_validate(result.document)


# --- the two version axes stay distinct -----------------------------------


def test_schema_version_is_not_the_app_version_etag() -> None:
    """`config_schema.get_version()` is the APP version, served as an ETag on
    GET /netllm/v1/config/schema. `schema_version` is the on-disk generation.
    Conflating them is the mistake PROGRAM.md §4 names; pin them apart."""
    from netllm_core.config_schema import config_schema_document
    from netllm_core.version import get_version

    document = config_schema_document()
    assert document["version"] == get_version()
    assert isinstance(document["version"], str)
    assert isinstance(CURRENT_SCHEMA_VERSION, int)
    assert "schema_version" not in document
    assert str(CURRENT_SCHEMA_VERSION) != get_version()


def test_schema_version_is_not_an_editable_section() -> None:
    """No client Save may set the generation. The migration runner owns it."""
    from netllm_core.config_merge import apply_config_patch
    from netllm_core.config_schema import SECTIONS

    assert "schema_version" not in SECTIONS
    cfg = NetllmConfig()
    merged = apply_config_patch(cfg, {"schema_version": 99})
    assert merged.schema_version == CURRENT_SCHEMA_VERSION


def test_a_pre_existing_backup_is_never_overwritten(tmp_path: Path) -> None:
    """The oldest copy of the user's config is the valuable one.

    `pre_migration_backup` returns early when `.bak-v{n}` already exists, and
    its docstring calls that out as a property — but nothing enforced it.
    Deleting the two-line guard left the whole suite green, and the harm is
    direct: the backup taken on the FIRST upgrade is the only copy of the
    user's original config, and a later save would replace it with something
    already migrated.

    Found by adversarial review, which removed the guard and watched a
    hand-written `.bak-v1` get clobbered.
    """
    from netllm_core.models import load_config, save_config

    path = tmp_path / "config.toml"
    path.write_text('[agent]\nlisten = "127.0.0.1:11400"\n', encoding="utf-8")

    backup = tmp_path / "config.toml.bak-v1"
    backup.write_text("PRECIOUS ORIGINAL — must survive\n", encoding="utf-8")
    before = backup.read_text(encoding="utf-8")

    cfg = load_config(path)
    save_config(cfg, path)
    save_config(load_config(path), path)

    assert backup.read_text(encoding="utf-8") == before, (
        "an existing .bak-v1 was overwritten; the user's original config is "
        "gone and only a migrated copy survives"
    )
