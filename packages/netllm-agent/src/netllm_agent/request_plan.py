"""The per-request plan every proxy surface computes before it routes
(F-24/F-26 plan §3 Phase 4).

The five proxy entry points used to open with the same ~25-line prologue —
source attribution, scenario classification, the model rewrite chain, the
capability guard, routing resolution, admission, shard extraction, header
normalization — copy-pasted with small, mostly accidental differences. The
dependency graph counts those prologues as 57 of ``service.py``'s 123
cross-cluster call sites, so collapsing them into one
:meth:`AgentService.build_request_plan` call removes roughly 45 percent of
the file's coupling before the F-26 module split has to move anything.

:class:`RequestPlan` is frozen: once built, everything downstream (selection,
invocation, accounting, exhaustion) reads it and nothing rewrites it. The one
value that is still *derived* per attempt is the upstream model name — that
is a per-backend property, not a plan property.

**Module home.** Plan §1 files ``RequestPlan`` under ``netllm-core``. It
lives here instead because the plan carries a :class:`ShardContext` and a
:class:`Surface`, both of which are netllm-agent types (``Surface`` landed in
``netllm_agent.taxonomy`` in Phase 3); importing them from netllm-core would
invert the package dependency. Phase 9 is the layout phase and can move the
type once the surrounding modules move with it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from netllm_core.models import SourceConfig
from netllm_core.routing_policy import ResolvedRouting
from netllm_core.scenarios import Scenario
from netllm_core.source_identity import ResolvedSource

from .shard import ShardContext
from .taxonomy import Surface

__all__ = ["RequestPlan", "api_format_for"]


def api_format_for(surface: Surface) -> str:
    """Wire dialect the scenario classifier and routing policy see.

    Note the asymmetry this preserves verbatim: the embeddings surface
    classifies as ``"openai"`` (D14 — chat scenario rules silently apply to
    embedding traffic; Phase 4d gives the classifier a surface input so a
    rule can opt out of that).
    """
    return "anthropic" if surface is Surface.MESSAGES else "openai"


@dataclass(frozen=True)
class RequestPlan:
    """Everything decided about a request before the first backend is picked."""

    surface: Surface
    # Lower-cased header map; normalized exactly once, at the top of the
    # plan builder (D12 — the route layer used to re-normalize for Messages).
    headers: Mapping[str, str]
    source: ResolvedSource
    source_config: SourceConfig | None
    scenario: Scenario
    # The name the caller asked for, restored onto every response body and
    # SSE frame before it goes back out.
    requested_model: str
    # The canonical name after source model_rewrites and the scenario
    # override — what candidacy, selection, and accounting all key on.
    model: str
    routing: ResolvedRouting
    # Extracted on every surface (Phase 4); only the chat paths feed it to
    # selection today. Turning it on for EMB/M-NS/M-S is D5, Phase 5.
    shard: ShardContext | None
    payload: dict[str, Any]
    # Agent-hop requests (x-netllm-hops >= 1): skip pool substitution on
    # the terminating peer so the forwarded model name is invoked literally.
    exact_model_only: bool = False
    # Messages only: the resolved Anthropic key, "" when the request had
    # neither an ``x-api-key`` header nor ``ANTHROPIC_API_KEY``. Decides the
    # keyless-401 vs generic exhaustion split (D11).
    api_key: str = ""

    @property
    def api_format(self) -> str:
        return api_format_for(self.surface)
