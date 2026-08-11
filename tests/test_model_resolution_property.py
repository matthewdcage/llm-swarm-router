"""F-25 Phase 8: property tests for ModelResolver with the LEGACY matchers
as oracles.

The two legacy matchers are reproduced verbatim below, frozen at the tree
they were deleted from (Phase 8b). They are the oracle: over generated
alias/pool/catalog corpora the unified resolver must agree with them, except
for the single declared divergence — **D18**, the group (``model_pools``)
invocation arm gaining the tag-prefix/casefold-tag-prefix matching that the
candidacy matcher always had.

Three properties:

1. ``test_candidacy_matches_legacy_oracle`` — per-backend ``ModelResolver.serves``
   with ``allow_group_overflow=True`` matches the legacy catch-all candidacy
   predicate (group overflow is deferred to ``RouterPool``'s two-phase
   collect — **D19**).
2. ``test_invocation_matches_legacy_oracle_modulo_d18`` — the invoked name
   equals the legacy one, or the difference is exactly D18-class: the walk
   landed on a ``group-*`` stage and the chosen name is one the backend
   actually serves.
3. ``test_resolver_is_internally_coherent`` — the F-25 invariant the legacy
   pair could not hold: if the resolver says a backend is a candidate for a
   name and that backend has a known catalog, the name it invokes is in that
   catalog. Asserted for the resolver, and shown to be violated by the legacy
   pair on the recorded trap fixture.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from netllm_core.model_resolution import ModelResolver
from netllm_core.models import Backend, BackendHealth, ModelPool

# --------------------------------------------------------------------------
# Frozen legacy oracles (RouterPool.model_names_for / _serves_model /
# pool_models_for_backend / resolve_via_pool, and
# AgentService._model_for_backend) — copied verbatim from the pre-Phase-8b
# tree. Do not "improve" these; their value is that they do not change.
# --------------------------------------------------------------------------


def legacy_model_names_for(aliases: dict[str, list[str]], model: str) -> list[str]:
    resolved = aliases.get(model)
    if resolved is None:
        folded = model.casefold()
        for key, ids in aliases.items():
            if key.casefold() == folded:
                resolved = ids
                break
    return [model, *(resolved or [])]


def legacy_serves_model(served: list[str], names: list[str]) -> bool:
    folded = [n.casefold() for n in names]
    return any(
        m == n or m.startswith(n + ":")
        for n in folded
        for m in (s.casefold() for s in served)
    )


def legacy_backend_matches_host_ref(backend: Backend, ref: str) -> bool:
    target = ref.strip()
    if not target:
        return False
    return (
        backend.id == target
        or backend.id == f"peer:{target}"
        or (backend.agent_id != "" and backend.agent_id == target)
        or backend.base_url.rstrip("/") == target.rstrip("/")
    )


def legacy_pool_models_for_backend(
    pools: dict[str, ModelPool], backend: Backend
) -> list[str]:
    names: list[str] = []
    for pool in pools.values():
        if not pool.enabled:
            continue
        if not any(legacy_backend_matches_host_ref(backend, ref) for ref in pool.hosts):
            continue
        for m in pool.models:
            if m not in names:
                names.append(m)
    return names


def legacy_pool_authorises(
    pool_models: list[str], aliases: dict[str, list[str]], requested_model: str
) -> bool:
    """D19: a pool substitutes only for models it lists.

    The original matchers walked the pool's model list against the catalog
    without ever consulting ``requested_model``, so a member serving any one
    pool model became a candidate for *every* name — including names no
    backend in the mesh hosts. The oracle carries the guard too, since the
    divergence is deliberate rather than drift.
    """
    names = legacy_model_names_for(aliases, requested_model)
    return legacy_serves_model(pool_models, names)


def legacy_resolve_via_pool(
    pools: dict[str, ModelPool],
    backend: Backend,
    requested_model: str,
    aliases: dict[str, list[str]] | None = None,
) -> str | None:
    pool_models = legacy_pool_models_for_backend(pools, backend)
    if not pool_models:
        return None
    if not legacy_pool_authorises(pool_models, aliases or {}, requested_model):
        return None
    served = backend.health.models
    for m in pool_models:
        if m in served:
            return m
    folded = {m.casefold() for m in pool_models}
    for served_id in served:
        if served_id.casefold() in folded:
            return served_id
    return None


def legacy_candidacy(
    aliases: dict[str, list[str]],
    pools: dict[str, ModelPool],
    backend: Backend,
    model: str,
) -> bool:
    """The name-level half of RouterPool.backends_for_model's collect().

    The enabled/local/allow_remote filters are selection policy, not name
    matching, and stay in the pool; everything below is matcher A.
    """
    served = backend.health.models
    if not served:
        if backend.local and backend.health.http_status in (401, 403):
            return False
        return True
    names = legacy_model_names_for(aliases, model)
    if legacy_serves_model(served, names):
        return True
    pool_models = legacy_pool_models_for_backend(pools, backend)
    return (
        bool(pool_models)
        and legacy_pool_authorises(pool_models, aliases, model)
        and legacy_serves_model(served, pool_models)
    )


def legacy_invocation(
    aliases: dict[str, list[str]],
    pools: dict[str, ModelPool],
    backend: Backend,
    model: str,
) -> str:
    """AgentService._model_for_backend, matcher B."""
    served = backend.health.models
    if not served:
        return model
    names = legacy_model_names_for(aliases, model)
    for name in names:
        if name in served:
            return name
    for name in names:
        for served_id in served:
            if served_id.startswith(name + ":"):
                return served_id
    folded = [n.casefold() for n in names]
    for name in folded:
        for served_id in served:
            sid = served_id.casefold()
            if sid == name or sid.startswith(name + ":"):
                return served_id
    pool_model = legacy_resolve_via_pool(pools, backend, model, aliases)
    if pool_model is not None:
        return pool_model
    return model


# --------------------------------------------------------------------------
# Corpus strategies. Deliberately tiny alphabets so collisions, case flips
# and tag-prefix relationships happen often instead of never.
# --------------------------------------------------------------------------

_BASES = st.sampled_from(["llama3", "Llama3", "qwen2", "QWEN2", "poolmodel", "gpt-4o"])
_TAGS = st.sampled_from(["", ":7b", ":70B", ":latest", ":8b-instruct"])
_HOSTS = st.sampled_from(["alpha", "peer:beta", "beta", "http://alpha/v1", "gamma"])

model_names = st.builds(lambda b, t: b + t, _BASES, _TAGS)


@st.composite
def corpora(draw: st.DrawFn) -> tuple[dict, dict, Backend, str]:
    served = draw(st.lists(model_names, min_size=0, max_size=4, unique=True))
    aliases = draw(
        st.dictionaries(
            model_names,
            st.lists(model_names, min_size=1, max_size=3),
            max_size=3,
        )
    )
    pools = draw(
        st.dictionaries(
            st.sampled_from(["lab", "big", "spill"]),
            st.builds(
                lambda enabled, hosts, models: ModelPool(
                    enabled=enabled, hosts=hosts, models=models
                ),
                st.booleans(),
                st.lists(_HOSTS, min_size=0, max_size=2, unique=True),
                st.lists(model_names, min_size=0, max_size=3, unique=True),
            ),
            max_size=2,
        )
    )
    backend = Backend(
        id=draw(st.sampled_from(["alpha", "peer:beta", "delta"])),
        base_url="http://alpha/v1",
        local=draw(st.booleans()),
        agent_id=draw(st.sampled_from(["", "beta", "zeta"])),
        health=BackendHealth(
            status="online",
            models=served,
            http_status=draw(st.sampled_from([None, 200, 401, 403])),
        ),
    )
    requested = draw(model_names)
    return aliases, pools, backend, requested


_SETTINGS = settings(
    max_examples=1500,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


@given(corpora())
@_SETTINGS
def test_candidacy_matches_legacy_oracle(case: tuple) -> None:
    aliases, pools, backend, requested = case
    resolver = ModelResolver(model_aliases=aliases, model_pools=pools)
    assert resolver.serves(
        requested, backend, allow_group_overflow=True
    ) == legacy_candidacy(aliases, pools, backend, requested), (
        "per-backend candidacy with group overflow must match legacy matcher A"
    )


@given(corpora())
@_SETTINGS
def test_invocation_matches_legacy_oracle_modulo_d18(case: tuple) -> None:
    aliases, pools, backend, requested = case
    resolver = ModelResolver(model_aliases=aliases, model_pools=pools)
    resolution = resolver.resolve(requested, backend)
    legacy = legacy_invocation(aliases, pools, backend, requested)
    if resolution.upstream_model == legacy:
        return
    # The one declared divergence class (D18): the group arm now matches
    # with the same three arms candidacy always used, so it lands on a
    # served ID where matcher B fell through (or picked a different one).
    assert resolution.stage.startswith("group-"), (
        f"undeclared divergence at stage {resolution.stage}: "
        f"legacy={legacy!r} unified={resolution.upstream_model!r} "
        f"served={backend.health.models} aliases={aliases}"
    )
    assert resolution.upstream_model in backend.health.models, (
        "the resolver must only ever invoke a name the backend serves"
    )


@given(corpora())
@_SETTINGS
def test_resolver_is_internally_coherent(case: tuple) -> None:
    """The F-25 invariant: one walk answers candidacy and invocation, so a
    selected backend is always invoked with a name it advertises."""
    aliases, pools, backend, requested = case
    resolver = ModelResolver(model_aliases=aliases, model_pools=pools)
    resolution = resolver.resolve(requested, backend)
    if not resolution.serves or not backend.health.models:
        return
    if resolution.stage == "blind-catalog":
        return
    assert resolution.upstream_model in backend.health.models


def test_legacy_pair_violates_coherence_on_the_recorded_trap() -> None:
    """The fixture behind naming-f25-trap-pool-tag-prefix-disagree, as a
    unit: matcher A admits the host, matcher B sends it a name it does not
    serve. This is what D18 retires."""
    # "anything-goes" is listed so D19's membership guard admits the request:
    # the trap recorded here is the tag-prefix disagreement between the two
    # legacy matchers, not the old catch-all that D19 removed.
    pools = {
        "lab": ModelPool(
            enabled=True,
            hosts=["http://alpha.farm/v1"],
            models=["poolmodel", "anything-goes"],
        )
    }
    backend = Backend(
        id="alpha",
        base_url="http://alpha.farm/v1",
        health=BackendHealth(status="online", models=["poolmodel:7b"]),
    )
    assert legacy_candidacy({}, pools, backend, "anything-goes") is True
    assert legacy_invocation({}, pools, backend, "anything-goes") == "anything-goes"

    resolver = ModelResolver(model_pools=pools)
    resolution = resolver.resolve("anything-goes", backend)
    assert resolution.serves is True
    assert resolution.stage == "group-tag-prefix"
    assert resolution.upstream_model == "poolmodel:7b"
