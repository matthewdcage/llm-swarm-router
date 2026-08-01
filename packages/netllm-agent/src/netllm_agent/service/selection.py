"""Backend selection and the candidate schedule — cluster ``selection.py``.

The strategy walk (``_select_backend_for_request``) and ``build_candidates``,
which turns a plan plus the request-scoped cloud extras into the
:class:`~netllm_agent.candidates.CandidateSchedule` the engine walks.

[Seam S2] ``build_candidates`` no longer calls back into the Messages
surface for its anthropic fallback tier. The tier arrives as an argument,
supplied by the adapter that knows it exists
(``surfaces.base.AnthropicDialectAdapter.fallback_tiers``), which is what
removes the ``selection.py`` ⇄ ``surfaces/messages.py`` edge the AST
dependency graph flagged — and it is the same adapter seam D6 already uses
to express the two cloud topologies as data.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any

from netllm_core.models import Backend

from netllm_agent.candidates import CandidateSchedule, excluded_api_formats
from netllm_agent.request_plan import RequestPlan
from netllm_agent.shard import ShardContext, backend_for_url

logger = logging.getLogger(__name__)

__all__ = ["SelectionMixin"]


class SelectionMixin:
    """Which backend, in which order, for how many attempts."""

    def _select_backend_for_request(
        self,
        model: str,
        strategy: str,
        attempt: int,
        shard: ShardContext | None,
        *,
        local_only: bool = False,
        prefer_provider: str | None = None,
        prefer_cloud: bool = False,
        exclude_ids: set[str] | None = None,
        pinned: str | None = None,
        cloud_provider_allowlist: frozenset[str] | None = None,
        extra_candidates: list[Backend] | None = None,
    ) -> Backend | None:
        if pinned:
            backend = self.pool.backend_by_id(pinned)
            if (
                backend is not None
                and not (local_only and not backend.local)
                and backend.id not in (exclude_ids or set())
            ):
                return backend
            if attempt == 1:
                logger.warning(
                    "pinned backend %r unavailable — falling back to %s",
                    pinned,
                    strategy,
                )
        if strategy == "auto":
            # Shard-context requests keep deterministic placement;
            # everything else balances by live in-flight load.
            strategy = "batch_shard" if shard else "least_load"
        if strategy == "batch_shard":
            if shard and shard.batch_id is not None and shard.index is not None:
                candidates = self.pool.backends_for_model(
                    model, extra_candidates=extra_candidates
                )
                # [D5/D8/D17] The batch-ledger arm is the one selection
                # route that never consulted exclude_ids: it handed the raw
                # candidate list to the ledger. Feeding the shard to EMB and
                # the Messages surfaces makes that reachable with a *dialect*
                # exclusion in the set — and an anthropic row assigned by
                # the ledger for a /v1/embeddings request is precisely what
                # schedule.ineligible_ids exists to prevent.
                #
                # [D17] This is NOT invisible on the chat paths. The filter
                # also removes the backend that just failed, and
                # BatchRequestLedger.reassign_failed walks by *index*
                # (shard.py:75-83: `urls.index(current_url)`, then
                # `urls[pos + 1:]`). Dropping the failed row makes that
                # lookup raise ValueError, so pos = -1 and the walk restarts
                # at the HEAD of the candidate list instead of continuing
                # past the failed position. Chat batch_shard failover order
                # and attempt count therefore change whenever the ledger
                # assigned a non-first backend: with three rows and shard
                # index 2, a failure used to end the request after one
                # attempt (urls[3:] is empty) and now walks rows 0..1.
                # Deliberate — the old forward-only walk gave a shard pinned
                # to the last row zero failover, and post-D5 an unfiltered
                # list could re-dispatch to an ineligible dialect. Pinned by
                # the chain-batch-shard-reassign-restarts-at-head-* vectors.
                if exclude_ids:
                    candidates = [b for b in candidates if b.id not in exclude_ids]
                if attempt == 1:
                    url = self._batch_ledger.assign(
                        shard.batch_id, shard.index, candidates
                    )
                else:
                    current = self._batch_ledger.assignments.get(
                        (shard.batch_id, shard.index), ""
                    )
                    url = self._batch_ledger.reassign_failed(
                        shard.batch_id,
                        shard.index,
                        candidates,
                        current_url=current,
                    )
                if url:
                    return backend_for_url(url, candidates)
                return None

            shard_key = shard.shard_key if shard else None
            if shard_key is None and shard and shard.index is not None:
                shard_key = str(shard.index)
            if shard_key:
                use_strategy = "batch_shard" if attempt == 1 else "failover"
                return self.pool.select_backend(
                    model,
                    use_strategy,  # type: ignore[arg-type]
                    shard_key=shard_key,
                    attempt=attempt,
                    local_only=local_only,
                    prefer_provider=prefer_provider,
                    prefer_cloud=prefer_cloud,
                    exclude_ids=exclude_ids,
                    cloud_provider_allowlist=cloud_provider_allowlist,
                    extra_candidates=extra_candidates,
                )

            if attempt == 1:
                self._shardless_fallbacks += 1
                # Every request hitting this path means the configured
                # strategy is degenerate for this traffic — say so once,
                # then keep a counter instead of spamming the log.
                count = self._shardless_fallbacks
                if count == 1 or count % 100 == 0:
                    logger.warning(
                        "batch_shard without shard context — falling back to "
                        "round_robin (%s such requests so far; consider "
                        "default_strategy = 'auto' or 'least_load')",
                        count,
                    )
                return self.pool.select_backend(
                    model,
                    "round_robin",
                    local_only=local_only,
                    prefer_provider=prefer_provider,
                    prefer_cloud=prefer_cloud,
                    exclude_ids=exclude_ids,
                    cloud_provider_allowlist=cloud_provider_allowlist,
                    extra_candidates=extra_candidates,
                )
            return self.pool.select_backend(
                model,
                "failover",
                attempt=attempt,
                local_only=local_only,
                prefer_provider=prefer_provider,
                prefer_cloud=prefer_cloud,
                exclude_ids=exclude_ids,
                cloud_provider_allowlist=cloud_provider_allowlist,
                extra_candidates=extra_candidates,
            )

        # Load-aware strategies keep balancing on retries — exclude_ids
        # already guarantees progress past the failed backend. Dropping
        # to failover (local-first) on attempt 2 meant one flaky backend
        # funneled every retry to the local machine regardless of load.
        load_aware = {
            "least_load",
            "latency_weighted",
            "round_robin",
            "local_spillover",
        }
        if attempt == 1 or strategy in load_aware:
            use_strategy = strategy
        else:
            use_strategy = "failover"
        shard_key = shard.shard_key if shard else None
        return self.pool.select_backend(
            model,
            use_strategy,  # type: ignore[arg-type]
            shard_key=shard_key,
            attempt=attempt,
            local_only=local_only,
            prefer_provider=prefer_provider,
            prefer_cloud=prefer_cloud,
            exclude_ids=exclude_ids,
            cloud_provider_allowlist=cloud_provider_allowlist,
            extra_candidates=extra_candidates,
        )

    def _mark_shard_success(self, shard: ShardContext | None) -> None:
        if shard and shard.batch_id is not None and shard.index is not None:
            self._batch_ledger.mark_done(shard.batch_id, shard.index)

    async def _offload_if_probing(self, fn: Any, /, *args: Any, **kwargs: Any) -> Any:
        """Run selection in a worker thread only when a health probe could
        fire; fresh caches stay on the event loop (no thread overhead,
        no pool-exhaustion exposure under load)."""
        if self.pool.any_health_stale():
            return await asyncio.to_thread(fn, *args, **kwargs)
        return fn(*args, **kwargs)

    def build_candidates(
        self,
        plan: RequestPlan,
        cloud_extra: Sequence[Backend],
        *,
        fallback_tiers: Sequence[Sequence[Backend]] = (),
    ) -> CandidateSchedule:
        """The one candidate schedule every proxy surface runs (Phase 5).

        Call it *after* ``refresh_local_backends()`` and cloud
        materialization: the pool it measures must be the pool the loop
        will select from.

        Three decisions, one place (candidates.py has the long form):

        - **D8** — dialect eligibility becomes ``ineligible_ids``, computed
          once over the pool *and* the request-scoped extras, instead of
          three different pre-seedings of the loop's ``tried`` set. ``tried``
          is now purely "this backend failed this request".
        - **D6** — the two cloud topologies become data: OpenAI surfaces get
          ``extra_candidates`` + ``prefer_cloud``, the Messages surfaces get
          an ordered ``fallback_tiers`` entry. Neither is a code path any
          more.
        - **D7** — ``max_attempts`` is the sum over both phases, so the
          legacy cloud row buys the attempt it can actually use and the
          Messages fallback tier stops running past the cap.
        """
        excluded = excluded_api_formats(plan.surface)
        extras = tuple(cloud_extra)
        selectable = [*self.pool.backends, *extras]
        ineligible = frozenset(b.id for b in selectable if b.api_format in excluded)
        eligible = [b for b in selectable if b.id not in ineligible]

        # [Seam S2] The fallback tiers come from the adapter (empty on the
        # OpenAI surfaces, the anthropic-format rows on Messages), so this
        # module no longer branches on ``plan.surface`` nor calls into a
        # surface module. Empty tiers are dropped exactly as before.
        tiers: tuple[tuple[Backend, ...], ...] = tuple(
            tuple(tier) for tier in fallback_tiers if tier
        )

        return CandidateSchedule(
            strategy_attempts=max(len(eligible), 1),
            extra_candidates=extras,
            prefer_cloud=plan.routing.cloud_leads,
            ineligible_ids=ineligible,
            fallback_tiers=tiers,
        )
