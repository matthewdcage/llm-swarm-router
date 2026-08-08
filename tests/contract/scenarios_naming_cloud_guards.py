"""Phase 0b contract vectors: model naming, cloud topology, guards/policy.

Scenario definitions for the vectors under
``tests/contract/vectors/naming-cloud-guards/<group>/*.json``. Every doc is
built here in Python and materialized to JSON by ``record()``; the checked-in
JSON is the contract, this module is how it was authored.

Three groups (plan-f24-f26.md §2, Phase 0b):

- ``naming`` — routing.model_aliases (exact / tag-prefix / casefold),
  routing.model_pools, sources[].model_rewrites stacked with
  sources[].scenarios[].model, requested-name restore on every response
  shape, and one adversarial alias/pool set where the pool's *candidate*
  matcher (``RouterPool._serves_model`` via ``backends_for_model``) and the
  service's *invocation* matcher (``AgentService._model_for_backend`` →
  ``RouterPool.resolve_via_pool``) disagreed. That pair was the F-25
  regression trap, recorded as-is in Phase 0 and **retired as D18 in Phase
  8b**, where both matchers were replaced by the one
  ``netllm_core.model_resolution.ModelResolver`` walk.
- ``cloud`` — cloud.fallback ordering (``cloud`` vs ``local`` vs ``none``),
  the master switch, materialized ``cloud-<id>`` rows, the legacy
  request-scoped rows built from caller credentials, and the Messages
  anthropic-format fallback tier.
- ``guards`` — dialect-typed 400s from the non-chat capability guard on
  every chat/messages surface, the (still absent) embeddings guard, and
  scenario classification per surface.

Divergence annotations
----------------------
``divergence`` lists behavior-matrix.md IDs D1–D16 that a vector's recorded
cells embody. Vectors whose behavior the F-30..F-48 remediation already
fixed carry ``[]`` — they pin the fixed behavior. Findings that were real but
had no D-number when recorded (F-25's matcher split) were carried in a
non-schema ``note`` field instead of being mis-annotated; the matcher split
gained its number, D18, only when Phase 8b actually changed the behavior.
(The RESP-S success-accounting hole is no longer one of them: it is D16,
pinned by the ``*-responses-s`` 200-vectors in ``scenarios_streaming_errors``.)

Every vector also runs through ``anticollapse.assert_no_unintended_keyless_
401`` at record time and ``assert_corpus_has_no_unintended_keyless_401`` at
replay time, so a scenario that never reaches the farm cannot masquerade as
a recorded behavior.

Running
-------
``pytest`` does not auto-collect this module (its name does not match
``python_files``), and the Phase 0a runner globs ``vectors/*.json``
non-recursively, so it does not pick up this subtree either. Drive it
explicitly:

    uv run pytest tests/contract/scenarios_naming_cloud_guards.py
    NETLLM_VECTOR_RECORD=1 uv run pytest \\
        tests/contract/scenarios_naming_cloud_guards.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from anticollapse import (
    assert_corpus_has_no_unintended_keyless_401,
    assert_no_unintended_keyless_401,
)
from canonical import canonicalize_result
from drivers import contract_environment
from netllm_core.models import OPENAI_CLOUD_BASE_URL

GROUP_DIR = Path(__file__).resolve().parent / "vectors" / "naming-cloud-guards"
RECORD = os.environ.get("NETLLM_VECTOR_RECORD") == "1"

# D18 (Phase 8b, F-25 matcher unification) is the highest ID this
# group carries; the shared lint in test_divergence_lint.py owns the
# repo-wide range.
VALID_DIVERGENCE_IDS = {f"D{i}" for i in range(1, 19)}
GROUPS = ("naming", "cloud", "guards")

# Farm hosts (farm.FarmBackend.base_url): openai rows carry "/v1", the
# anthropic surface carries the bare root.
ALPHA = "http://alpha.farm/v1"
BETA = "http://beta.farm/v1"
CLOUDO = "http://cloudo.farm/v1"
ANT = "http://ant.farm"

USER = [{"role": "user", "content": "hi"}]


def _chat(model: str, **kw: Any) -> dict[str, Any]:
    return {"model": model, "messages": USER, **kw}


def _msg(model: str, **kw: Any) -> dict[str, Any]:
    return {"model": model, "max_tokens": 16, "messages": USER, **kw}


def _backend(name: str, models: list[str], **kw: Any) -> dict[str, Any]:
    return {"name": name, "models": models, **kw}


def _fail(status: int = 503) -> list[dict[str, Any]]:
    return [{"behavior": "http", "status": status}]


def vector(
    group: str,
    vid: str,
    *,
    scenario: dict[str, Any],
    request: dict[str, Any],
    divergence: list[str] | None = None,
    note: str = "",
) -> tuple[str, dict[str, Any]]:
    doc: dict[str, Any] = {
        "id": vid,
        "divergence": divergence or [],
        "note": note,
        "scenario": scenario,
        "request": request,
    }
    return group, doc


# ---------------------------------------------------------------------------
# Group 1 — model naming
# ---------------------------------------------------------------------------

# One source used by the rewrite/scenario-stacking vectors: a request-name
# rewrite plus a "think"-scenario hard override on top of it.
REWRITE_SOURCE = {
    "sources": [
        {
            "id": "cli",
            "model_rewrites": {"req-name": "served-a"},
            "scenarios": {"think": {"model": "scenario-model"}},
        }
    ]
}

# Restore matrix: one alias, one served id, every response shape.
RESTORE_ALIASES = {"routing": {"model_aliases": {"canon": ["served-a"]}}}


def _naming() -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    g = "naming"

    out.append(
        vector(
            g,
            "naming-alias-exact",
            scenario={
                "backends": [_backend("alpha", ["farm-chat-a", "farm-chat-b"])],
                "routing": {"model_aliases": {"canon": ["farm-chat-a"]}},
            },
            request={"path": "chat_ns", "body": _chat("canon")},
            note="alias exact match wins before any prefix match",
        )
    )
    out.append(
        vector(
            g,
            "naming-alias-tag-prefix",
            scenario={
                "backends": [_backend("alpha", ["qwen3:8b"])],
                "routing": {"model_aliases": {"qwen": ["qwen3"]}},
            },
            request={"path": "chat_ns", "body": _chat("qwen")},
            note="Ollama-style name:tag — the full served id goes upstream",
        )
    )
    out.append(
        vector(
            g,
            "naming-alias-casefold",
            scenario={"backends": [_backend("alpha", ["MiXeD-Case-Model"])]},
            request={"path": "chat_ns", "body": _chat("mixed-case-model")},
            note="casefold arm returns the served id's exact casing",
        )
    )
    out.append(
        vector(
            g,
            "naming-model-pools-catchall",
            scenario={
                "backends": [_backend("alpha", ["pool-model"])],
                "routing": {
                    "model_pools": {
                        "lab": {
                            "hosts": [ALPHA],
                            "models": ["pool-model"],
                            "enabled": True,
                        }
                    }
                },
            },
            request={"path": "chat_ns", "body": _chat("anything-goes")},
            note="pool member is a candidate for ANY name; requested name discarded",
        )
    )
    out.append(
        vector(
            g,
            "naming-model-pools-disabled",
            scenario={
                "backends": [_backend("alpha", ["pool-model"])],
                "routing": {
                    "model_pools": {
                        "lab": {
                            "hosts": [ALPHA],
                            "models": ["pool-model"],
                            "enabled": False,
                        }
                    }
                },
            },
            request={"path": "chat_ns", "body": _chat("anything-goes")},
            note="disabled pool: no catch-all, falls through to the 404 hint",
        )
    )
    out.append(
        vector(
            g,
            "naming-model-pools-isolation-multi-host",
            scenario={
                "backends": [
                    _backend("alpha", ["gemma4:26b"]),
                    _backend("beta", ["qwen3-next-80b"]),
                ],
                "routing": {
                    "model_pools": {
                        "mixed": {
                            "hosts": [ALPHA, BETA],
                            "models": ["gemma4:26b", "qwen3-next-80b"],
                            "enabled": True,
                        }
                    }
                },
            },
            request={"path": "chat_ns", "body": _chat("qwen3-next-80b")},
            note=(
                "request-aware pool: literal model on qwen-host must not "
                "overflow to gemma-host"
            ),
        )
    )

    # -- rewrites + scenario stacking ---------------------------------------
    rewrite_scn = {
        "backends": [_backend("alpha", ["served-a", "scenario-model"])],
        "routing": REWRITE_SOURCE,
    }
    out.append(
        vector(
            g,
            "naming-rewrite-source-default-scenario",
            scenario=rewrite_scn,
            request={
                "path": "chat_ns",
                "headers": {"x-netllm-source": "cli"},
                "body": _chat("req-name"),
            },
            divergence=["D10"],
            note=(
                "source model_rewrites resolves req-name -> served-a, but the chat "
                "path leaves the payload immutable and only rewrites when the "
                "per-backend id differs from the canonical name — so the ORIGINAL "
                "request name goes on the wire and the rewrite is a no-op "
                "upstream. Messages mutates upfront and does not have this hole "
                "(see naming-rewrite-messages-ns-on-wire)."
            ),
        )
    )
    out.append(
        vector(
            g,
            "naming-rewrite-plus-scenario-model-think",
            scenario=rewrite_scn,
            request={
                "path": "chat_ns",
                "headers": {"x-netllm-source": "cli"},
                "body": _chat("req-name", reasoning_effort="high"),
            },
            divergence=["D10"],
            note=(
                "scenario 'think' stacks its hard model override on top of the "
                "rewrite (scenario_counts cli:think); same payload-immutability "
                "hole means scenario-model never reaches the wire"
            ),
        )
    )
    out.append(
        vector(
            g,
            "naming-rewrite-messages-ns-on-wire",
            scenario={
                "backends": [_backend("alpha", ["served-a"])],
                "routing": {"sources": REWRITE_SOURCE["sources"]},
            },
            request={
                "path": "messages_ns",
                "headers": {"x-netllm-source": "cli"},
                "body": _msg("req-name"),
            },
            divergence=["D10"],
            note="messages mutates payload['model'] upfront: served-a IS on the wire",
        )
    )

    out.append(
        vector(
            g,
            "naming-cross-alias-retry-messages-ns",
            scenario={
                "backends": [
                    _backend(
                        "ant",
                        ["served-a"],
                        api_format="anthropic",
                        script=_fail(503),
                    ),
                    _backend("ant2", ["served-b"], api_format="anthropic"),
                ],
                "routing": {
                    "model_aliases": {"canon": ["served-a", "served-b"]},
                    "default_strategy": "failover",
                },
            },
            request={
                "path": "messages_ns",
                "headers": {"x-api-key": "caller"},
                "body": _msg("canon"),
            },
            divergence=["D10"],
            note=(
                "THE D10 vector. One canonical name, two anthropic-format "
                "backends serving it under DIFFERENT ids. Attempt 1 (ant) must "
                "carry 'served-a' and attempt 2 (ant2) 'served-b'. Under the "
                "old up-front payload mutation both attempts carried the same "
                "name, so the retry asked ant2 for a model it does not serve. "
                "The response body reports 'canon' either way."
            ),
        )
    )

    # -- requested-name restore across every response shape ------------------
    alias_openai = {
        "backends": [_backend("alpha", ["served-a"])],
        **RESTORE_ALIASES,
    }
    alias_anthropic = {
        "backends": [_backend("ant", ["served-a"], api_format="anthropic")],
        **RESTORE_ALIASES,
    }
    out.append(
        vector(
            g,
            "naming-restore-chat-ns",
            scenario=alias_openai,
            request={"path": "chat_ns", "body": _chat("canon")},
        )
    )
    out.append(
        vector(
            g,
            "naming-restore-chat-s",
            scenario=alias_openai,
            request={"path": "chat_s", "body": _chat("canon")},
            note="OpenAI SSE dialect: every chunk's model rewritten back to canon",
        )
    )
    out.append(
        vector(
            g,
            "naming-restore-emb",
            scenario={
                "backends": [_backend("alpha", ["bge-served"])],
                "routing": {"model_aliases": {"canon-embed": ["bge-served"]}},
            },
            request={"path": "emb", "body": {"model": "canon-embed", "input": "hi"}},
        )
    )
    out.append(
        vector(
            g,
            "naming-restore-responses-ns",
            scenario=alias_openai,
            request={"path": "responses_ns", "body": {"model": "canon", "input": "hi"}},
            note="Responses bridge inherits the chat restore verbatim",
        )
    )
    out.append(
        vector(
            g,
            "naming-restore-messages-ns-translated",
            scenario=alias_openai,
            request={"path": "messages_ns", "body": _msg("canon")},
            divergence=["D10"],
            note=(
                "M-NS via the openai translation arm. BASELINE: the response "
                "body reported the UPSTREAM served id (served-a), not the "
                "requested name — openai_to_anthropic_response prefers "
                "payload['model'], and the _messages_attempt restore only "
                "fired when requested_model != the canonical model, which it "
                "is not here. PHASE 4b [D10]: the restore now compares the "
                "name that actually came back against requested_model (what "
                "chat has always done), so the body reports 'canon'. Claimed "
                "under D10 because it is the same payload/model-timing cell: "
                "the old condition was only ever correct while the Messages "
                "surfaces mutated payload['model'] up-front."
            ),
        )
    )
    out.append(
        vector(
            g,
            "naming-restore-messages-ns-anthropic-arm",
            scenario=alias_anthropic,
            request={
                "path": "messages_ns",
                "headers": {"x-api-key": "caller-key"},
                "body": _msg("canon"),
            },
            divergence=["D10"],
            note=(
                "BASELINE: the anthropic arm sent the payload model as-is "
                "('canon'), with no alias resolution — it relied on the "
                "caller having mutated payload['model'] up-front. PHASE 4b "
                "[D10]: the payload is immutable and the arm resolves the "
                "served id per backend like every other surface, so the wire "
                "carries 'served-a'. The response body still reports 'canon' "
                "(the restore fix in the same commit)."
            ),
        )
    )
    out.append(
        vector(
            g,
            "naming-restore-messages-s-anthropic-arm",
            scenario=alias_anthropic,
            request={
                "path": "messages_s",
                "headers": {"x-api-key": "caller-key"},
                "body": _msg("canon"),
            },
            divergence=["D9", "D10"],
            note=(
                "Anthropic SSE dialect. PHASE 4b [D10] made the anthropic arm "
                "resolve the served id per backend, so 'served-a' goes on the "
                "wire (upstream_calls still show it) and message_start briefly "
                "carried it too — M-S was the one surface with no restore "
                "step, so the moment the wire name stopped being the canonical "
                "name the client saw the difference. PHASE 7 [D9] adds the "
                "restore: message_start carries 'canon' again while the "
                "upstream call is unchanged. D10's cell did not move."
            ),
        )
    )
    out.append(
        vector(
            g,
            "naming-restore-messages-s-translated",
            scenario=alias_openai,
            request={"path": "messages_s", "body": _msg("canon")},
            divergence=["D9"],
            note=(
                "translated Anthropic SSE: the bridge copies the upstream "
                "chunk's own model into message_start, so 'served-a' appeared "
                "there. [D9] Phase 7 restores 'canon' — and does it by value, "
                "not by comparing the adapter's resolved name, precisely "
                "because this arm's wire name is the bridge's choice"
            ),
        )
    )
    out.append(
        vector(
            g,
            "naming-restore-messages-s-rewritten-name",
            scenario={
                "backends": [_backend("ant", ["served-a"], api_format="anthropic")],
                "routing": {"sources": REWRITE_SOURCE["sources"]},
            },
            request={
                "path": "messages_s",
                "headers": {"x-netllm-source": "cli", "x-api-key": "caller-key"},
                "body": _msg("req-name"),
            },
            divergence=["D9", "D10"],
            note=(
                "source rewrite + M-S: [D9] Phase 7 — the client sees the name "
                "it sent, req-name, while served-a stays on the upstream call"
            ),
        )
    )

    # -- F-25 adversarial pair ----------------------------------------------
    pool_tag = {
        "model_pools": {
            "lab": {"hosts": [ALPHA], "models": ["poolmodel"], "enabled": True}
        }
    }
    out.append(
        vector(
            g,
            "naming-f25-trap-pool-tag-prefix-disagree",
            scenario={
                "backends": [_backend("alpha", ["poolmodel:7b"])],
                "routing": pool_tag,
            },
            request={"path": "chat_ns", "body": _chat("anything-goes")},
            divergence=["D18"],
            note=(
                "F-25 REGRESSION TRAP — RETIRED in plan §3 Phase 8b as [D18]. "
                "Originally: matcher A (RouterPool._serves_model, used by "
                "backends_for_model) accepted the host because served "
                "'poolmodel:7b' tag-prefix-matches pool model 'poolmodel', "
                "while matcher B (RouterPool.resolve_via_pool, reached from "
                "AgentService._model_for_backend) had NO tag-prefix arm — exact "
                "and casefold-exact only — so it returned None and the "
                "requested name 'anything-goes' went to a host that does not "
                "serve it. ModelResolver runs one walk for both questions, so "
                "the group arm now matches with the same three arms candidacy "
                "always used and the upstream call carries 'poolmodel:7b'. "
                "This vector is now byte-identical to the alias control below "
                "in everything but its routing block — which is the point."
            ),
        )
    )
    out.append(
        vector(
            g,
            "naming-f25-trap-alias-tag-prefix-agree",
            scenario={
                "backends": [_backend("alpha", ["poolmodel:7b"])],
                "routing": {
                    "model_aliases": {"anything-goes": ["poolmodel"]},
                    **pool_tag,
                },
            },
            request={"path": "chat_ns", "body": _chat("anything-goes")},
            # NOTE TEXT IS FROZEN. This vector is D18's control: it must
            # stay byte-identical across the Phase 8b switch, and a note
            # edit would destroy exactly that proof. The now-historical
            # wording is superseded by the trap vector's note above and by
            # behavior-matrix D18.
            note=(
                "control for the trap above: add the SAME name to "
                "model_aliases and the two matchers agree, because "
                "_model_for_backend's alias arm DOES support tag-prefix. The "
                "delta between these two vectors is exactly the A/B split."
            ),
        )
    )
    return out


# ---------------------------------------------------------------------------
# Group 2 — cloud topology
# ---------------------------------------------------------------------------

# Pool membership is decoupled from farm membership by overriding
# routing.backends: 'cloudo' stays a reachable farm host but is NOT a pool
# row, so the only way it can be routed to is the materialized cloud-openai
# row that [cloud.providers.openai] creates.
LOCAL_ROW = {
    "base_url": ALPHA,
    "provider": "custom",
    "api_format": "openai",
    "api_key": "farm-key",
    "enabled": True,
    "local": True,
}
CLOUD_PROVIDERS = {
    "openai": {
        "enabled": True,
        "base_url": CLOUDO,
        "api_key": "cloud-key",
        "models": ["shared"],
    }
}
SHARED_FARM = [_backend("alpha", ["shared"]), _backend("cloudo", ["shared"])]


def _cloud_scenario(fallback: str, *, enabled: bool = True) -> dict[str, Any]:
    return {
        "backends": SHARED_FARM,
        "routing": {"backends": [LOCAL_ROW], "default_strategy": "failover"},
        "cloud": {
            "enabled": enabled,
            "fallback": fallback,
            "providers": CLOUD_PROVIDERS,
        },
    }


def _cloud() -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    g = "cloud"

    out.append(
        vector(
            g,
            "cloud-fallback-cloud-local-leads",
            scenario=_cloud_scenario("cloud"),
            request={"path": "chat_ns", "body": _chat("shared")},
            note="default fallback='cloud': local mesh first, cloud is the tier after",
        )
    )
    out.append(
        vector(
            g,
            "cloud-fallback-local-cloud-leads",
            scenario=_cloud_scenario("local"),
            request={"path": "chat_ns", "body": _chat("shared")},
            note=(
                "fallback='local' => ResolvedRouting.cloud_leads => prefer_cloud "
                "orders the materialized cloud-openai row ahead of the local row"
            ),
        )
    )
    out.append(
        vector(
            g,
            "cloud-fallback-none-not-materialized",
            scenario=_cloud_scenario("none"),
            request={"path": "chat_ns", "body": _chat("shared")},
            note="fallback='none': allow_cloud_inject off, cloud row never appears",
        )
    )
    out.append(
        vector(
            g,
            "cloud-master-switch-off",
            scenario=_cloud_scenario("local", enabled=False),
            request={"path": "chat_ns", "body": _chat("shared")},
            note="cloud.enabled=false outranks fallback='local' entirely",
        )
    )
    out.append(
        vector(
            g,
            "cloud-materialized-row-sole-candidate",
            scenario={
                "backends": [
                    _backend("alpha", ["local-only-model"]),
                    _backend("cloudo", ["cloud-model"]),
                ],
                "routing": {"backends": [LOCAL_ROW], "default_strategy": "failover"},
                "cloud": {
                    "enabled": True,
                    "fallback": "cloud",
                    "providers": {
                        "openai": {
                            "enabled": True,
                            "base_url": CLOUDO,
                            "api_key": "cloud-key",
                            "models": ["cloud-model"],
                        }
                    },
                },
            },
            request={"path": "chat_ns", "body": _chat("cloud-model")},
            note=(
                "the cloud-<id> row's configured `models` list becomes its "
                "health.models allowlist, so it is the only candidate"
            ),
        )
    )
    out.append(
        vector(
            g,
            "cloud-local-only-header-suppresses-cloud",
            scenario=_cloud_scenario("local"),
            request={
                "path": "chat_ns",
                "headers": {"x-netllm-local-only": "1"},
                "body": _chat("shared"),
            },
            note="x-netllm-local-only is an absolute ceiling: clears cloud_leads too",
        )
    )
    out.append(
        vector(
            g,
            "cloud-legacy-openai-row-health-gated",
            scenario={
                "backends": [_backend("alpha", ["farm-chat"])],
                "routing": {"default_strategy": "failover"},
                "cloud": {"enabled": True, "fallback": "local"},
            },
            request={
                "path": "chat_ns",
                "headers": {"authorization": "Bearer sk-caller-key"},
                "body": _chat("farm-chat"),
            },
            divergence=["D6"],
            note=(
                "legacy request-scoped openai-cloud row (built from the caller's "
                "Authorization) rides as extra_candidates and competes in "
                "strategy selection — but backends_for_model prefers healthy "
                "candidates, and api.openai.com is unreachable from the farm, so "
                "even with cloud_leads=True the local row serves. The legacy row "
                "never reaches the wire on OpenAI paths."
            ),
        )
    )
    out.append(
        vector(
            g,
            "cloud-legacy-openai-row-earns-its-attempt",
            scenario={
                "backends": [
                    _backend("alpha", ["farm-chat"], script=_fail(500)),
                    # A farm host standing in for api.openai.com, deliberately
                    # NOT in routing.backends: the only way the request-scoped
                    # legacy row is both injected AND reachable, so its
                    # attempt is observable instead of health-gated away.
                    _backend(
                        "oaicloud",
                        ["farm-chat"],
                        url=OPENAI_CLOUD_BASE_URL,
                        configured=False,
                        local=False,
                    ),
                ],
                "routing": {"default_strategy": "failover"},
                "cloud": {"enabled": True, "fallback": "cloud"},
            },
            request={
                "path": "chat_ns",
                "headers": {"authorization": "Bearer sk-caller-key"},
                "body": _chat("farm-chat"),
            },
            divergence=["D6", "D7"],
            note=(
                "[D7] the attempt cap used to be max(len(pool.backends), 1) = 1, "
                "so the injected legacy cloud row could never be tried after the "
                "one pool row failed — the request-scoped candidate was counted "
                "by selection but not by the budget. max_attempts is now the sum "
                "over the schedule (1 pool row + 1 extra_candidate = 2), so "
                "attempt 2 reaches the cloud row and it serves the request. Note "
                "the wire evidence: the second call carries the CALLER's key "
                "(Bearer sk-caller-key), not the farm key — it really is the "
                "legacy request-scoped row (F-04), not a pool row."
            ),
        )
    )
    out.append(
        vector(
            g,
            "cloud-legacy-anthropic-row-fallback-tier",
            scenario={
                "backends": [_backend("alpha", ["farm-chat"], script=_fail(503))],
                "routing": {"default_strategy": "failover"},
                "cloud": {"enabled": True, "fallback": "cloud"},
            },
            request={
                "path": "messages_ns",
                "headers": {"x-api-key": "sk-ant-caller"},
                "body": _msg("farm-chat"),
            },
            divergence=["D6", "D7"],
            note=(
                "the legacy anthropic-cloud row IS reached, because the messages "
                "fallback tier bypasses both the health filter and the attempt "
                "cap: max_attempts = len(pool.backends) = 1, yet attempt 2 runs. "
                "api.anthropic.com is unreachable here, so the recorded outcome "
                "is the deterministic connect-error 502."
            ),
        )
    )
    out.append(
        vector(
            g,
            "cloud-anthropic-fallback-tier-messages-ns",
            scenario={
                "backends": [
                    _backend("alpha", ["farm-chat"], script=_fail(503)),
                    _backend("ant", ["farm-chat"], api_format="anthropic"),
                ],
                "routing": {"default_strategy": "failover"},
            },
            request={
                "path": "messages_ns",
                "headers": {"x-api-key": "caller"},
                "body": _msg("farm-chat"),
            },
            divergence=["D6", "D8"],
            note=(
                "anthropic-format pool rows are pre-seeded into `tried` (format "
                "filter), excluded from the strategy loop, then tried as an "
                "ordered final tier once openai candidates are exhausted"
            ),
        )
    )
    out.append(
        vector(
            g,
            "cloud-anthropic-fallback-tier-messages-s",
            scenario={
                "backends": [
                    _backend("alpha", ["farm-chat"], script=_fail(503)),
                    _backend("ant", ["farm-chat"], api_format="anthropic"),
                ],
                "routing": {"default_strategy": "failover"},
            },
            request={
                "path": "messages_s",
                "headers": {"x-api-key": "caller"},
                "body": _msg("farm-chat"),
            },
            divergence=["D6", "D8"],
            note=(
                "same tier via M-S's bespoke candidates_exhausted/fallback_iter "
                "state machine; failure happened before any bytes, so the "
                "second backend's stream replaces it cleanly"
            ),
        )
    )
    out.append(
        vector(
            g,
            "cloud-anthropic-fallback-tier-ordering",
            scenario={
                "backends": [
                    _backend("alpha", ["farm-chat"], script=_fail(503)),
                    _backend(
                        "ant", ["farm-chat"], api_format="anthropic", script=_fail(503)
                    ),
                    _backend("ant2", ["farm-chat"], api_format="anthropic"),
                ],
                "routing": {"default_strategy": "failover"},
            },
            request={
                "path": "messages_ns",
                "headers": {"x-api-key": "caller"},
                "body": _msg("farm-chat"),
            },
            divergence=["D6"],
            note="fallback tier is ORDERED (config order), not strategy-selected",
        )
    )
    out.append(
        vector(
            g,
            "cloud-messages-keyless-exhaustion-401",
            scenario={
                "backends": [_backend("alpha", ["other-model"])],
                "routing": {"default_strategy": "failover"},
            },
            request={"path": "messages_ns", "body": _msg("nope-model")},
            divergence=["D11"],
            note=(
                "messages exhaustion with no candidates and no key is a 401, "
                "where the OpenAI paths raise the 404 model-not-found hint"
            ),
        )
    )
    return out


# ---------------------------------------------------------------------------
# Group 3 — guards and policy
# ---------------------------------------------------------------------------

GUARD_FARM = {"backends": [_backend("alpha", ["farm-chat", "bge-m3"])]}


def _guards() -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    g = "guards"

    for path, body, dialect in (
        ("chat_ns", _chat("bge-m3"), "openai"),
        ("chat_s", _chat("bge-m3"), "openai"),
        ("responses_ns", {"model": "bge-m3", "input": "hi"}, "openai"),
        ("messages_ns", _msg("bge-m3"), "anthropic"),
        ("messages_s", _msg("bge-m3"), "anthropic"),
    ):
        out.append(
            vector(
                g,
                f"guards-non-chat-model-400-{path.replace('_', '-')}",
                scenario=GUARD_FARM,
                request={"path": path, "body": body},
                note=(
                    f"{dialect}-typed 400 raised pre-routing: zero upstream "
                    "calls, source counted but never admitted (source_in_flight "
                    "has no entry). Post-F-32 the stream paths return a real "
                    "400, not a 200 with an aborted body."
                ),
            )
        )

    out.append(
        vector(
            g,
            "guards-emb-chat-model-unguarded",
            scenario=GUARD_FARM,
            request={"path": "emb", "body": {"model": "farm-chat", "input": "hi"}},
            divergence=["D4"],
            note=(
                "BASELINE: no capability guard on /v1/embeddings — a chat "
                "model routed and got a 200 full of nonsense. PHASE 4c [D4]: "
                "the guard is a plan step on every surface now, so this is an "
                "OpenAI-typed 400 with zero upstream calls, source counted but "
                "never admitted — the same shape the chat and Messages guards "
                "have always produced."
            ),
        )
    )
    out.append(
        vector(
            g,
            "guards-emb-chat-model-burns-retry-budget",
            scenario={
                "backends": [
                    _backend("alpha", ["farm-chat"], script=_fail(500)),
                    _backend("beta", ["farm-chat"], script=_fail(500)),
                ]
            },
            request={"path": "emb", "body": {"model": "farm-chat", "input": "hi"}},
            divergence=["D4"],
            note=(
                "BASELINE: the concrete cost of the missing guard — the whole "
                "retry budget spent on a model that can never embed, ending "
                "in a 502. PHASE 4c [D4]: rejected before the loop, "
                "upstream_calls == [] and both backends untouched. This "
                "vector is the reason the guard exists."
            ),
        )
    )
    out.append(
        vector(
            g,
            "guards-emb-embedding-model-ok",
            scenario=GUARD_FARM,
            request={"path": "emb", "body": {"model": "bge-m3", "input": "hi"}},
        )
    )
    rewrite_guard_scenario = {
        **GUARD_FARM,
        "routing": {
            "sources": [
                {
                    "id": "default",
                    "model_rewrites": {"public-embed": "bge-m3"},
                }
            ]
        },
    }
    out.append(
        vector(
            g,
            "guards-rewrite-capability-400-chat-s",
            scenario=rewrite_guard_scenario,
            request={"path": "chat_s", "body": _chat("public-embed")},
            note=(
                "F-57: capability guard classifies the rewritten id (bge-m3) "
                "but the 400 quotes the caller's name (public-embed)."
            ),
        )
    )
    out.append(
        vector(
            g,
            "guards-emb-anthropic-rows-excluded",
            scenario={
                "backends": [
                    _backend("ant", ["bge-m3"], api_format="anthropic"),
                    _backend("alpha", ["bge-m3"]),
                ],
                "routing": {"default_strategy": "failover"},
            },
            request={
                "path": "emb",
                "headers": {"x-api-key": "k"},
                "body": {"model": "bge-m3", "input": "hi"},
            },
            divergence=["D8"],
            note="anthropic pool rows pre-seeded into `tried`: no embeddings there",
        )
    )
    out.append(
        vector(
            g,
            "guards-scenario-emb-classified-as-chat",
            scenario={
                "backends": [_backend("alpha", ["bge-m3", "scenario-embed"])],
                "routing": {
                    "sources": [
                        {
                            "id": "cli",
                            "scenarios": {"think": {"model": "scenario-embed"}},
                        }
                    ]
                },
            },
            request={
                "path": "emb",
                "headers": {"x-netllm-source": "cli"},
                "body": {"model": "bge-m3", "input": "hi", "reasoning_effort": "high"},
            },
            divergence=["D14", "D10"],
            note=(
                "embeddings traffic is classified by the CHAT classifier with "
                "api_format='openai', so a chat-shaped scenario rule fires "
                "(scenario_counts cli:think) and swaps the model for embedding "
                "traffic. The swap then never reaches the wire (payload "
                "immutability, D10), so the damage is confined to candidacy "
                "and counters — for now."
            ),
        )
    )
    out.append(
        vector(
            g,
            "guards-scenario-rule-surface-qualified-skips-emb",
            scenario={
                "backends": [_backend("alpha", ["bge-m3", "scenario-embed"])],
                "routing": {
                    "sources": [
                        {
                            "id": "cli",
                            "scenarios": {
                                "think": {
                                    "model": "scenario-embed",
                                    "surfaces": ["chat"],
                                }
                            },
                        }
                    ]
                },
            },
            request={
                "path": "emb",
                "headers": {"x-netllm-source": "cli"},
                "body": {"model": "bge-m3", "input": "hi", "reasoning_effort": "high"},
            },
            divergence=["D14"],
            note=(
                "PHASE 4d [D14] opt-in: byte-for-byte the request of "
                "guards-scenario-emb-classified-as-chat, with surfaces=['chat'] "
                "added to the rule. The request is STILL classified 'think' "
                "(scenario_counts cli:think is identical — classification is "
                "surface-independent on purpose), but the rule no longer fires, "
                "so the canonical model stays bge-m3: candidacy, the metrics "
                "model label and the 404 hint all follow the requested model "
                "again instead of the chat-shaped override. (The wire model is "
                "bge-m3 in BOTH vectors — the override never reached the wire "
                "even when it fired, which is the chat-side half of D10 and is "
                "NOT fixed in this phase.) Drop the qualifier and the old "
                "behavior comes back."
            ),
        )
    )

    for vid, path, body, headers in (
        (
            "guards-scenario-chat-web-search",
            "chat_ns",
            _chat("farm-chat", tools=[{"type": "web_search_preview"}]),
            {},
        ),
        (
            "guards-scenario-chat-background-ua",
            "chat_ns",
            _chat("farm-chat", max_tokens=64),
            {"user-agent": "claude-code/1.0"},
        ),
        (
            "guards-scenario-messages-think",
            "messages_ns",
            _msg("farm-chat", thinking={"type": "enabled", "budget_tokens": 1024}),
            {},
        ),
        (
            "guards-scenario-messages-ignores-openai-reasoning",
            "messages_ns",
            _msg("farm-chat", reasoning_effort="high"),
            {},
        ),
        (
            "guards-scenario-responses-think",
            "responses_ns",
            {"model": "farm-chat", "input": "hi", "reasoning": {"effort": "high"}},
            {},
        ),
    ):
        out.append(
            vector(
                g,
                vid,
                scenario={"backends": [_backend("alpha", ["farm-chat"])]},
                request={"path": path, "headers": headers, "body": body},
            )
        )

    out.append(
        vector(
            g,
            "guards-scenario-messages-long-context",
            scenario={"backends": [_backend("alpha", ["farm-chat"])]},
            request={
                "path": "messages_ns",
                "body": {
                    "model": "farm-chat",
                    "max_tokens": 16,
                    "messages": [{"role": "user", "content": "x" * 140_000}],
                },
            },
            note="structural signal outranks intent signals",
        )
    )
    return out


def all_vectors() -> list[tuple[str, dict[str, Any]]]:
    return [*_naming(), *_cloud(), *_guards()]


# ---------------------------------------------------------------------------
# Materialization / replay
# ---------------------------------------------------------------------------


def vector_path(group: str, vid: str) -> Path:
    return GROUP_DIR / group / f"{vid}.json"


def run_vector(doc: dict[str, Any]) -> dict[str, Any]:
    """Same contract as test_vectors.run_vector (Phase 0a)."""
    with contract_environment(doc["scenario"]) as env:
        result = env.drive(doc["request"])
        delta = env.probe.delta()
        calls = env.farm.inference_calls()
    return canonicalize_result(result, delta, calls)


def _write(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")


def materialize() -> list[Path]:
    """Write every scenario doc (definition only, no `expected`)."""
    written: list[Path] = []
    for group, doc in all_vectors():
        path = vector_path(group, doc["id"])
        if path.exists():
            existing = json.loads(path.read_text())
            if "expected" in existing:
                doc = {**doc, "expected": existing["expected"]}
        _write(path, doc)
        written.append(path)
    return written


def checked_in_vectors() -> list[Path]:
    return sorted(GROUP_DIR.glob("*/*.json"))


# ---------------------------------------------------------------------------
# Tests (pytest does not auto-collect this module — pass the path explicitly)
# ---------------------------------------------------------------------------


def test_every_definition_is_materialized() -> None:
    defined = {vector_path(g, d["id"]) for g, d in all_vectors()}
    missing = sorted(str(p) for p in defined - set(checked_in_vectors()))
    assert not missing, f"undefined vector files: {missing}"


def test_no_unintended_keyless_401_recorded() -> None:
    """Replay-time half of the anti-collapse guard (anticollapse.py)."""
    assert_corpus_has_no_unintended_keyless_401(checked_in_vectors())


def test_vector_docs_are_well_formed() -> None:
    for path in checked_in_vectors():
        doc = json.loads(path.read_text())
        for key in ("id", "divergence", "scenario", "request"):
            assert key in doc, f"{path.name} missing {key!r}"
        assert doc["id"] == path.stem, f"{path.name}: id/filename mismatch"
        assert isinstance(doc["divergence"], list)
        unknown = set(doc["divergence"]) - VALID_DIVERGENCE_IDS
        assert not unknown, f"{path.name}: unknown divergence IDs {unknown}"
        assert doc["request"].get("path"), f"{path.name} request has no path"
        assert path.parent.name in GROUPS, f"{path.name}: unknown group dir"


@pytest.mark.parametrize(
    "vector_path_", checked_in_vectors(), ids=lambda p: f"{p.parent.name}/{p.stem}"
)
def test_naming_cloud_guards_vector(vector_path_: Path) -> None:
    doc = json.loads(vector_path_.read_text())
    actual = run_vector(doc)
    # Anti-collapse: fails at record time, before a false vector can land.
    assert_no_unintended_keyless_401(doc, actual)
    if RECORD:
        doc["expected"] = actual
        _write(vector_path_, doc)
    expected = doc.get("expected")
    assert expected is not None, (
        f"{vector_path_.name} has no expected block — record it with "
        "NETLLM_VECTOR_RECORD=1"
    )
    assert actual == expected


if __name__ == "__main__":  # pragma: no cover - authoring helper
    for p in materialize():
        print(p)
