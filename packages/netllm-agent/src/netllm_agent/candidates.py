"""The ordered attempt budget for one request (F-24/F-26 plan §3 Phase 5).

Every failover loop used to re-derive its own answer to three questions, in
three different places and three different ways:

1. *Which backends may the strategy loop even consider?* Chat filtered
   nothing; embeddings pre-seeded the per-request ``tried`` set with every
   anthropic-format pool row; the Messages surfaces pre-seeded it with the
   anthropic rows of the pool **and** of the request-scoped cloud extras.
   One set was doing two unrelated jobs — "this backend already failed" and
   "this backend speaks the wrong dialect" — with three initializations
   (**D8**).
2. *Where does the cloud go?* The OpenAI surfaces hand the legacy cloud row
   to selection as an ``extra_candidates`` row and let ``prefer_cloud``
   order it; the Messages surfaces exclude anthropic rows from selection
   entirely and replay them afterwards as an ordered tier (**D6**).
3. *How many attempts is that worth?* ``max(len(pool.backends), 1)`` on
   every surface — a number that counted rows the loop could never pick
   (ineligible dialects), ignored rows it could (the request-scoped cloud
   extras), and did not cover the Messages fallback tier at all, which is
   why M-NS/M-S could exceed their own cap (**D7**).

:class:`CandidateSchedule` answers all three once, declaratively, before the
loop starts. ``max_attempts`` is then an explicit sum over the schedule
rather than a proxy measurement of the pool, and the loop's job shrinks to
"walk the schedule".
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from netllm_core.pool import Backend

from .taxonomy import Surface, spec_for

__all__ = ["CandidateSchedule", "excluded_api_formats"]


def excluded_api_formats(surface: Surface) -> frozenset[str]:
    """Wire dialects a surface's *strategy loop* must never select (D8).

    This is the declarative form of the three ``tried`` pre-seedings:

    - ``CHAT`` excludes nothing. Chat has always been willing to select an
      anthropic-format row and talk OpenAI to it (``_openai_upstream`` is
      used for every row regardless of ``api_format``); that is preserved
      verbatim here, divergent or not.
    - ``EMBEDDINGS`` excludes ``anthropic``: the Anthropic API has no
      embeddings endpoint, so those rows are unservable, full stop — there
      is no fallback tier to catch them.
    - ``MESSAGES`` excludes ``anthropic`` from *selection* only. Those rows
      are not unservable, they are deferred: they come back as the ordered
      fallback tier so the cloud never shadows the local mesh in a
      rotation.
    """
    return spec_for(surface).excluded_api_formats


@dataclass(frozen=True)
class CandidateSchedule:
    """Everything the failover loop is allowed to try, decided up front.

    Two phases, in order:

    1. the **strategy phase** — ``strategy_attempts`` selection rounds over
       the pool plus :attr:`extra_candidates`, with :attr:`ineligible_ids`
       held out and :attr:`prefer_cloud` deciding whether cloud rows lead;
    2. the **fallback phase** — :attr:`fallback_tiers` walked in order,
       each tier in its own configured order (no strategy, no health
       filter): the Messages surfaces' anthropic arm.

    :attr:`max_attempts` bounds the two together, which is the whole point
    of the type (D7).
    """

    # Selection rounds the strategy phase may run: one per candidate it
    # could actually pick (eligible pool rows + eligible extras), floored
    # at 1 so an empty pool still produces the one attempt whose failure
    # generates the exhaustion error.
    strategy_attempts: int
    # Request-scoped rows that are not in the pool but ride along in
    # selection (the legacy cloud backend, F-04).
    extra_candidates: tuple[Backend, ...] = ()
    # routing.cloud_leads — orders cloud candidates first (D6).
    prefer_cloud: bool = False
    # Backends held out of the strategy phase for dialect reasons (D8).
    # Kept apart from the loop's `tried` set, which is now purely "this
    # backend failed this request".
    ineligible_ids: frozenset[str] = frozenset()
    # Ordered tiers tried after the strategy phase (D6).
    fallback_tiers: tuple[tuple[Backend, ...], ...] = ()

    @property
    def fallback_attempts(self) -> int:
        return sum(len(tier) for tier in self.fallback_tiers)

    @property
    def max_attempts(self) -> int:
        """The explicit sum over the schedule (D7).

        Previously ``max(len(pool.backends), 1)``: a pool measurement that
        both over- and under-counted, and that the Messages fallback tier
        simply ran past.
        """
        return self.strategy_attempts + self.fallback_attempts

    def fallback_backends(self) -> Iterator[Backend]:
        """The fallback phase, flattened, tier order preserved."""
        for tier in self.fallback_tiers:
            yield from tier

    def exclusions(self, tried: Sequence[str] | set[str]) -> set[str]:
        """``exclude_ids`` for one selection round.

        The union of the dialect-ineligible rows (fixed for the request)
        and the rows that have already failed it (grows per attempt).
        """
        return {*self.ineligible_ids, *tried}
