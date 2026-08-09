"""The deprecation clock (PROGRAM.md §3 Axis E.4).

`DEPRECATIONS` below IS the registry — a frozen dataclass tuple, the same
shape as `CLOUD_PROVIDERS`. Edit it here.

`docs/deprecations.toml` is its human-readable projection, written by
`scripts/generate-registry-artifacts.py` and diffed by `scripts/ci.sh lint`,
so the doc cannot drift. The registry is the Python side rather than the TOML
because this module has to answer at *runtime*, on a machine that installed a
wheel and has no `docs/` directory: a clock only CI can read would never fire
for the person whose config carries the deprecated key. Adding a row means
editing this file and re-running the generator.

Three consumers, one registry:
  * `netllm_core.models.load_config` — a `DeprecationWarning` naming the key,
    the release it goes away in, and what to use instead.
  * `netllm_core.config_report` — `netllm doctor` and the dashboard's doctor
    panel, listing only the keys actually present in the user's file.
  * `tests/conformance/kit_versioning.py` — the gate: once
    `compare_versions(get_version(), remove_in) >= 0` and the symbol is still
    importable, CI fails. That is what makes `remove_in` a date rather than a
    wish.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

DeprecationKind = Literal["config-key", "symbol"]


@dataclass(frozen=True)
class Deprecation:
    """One thing on its way out, with the release it must be gone by."""

    id: str
    kind: DeprecationKind
    config_path: str
    symbol: str
    deprecated_in: str
    remove_in: str
    replacement: str
    notes: str

    def message(self) -> str:
        """The one-line form used by the warning and by doctor."""
        target = self.config_path or self.symbol
        moved = (
            f" Use {self.replacement} instead."
            if self.replacement
            else " There is no replacement — remove it."
        )
        return (
            f"{target} is deprecated since netllm {self.deprecated_in} and is "
            f"removed in {self.remove_in}.{moved}"
        )


# netllm:generated:begin:deprecations
DEPRECATIONS: tuple[Deprecation, ...] = (
    Deprecation(
        id="routing.require_same_model_for_shard",
        kind="config-key",
        config_path="routing.require_same_model_for_shard",
        symbol="netllm_core.models:RoutingConfig.require_same_model_for_shard",
        deprecated_in="0.4.6",
        remove_in="0.6.0",
        replacement="",
        notes=(
            "Inert since its only consumer, plan_batch_shard, was deleted as dead"
            " code (routing-hardening-plan.md Phase 5). Removed from"
            " config_summary, the dashboard and macOS Settings in 0.4.6 so nobody"
            " can toggle something that does nothing (audit F-17); the field"
            " itself is kept only so an existing config.toml still loads. The"
            " semantics move to the planned model_groups feature, so there is"
            " nothing to migrate to yet — delete the key."
        ),
    ),
    Deprecation(
        id="pool.cached_peer_online",
        kind="symbol",
        config_path="",
        symbol="netllm_core.pool:RouterPool.cached_peer_online",
        deprecated_in="0.4.6",
        remove_in="0.6.0",
        replacement="netllm_core.pool:RouterPool.cached_online",
        notes=(
            "A pure alias — `return self.cached_online(backend)`. Its docstring"
            ' said "kept for one release" without naming which one, which is'
            " exactly how a one-release shim reaches its third. No caller remains"
            " in this tree."
        ),
    ),
)
# netllm:generated:end:deprecations


CONFIG_KEY_DEPRECATIONS: tuple[Deprecation, ...] = tuple(
    entry for entry in DEPRECATIONS if entry.kind == "config-key"
)


def _walk(document: dict[str, Any], dotted: str) -> bool:
    """True when `dotted` names a key that is actually present in `document`.

    Present, not truthy: `require_same_model_for_shard = false` is still a use
    of a deprecated key and the user still has to delete it.
    """
    node: Any = document
    parts = dotted.split(".")
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return isinstance(node, dict) and parts[-1] in node


def deprecated_keys_in_document(document: dict[str, Any]) -> list[Deprecation]:
    """Config-key deprecations literally present in a parsed config.toml.

    Takes the raw parsed document rather than a `NetllmConfig` on purpose: a
    validated model carries every field at its default, so "is the key set"
    is unanswerable from the model. Only the file knows.
    """
    return [
        entry
        for entry in CONFIG_KEY_DEPRECATIONS
        if entry.config_path and _walk(document, entry.config_path)
    ]


def expired(version: str) -> list[Deprecation]:
    """Entries whose `remove_in` this version has reached or passed.

    The gate's other half — "and the symbol still exists" — lives in
    `tests/conformance/kit_versioning.py`, because resolving a symbol means
    importing it and this module is imported by `models.py` on every load.
    """
    from netllm_core.update import compare_versions

    return [
        entry
        for entry in DEPRECATIONS
        if compare_versions(version, entry.remove_in) >= 0
    ]
