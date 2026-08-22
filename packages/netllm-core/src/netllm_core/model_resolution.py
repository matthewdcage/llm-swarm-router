"""ModelResolver — the single alias/pool/catalog matcher (F-25 core).

Before this module the router had **two** independent matchers that had to
agree and did not:

* **Matcher A** — candidacy. ``RouterPool.backends_for_model`` asked
  ``_serves_model`` whether a backend's catalog covered the requested name
  (or, for a pool member, the pool's allowed models). Fully casefolded,
  exact-or-``name:tag``-prefix, no notion of *which* served ID won.
* **Matcher B** — invocation. ``AgentService._model_for_backend`` walked
  alias exact → alias tag-prefix → alias casefold → ``resolve_via_pool`` →
  passthrough, and ``resolve_via_pool`` had **no tag-prefix arm at all**.

A backend could therefore be picked *because* its ``poolmodel:7b`` tag-prefix
matched the pool's ``poolmodel``, and then be invoked with the raw requested
name it does not serve, because the invocation matcher's pool arm could not
see the same match. That is the F-25 regression trap recorded in Phase 0
(``naming-f25-trap-pool-tag-prefix-disagree``).

This module replaces both with ONE walk::

    alias exact  →  alias tag-prefix  →  alias casefold
                 →  pool exact → pool tag-prefix → pool casefold
                 →  catalog passthrough

``resolve()`` returns the served ID the walk landed on plus the stage that
produced it; ``serves()`` is *derived from the same walk* (it is simply
"the walk did not fall through to passthrough", plus the two catalog rules
below), so candidacy and invocation can no longer disagree by construction.

Two catalog rules ride along, lifted verbatim from the old candidacy path:

* **blind catalog** — a backend with an empty ``health.models`` (unprobed,
  or a cloud row whose catalog only exists per request) stays a candidate
  rather than being guessed wrong about; the requested name passes through.
* **auth-gated skip** — except when that empty catalog belongs to a *local*
  backend whose probe came back 401/403: it is reachable but every inference
  call will fail, and as a blind candidate it shows ``in_flight=0`` and wins
  every ``least_load`` pick, starving real backends.

Group representation (plan §3 Phase 8c): ``routing.model_pools`` is parsed
into :class:`ModelGroup` at construction. A pool *is* a group, so the
planned ``routing.model_groups`` (docs/routing-hardening-plan.md:167–176) is
schema-only work — a second parser into the same representation — rather
than a second mechanism to keep in sync.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

from netllm_core.capabilities import model_capability
from netllm_core.models import ModelPool


def alias_equivalence_classes(
    model_aliases: dict[str, list[str]],
) -> dict[str, str]:
    """Union-find over alias keys and values that share a pool tier spelling.

    Cross-tier names listed under one alias key (e.g. Gemma plus Nemotron)
    are not merged into one class.
    """
    parent: dict[str, str] = {}

    def find(name: str) -> str:
        key = name.casefold()
        parent.setdefault(key, key)
        if parent[key] != key:
            parent[key] = find(parent[key])
        return parent[key]

    def union(left: str, right: str) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    for key, values in model_aliases.items():
        find(key)
        for value in values:
            if model_capability(key) != model_capability(value):
                continue
            union(key, value)
    return {key: find(key) for key in parent}


# Stage names returned by ModelResolver.resolve(). Ordered as the walk runs.
STAGE_ALIAS_EXACT = "alias-exact"
STAGE_ALIAS_TAG_PREFIX = "alias-tag-prefix"
STAGE_ALIAS_CASEFOLD = "alias-casefold"
STAGE_GROUP_EXACT = "group-exact"
STAGE_GROUP_TAG_PREFIX = "group-tag-prefix"
STAGE_GROUP_CASEFOLD = "group-casefold"
STAGE_BLIND_CATALOG = "blind-catalog"
STAGE_AUTH_GATED = "auth-gated"
STAGE_PASSTHROUGH = "passthrough"

# Stages where the walk found a real served ID. Every other stage means the
# requested name goes upstream unchanged.
_MATCHED_STAGES = frozenset(
    {
        STAGE_ALIAS_EXACT,
        STAGE_ALIAS_TAG_PREFIX,
        STAGE_ALIAS_CASEFOLD,
        STAGE_GROUP_EXACT,
        STAGE_GROUP_TAG_PREFIX,
        STAGE_GROUP_CASEFOLD,
    }
)

_AUTH_GATED_STATUSES = (401, 403)


class _BackendLike(Protocol):
    """The Backend surface the resolver reads.

    Structural rather than concrete so the resolver stays a leaf module:
    it must not pull in the pool/service graph, and tests can drive it with
    a stub.
    """

    id: str
    base_url: str
    agent_id: str
    local: bool

    @property
    def health(self) -> object: ...


@dataclass(frozen=True)
class ModelGroup:
    """A named set of models scoped to a set of hosts.

    ``routing.model_pools`` parses into this; a future
    ``routing.model_groups`` parses into the same thing (Phase 8c,
    "fold, don't coexist").
    """

    name: str
    hosts: tuple[str, ...]
    models: tuple[str, ...]
    enabled: bool = True


@dataclass(frozen=True)
class Resolution:
    """Outcome of one resolver walk for (requested name, backend)."""

    requested: str
    upstream_model: str
    stage: str

    @property
    def matched(self) -> bool:
        """True when the walk landed on a served ID this backend advertises."""
        return self.stage in _MATCHED_STAGES

    @property
    def serves(self) -> bool:
        """Whether this backend is a candidate for the requested name.

        Derived from the same walk as ``upstream_model`` — that identity is
        the whole point of F-25. Blind catalogs are candidates (nothing is
        known, so nothing is ruled out); auth-gated local rows are not.
        """
        return self.matched or self.stage == STAGE_BLIND_CATALOG


def _served_models(backend: _BackendLike) -> list[str]:
    health = backend.health
    models = getattr(health, "models", None)
    return list(models) if models else []


def _walk(candidates: Sequence[str], served: Sequence[str]) -> tuple[str, str] | None:
    """The shared three-arm match, used for aliases and for groups alike.

    Exact matches across the whole candidate list win before any prefix
    match — a backend may serve several tags of one base name. Tag-prefix
    matches (Ollama-style ``name:tag``) return the *full* served ID, since a
    bare name only resolves to the ``latest`` tag upstream. Casefold matches
    fall back to the served ID's exact casing — providers like oMLX reject
    differently-cased names.

    Candidate order is the tie-break in every arm (configured order wins),
    which is also the order matcher A used, so ``serves()`` and the invoked
    name are answers from one walk rather than two.
    """
    if not candidates or not served:
        return None
    for name in candidates:
        if name in served:
            return name, "exact"
    for name in candidates:
        for served_id in served:
            if served_id.startswith(name + ":"):
                return served_id, "tag-prefix"
    for name in candidates:
        folded = name.casefold()
        for served_id in served:
            sid = served_id.casefold()
            if sid == folded or sid.startswith(folded + ":"):
                return served_id, "casefold"
    return None


class ModelResolver:
    """One matcher for candidacy (`serves`), invocation (`resolve`) and
    404 hints (`known_models`).

    Holds references to the live ``model_aliases`` mapping (callers mutate it
    on reload) and a parsed snapshot of the model groups. Construction is
    cheap by design — callers build one per request rather than caching a
    stale view of config.
    """

    def __init__(
        self,
        *,
        model_aliases: dict[str, list[str]] | None = None,
        model_pools: dict[str, ModelPool] | None = None,
        model_groups: Iterable[ModelGroup] | None = None,
        pool_host_bindings: dict[tuple[str, str], str] | None = None,
    ) -> None:
        self.model_aliases = model_aliases if model_aliases is not None else {}
        # (group_name, backend_id) -> served upstream id chosen once per host
        # so pool overflow does not thrash VRAM by alternating models.
        self._pool_host_bindings = pool_host_bindings
        groups = [
            ModelGroup(
                name=name,
                hosts=tuple(pool.hosts),
                models=tuple(pool.models),
                enabled=pool.enabled,
            )
            for name, pool in (model_pools or {}).items()
        ]
        if model_groups:
            groups.extend(model_groups)
        self.groups: tuple[ModelGroup, ...] = tuple(groups)

    # -- alias / group inputs to the walk --------------------------------

    def _pools_containing(self, model: str) -> frozenset[str]:
        names = self.alias_names(model)
        pools: set[str] = set()
        for group in self.groups:
            if group.enabled and _walk(names, group.models) is not None:
                pools.add(group.name)
        return frozenset(pools)

    def _literal_pool_models(self, model: str) -> frozenset[str]:
        """Pool allowlist rows matched by the literal alias key only."""
        primary = self.alias_names(model)[0]
        matched: set[str] = set()
        for group in self.groups:
            if not group.enabled:
                continue
            for pool_model in group.models:
                if _walk([primary], [pool_model]) is not None:
                    matched.add(pool_model.casefold())
        return frozenset(matched)

    @staticmethod
    def _model_family(name: str) -> str:
        lowered = name.casefold()
        for token in (
            "gemma",
            "nemotron",
            "qwen",
            "llama",
            "bge",
            "deepseek",
            "mistral",
        ):
            if token in lowered:
                return token
        return lowered.split("-")[0].split(":")[0]

    def _alias_pool_bundle(self, requested: str) -> frozenset[str]:
        """Pool rows the requested alias key may route to (same-tier spellings)."""
        aliases = self.alias_names(requested)
        primary = aliases[0]
        bundle: set[str] = set()
        for group in self.groups:
            if not group.enabled:
                continue
            for pool_model in group.models:
                if _walk([primary], [pool_model]) is not None:
                    bundle.add(pool_model.casefold())
        for alias in aliases[1:]:
            if model_capability(primary) != model_capability(alias):
                continue
            if self._model_family(alias) != self._model_family(primary):
                continue
            for group in self.groups:
                if not group.enabled:
                    continue
                for pool_model in group.models:
                    if _walk([alias], [pool_model]) is not None:
                        bundle.add(pool_model.casefold())
        return frozenset(bundle)

    def _pool_tier_names(self, model: str) -> frozenset[str]:
        return self._alias_pool_bundle(model)

    def is_steering_alias(self, requested: str, alias_value: str) -> bool:
        """True when ``alias_value`` would steer a pool request off its tier."""
        if alias_value.casefold() == requested.casefold():
            return False
        if model_capability(requested) != model_capability(alias_value):
            return True
        value_models = self._literal_pool_models(alias_value)
        if not value_models:
            return False
        bundle = self._alias_pool_bundle(requested)
        if value_models <= bundle:
            return False
        return True

    def _alias_names_for_walk(self, requested: str, *, pool_literal: bool) -> list[str]:
        names = self.alias_names(requested)
        if not pool_literal or not self.request_in_enabled_group(requested):
            return names
        filtered = [names[0]]
        for alias in names[1:]:
            if not self.is_steering_alias(requested, alias):
                filtered.append(alias)
        return filtered

    def alias_names(self, model: str) -> list[str]:
        """Requested name plus configured aliases, request name first.

        Alias keys match case-insensitively so clients sending a
        differently-cased model name still resolve.
        """
        aliases = self.model_aliases.get(model)
        if aliases is None:
            folded = model.casefold()
            for key, ids in self.model_aliases.items():
                if key.casefold() == folded:
                    aliases = ids
                    break
        return [model, *(aliases or [])]

    @staticmethod
    def _host_matches(backend: _BackendLike, ref: str) -> bool:
        """Same ref forms as ``backend_by_id``: id, ``peer:<agent-id>``, bare
        agent_id, or base_url — so a group's ``hosts`` list can name a machine
        the same way the ``x-netllm-backend`` pin header does."""
        target = ref.strip()
        if not target:
            return False
        return (
            backend.id == target
            or backend.id == f"peer:{target}"
            or (backend.agent_id != "" and backend.agent_id == target)
            or backend.base_url.rstrip("/") == target.rstrip("/")
        )

    def request_in_enabled_group(self, model: str) -> bool:
        """True when ``model`` (or one of its aliases) is listed in any
        enabled ``routing.model_pools`` / group allowlist."""
        alias_names = self.alias_names(model)
        for group in self.groups:
            if group.enabled and _walk(alias_names, group.models) is not None:
                return True
        return False

    def group_models_for(self, backend: _BackendLike) -> list[str]:
        """Union of allowed models from every enabled group this backend
        belongs to, in group-declaration order. Empty when the backend is
        not a member of any enabled group.

        Overflow substitution must not use this union — see
        ``_authorizing_groups_for`` and ``_resolve_group_sticky``."""
        names: list[str] = []
        for group in self.groups:
            if not group.enabled:
                continue
            if not any(self._host_matches(backend, ref) for ref in group.hosts):
                continue
            for m in group.models:
                if m not in names:
                    names.append(m)
        return names

    def _authorizing_groups_for(
        self, requested: str, backend: _BackendLike
    ) -> list[ModelGroup]:
        """Enabled pools that list ``requested`` and include this backend."""
        pool_names = self._pools_containing(requested)
        if not pool_names:
            return []
        groups: list[ModelGroup] = []
        for group in self.groups:
            if not group.enabled or group.name not in pool_names:
                continue
            if not any(self._host_matches(backend, ref) for ref in group.hosts):
                continue
            groups.append(group)
        return groups

    def _resolve_group_sticky(
        self,
        requested: str,
        backend: _BackendLike,
        served: Sequence[str],
    ) -> tuple[str, str] | None:
        """Pick one catalog model per (pool, host) for overflow substitution.

        Only models from pools that list the *requested* name are eligible —
        a host in both a chat pool and an embedding pool must not substitute
        chat traffic onto embedding weights (or vice versa).
        """
        authorizing = self._authorizing_groups_for(requested, backend)
        if not authorizing:
            return None

        bindings = self._pool_host_bindings
        if bindings is not None:
            for group in authorizing:
                key = (group.name, backend.id)
                if key not in bindings:
                    continue
                bound = bindings[key]
                for served_id in served:
                    if served_id.casefold() == bound.casefold():
                        return served_id, STAGE_GROUP_EXACT
                del bindings[key]

        req_cap = model_capability(requested)
        matches: list[tuple[str, str, str]] = []
        seen: set[str] = set()
        for group in authorizing:
            for pool_model in group.models:
                if model_capability(pool_model) != req_cap:
                    continue
                hit = _walk(self.alias_names(pool_model), served)
                if hit is None:
                    continue
                served_id, arm = hit
                if model_capability(served_id) != req_cap:
                    continue
                key = served_id.casefold()
                if key in seen:
                    continue
                seen.add(key)
                matches.append((pool_model, served_id, arm))
        if not matches:
            return None

        matches.sort(key=lambda row: (row[1].casefold(), row[0].casefold()))
        _pool_model, served_id, arm = matches[0]
        if bindings is not None:
            for group in authorizing:
                bindings[(group.name, backend.id)] = served_id
        return served_id, f"group-{arm}"

    # -- the one walk -----------------------------------------------------

    def resolve(
        self,
        requested: str,
        backend: _BackendLike,
        *,
        served: Sequence[str] | None = None,
        allow_group_overflow: bool = True,
        pool_literal: bool = False,
    ) -> Resolution:
        """Resolve `requested` against one backend's catalog.

        `served` overrides the catalog read off the backend — candidacy has
        already read (and possibly re-probed) it, and must decide on the
        exact snapshot it saw rather than on a racing re-read.

        ``allow_group_overflow=False`` restricts the walk to alias arms only
        (request-aware pool phase 1). Phase 2 candidacy passes
        ``allow_group_overflow=True`` so pool members substitute only when
        no backend in the mesh serves the requested name literally.

        Never raises and never returns an empty name: an unmatched request
        passes the requested name through, exactly as the legacy invocation
        matcher did.
        """
        served = list(served) if served is not None else _served_models(backend)
        if not served:
            http_status = getattr(backend.health, "http_status", None)
            if backend.local and http_status in _AUTH_GATED_STATUSES:
                return Resolution(requested, requested, STAGE_AUTH_GATED)
            return Resolution(requested, requested, STAGE_BLIND_CATALOG)

        hit = _walk(
            self._alias_names_for_walk(requested, pool_literal=pool_literal),
            served,
        )
        if hit is not None:
            name, arm = hit
            return Resolution(requested, name, f"alias-{arm}")

        if allow_group_overflow:
            # A group authorises substitution *for its own models only*.
            # Without this membership guard the arm below walked the group
            # list against the catalog while ignoring ``requested`` entirely:
            # any backend that served a single group member became a
            # candidate for every model name in existence, including names
            # nothing in the mesh hosts. A one-model local backend listed in
            # a group alongside LAN peers therefore swallowed 100% of
            # traffic and answered under whatever name was asked for.
            hit = self._resolve_group_sticky(requested, backend, served)
            if hit is not None:
                name, stage = hit
                return Resolution(requested, name, stage)

        return Resolution(requested, requested, STAGE_PASSTHROUGH)

    def serves(
        self,
        requested: str,
        backend: _BackendLike,
        *,
        served: Sequence[str] | None = None,
        allow_group_overflow: bool = True,
        pool_literal: bool = False,
    ) -> bool:
        """Candidacy predicate, derived from the same walk as `resolve`."""
        return self.resolve(
            requested,
            backend,
            served=served,
            allow_group_overflow=allow_group_overflow,
            pool_literal=pool_literal,
        ).serves

    def upstream_model(
        self,
        requested: str,
        backend: _BackendLike,
        *,
        exact_model_only: bool = False,
        pool_literal: bool = False,
    ) -> str:
        """The model ID to actually send upstream to `backend`.

        Agent-hop requests (``exact_model_only=True``) skip pool/group
        substitution so the terminating peer invokes the forwarded model
        name literally.
        """
        use_pool_literal = pool_literal or (
            not exact_model_only and self.request_in_enabled_group(requested)
        )
        return self.resolve(
            requested,
            backend,
            allow_group_overflow=not exact_model_only,
            pool_literal=use_pool_literal,
        ).upstream_model

    # -- 404 hints --------------------------------------------------------

    def known_models(
        self,
        backends: Iterable[_BackendLike],
        *,
        limit: int = 25,
        capability: str | None = None,
    ) -> list[str]:
        """Distinct model IDs across enabled backends (for 404 messages)."""
        seen: dict[str, None] = {}
        for b in backends:
            if not getattr(b, "enabled", True):
                continue
            for m in _served_models(b):
                if capability is not None and model_capability(m) != capability:
                    continue
                seen.setdefault(m)
        return list(seen)[:limit]
