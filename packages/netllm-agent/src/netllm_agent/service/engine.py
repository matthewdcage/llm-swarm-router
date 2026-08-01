"""The failover loop — one implementation, every non-streaming surface
(F-24/F-26 plan §3 Phase 6).

``run_with_failover`` is the collapse of ``proxy_chat_completion``'s,
``proxy_embeddings``' and ``proxy_messages``' three hand-copied loops. Each
one used to re-derive the same six steps (select, acquire, invoke, restore,
account, release) with small accidental differences, which is the mechanism
behind every divergence in behavior-matrix.md: a fix landed on one copy.

The loop takes a :class:`~netllm_agent.request_plan.RequestPlan` (what was
decided before routing) and a
:class:`~netllm_agent.service.surfaces.base.SurfaceAdapter` (everything that varies
per surface) and walks the adapter's
:class:`~netllm_agent.candidates.CandidateSchedule` in two phases:

1. the **strategy phase** — up to ``schedule.strategy_attempts`` rounds of
   ``_select_backend_for_request``, each excluding the rows that have
   already failed this request plus the rows that are dialect-ineligible;
2. the **fallback phase** — ``schedule.fallback_tiers`` in order, no
   strategy and no health filter, bounded by ``schedule.max_attempts``
   (D7). Empty on the OpenAI surfaces, so the phase is a no-op there.

Then :meth:`SurfaceAdapter.exhaustion_error` ends the request.

**Anti-erosion invariant** (plan §1, enforced by
``tests/contract/test_engine_erosion.py`` and ``scripts/check-engine-erosion.py``):
this module must never reference a ``Surface`` member, import a
``surfaces/`` module other than ``base``, or branch on ``plan.surface`` /
the adapter's concrete type. A new per-surface need extends the adapter
protocol. This is the property that makes "exactly one failover loop" hold
over time rather than just today.

Streaming (plan §3 Phase 7)
---------------------------

``open_stream`` walks the *same* two-phase schedule, but its per-attempt
unit is **select → acquire → connect → first event**: it returns a
:class:`StreamSession` only once an upstream stream has actually produced
its first event. That makes the post-F-32 pre-flight shape structural
rather than a convention held up by ``app._started_stream`` — everything
that can still choose a different backend happens before the caller has a
session, and everything after the session exists is unretryable by
construction.

:class:`StreamSession` then owns the rest of the request: per-line model
restore, usage capture, success accounting, shard completion, the
``yielded_any`` no-replay rule, and release — on clean end, on mid-stream
failure, and on the client disconnecting (``GeneratorExit``) alike.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from netllm_core.pool import Backend

from netllm_agent.candidates import CandidateSchedule
from netllm_agent.metrics import BACKEND_IN_FLIGHT
from netllm_agent.request_plan import RequestPlan

if TYPE_CHECKING:  # pragma: no cover - typing only
    # Annotation-only (PEP 563). Importing the adapter protocol at
    # runtime would make `engine` and `surfaces` a genuine import
    # cycle now that the surface entry points live in `surfaces/*`
    # and call back into the engine. The anti-erosion gate still
    # reads this node: `surfaces.base` is the one allowed import.
    from .accounting import AttemptRecorder
    from .surfaces.base import Invocation, SurfaceAdapter

logger = logging.getLogger(__name__)

__all__ = ["StreamSession", "open_stream", "run_with_failover"]


async def run_with_failover(adapter: SurfaceAdapter, plan: RequestPlan) -> Any:
    """Route one non-streaming request, retrying across candidates.

    Admission was taken by ``build_request_plan`` (before the caller's
    first ``await``); the matching release is this function's ``finally``,
    so it happens on success, on exhaustion, on an unclassified exception
    and on cancellation alike.
    """
    service = adapter.service
    try:
        await service.refresh_local_backends()
        schedule = adapter.candidates(plan)
        recorder = service.new_attempt_recorder()
        last_error: Exception | None = None
        tried: set[str] = set()

        async for attempt, backend in _walk_schedule(adapter, plan, schedule, tried):
            try:
                return await _run_attempt(adapter, plan, backend, recorder, attempt)
            except Exception as exc:
                if not adapter.classify_error(exc):
                    raise
                last_error = exc
                # Purely "this backend failed this request" (D8): dialect
                # eligibility is the schedule's ``ineligible_ids``.
                tried.add(backend.id)

        raise adapter.exhaustion_error(plan, last_error)
    finally:
        service._source_release(plan.source.id)


async def _walk_schedule(
    adapter: SurfaceAdapter,
    plan: RequestPlan,
    schedule: CandidateSchedule,
    tried: set[str],
) -> AsyncIterator[tuple[int, Backend]]:
    """The two-phase candidate walk, shared by both entry points.

    Phase one runs ``_select_backend_for_request`` up to
    ``schedule.strategy_attempts`` times, re-reading ``tried`` on every
    round — the caller adds to it between yields, which is how a failed
    backend stops being selectable without the walk knowing why it failed.
    Phase two replays ``schedule.fallback_tiers`` in order, no strategy and
    no health filter, bounded by the *same* attempt budget the strategy
    phase spends from (D7); it used to be an unbounded ``for`` that ran past
    ``max_attempts`` by construction, because the cap only ever measured the
    pool.

    Both the non-streaming loop and ``open_stream`` walk it. If they did not,
    "failover order" would be two things again — which is the F-24 defect in
    miniature.
    """
    service = adapter.service
    routing = plan.routing
    attempt = 0
    while attempt < schedule.strategy_attempts:
        attempt += 1
        backend = await service._offload_if_probing(
            service._select_backend_for_request,
            plan.model,
            routing.strategy,
            attempt,
            plan.shard,
            local_only=routing.local_only,
            prefer_provider=routing.prefer_provider,
            prefer_cloud=schedule.prefer_cloud,
            exclude_ids=schedule.exclusions(tried),
            pinned=routing.pinned_backend,
            cloud_provider_allowlist=routing.cloud_provider_allowlist,
            extra_candidates=list(schedule.extra_candidates),
        )
        if backend is None:
            break
        yield attempt, backend

    for backend in schedule.fallback_backends():
        if attempt >= schedule.max_attempts:
            break
        attempt += 1
        yield attempt, backend


async def _run_attempt(
    adapter: SurfaceAdapter,
    plan: RequestPlan,
    backend: Backend,
    recorder: AttemptRecorder,
    attempt: int,
) -> Any:
    """One acquire → invoke → account → release cycle.

    Everything between ``acquire`` and the ``finally`` is inside the timed
    region, including upstream-client construction and per-backend model
    resolution: those can fail too, and counting their failures nowhere is
    exactly the D2 hole this phase must not reopen.
    """
    service = adapter.service
    service.pool.acquire(backend)
    BACKEND_IN_FLIGHT.labels(backend=backend.base_url).set(backend.in_flight)
    t0 = time.monotonic()
    try:
        invocation = adapter.build_invocation(plan, backend)
        result = await adapter.invoke(plan, invocation)
        result = adapter.restore_model(plan, invocation, result)
        latency_s = time.monotonic() - t0
        prompt_tokens, completion_tokens = adapter.extract_usage(result)
        recorder.success(
            backend=backend,
            model=plan.model,
            latency_s=latency_s,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            shard=plan.shard,
        )
        return result
    except Exception as exc:
        if adapter.classify_error(exc):
            recorder.failure(backend=backend, model=plan.model, exc=exc)
            logger.warning(
                "%s %s failed (attempt %s): %s",
                adapter.log_label,
                backend.base_url,
                attempt,
                exc,
            )
        raise
    finally:
        service.pool.release(backend)
        BACKEND_IN_FLIGHT.labels(backend=backend.base_url).set(backend.in_flight)
        service._update_health_metrics()


async def open_stream(adapter: SurfaceAdapter, plan: RequestPlan) -> StreamSession:
    """Route one streaming request up to (and including) its first event.

    The shape is the point. Selection, acquisition, the upstream call *and*
    the first event all happen here, inside the failover walk, so:

    - every recoverable failure — no candidate, a refused connection, a
      500 on the request, a drop before frame 0 — happens while this
      coroutine is still being awaited, which is where the route layer can
      still turn it into a real HTTP status (F-32). It cannot become a
      200-with-an-aborted-body, and that is now true by construction rather
      than because ``app._started_stream`` remembers to pull a chunk;
    - conversely, once a :class:`StreamSession` exists, a byte is already
      spoken for. Retrying past that point would replay a second upstream
      response into one SSE body, so the session never retries — the
      ``yielded_any`` rule, expressed as a type rather than as a flag.

    Admission (taken in ``build_request_plan``) is released here on every
    path that does *not* return a session; the session owns it otherwise.
    """
    service = adapter.service
    try:
        await service.refresh_local_backends()
        schedule = adapter.candidates(plan)
        recorder = service.new_attempt_recorder()
        last_error: Exception | None = None
        tried: set[str] = set()

        async for attempt, backend in _walk_schedule(adapter, plan, schedule, tried):
            try:
                return await _connect_stream(adapter, plan, backend, recorder, attempt)
            except Exception as exc:
                if not adapter.classify_error(exc):
                    raise
                last_error = exc
                tried.add(backend.id)

        raise adapter.exhaustion_error(plan, last_error)
    except BaseException:
        # Not `finally`: on the success path the session inherits the
        # admission slot and releases it when the stream ends.
        service._source_release(plan.source.id)
        raise


async def _connect_stream(
    adapter: SurfaceAdapter,
    plan: RequestPlan,
    backend: Backend,
    recorder: AttemptRecorder,
    attempt: int,
) -> StreamSession:
    """Acquire one backend and pull the stream's first event.

    Mirrors ``_run_attempt``: the upstream client construction and the
    per-backend model resolution are inside the timed, accounted region,
    because a failure there is still this backend's failure (D2).

    On failure the backend is released here — there is no session to own
    it yet. On success the release moves to the session.
    """
    service = adapter.service
    service.pool.acquire(backend)
    BACKEND_IN_FLIGHT.labels(backend=backend.base_url).set(backend.in_flight)
    t0 = time.monotonic()
    first: str | None = None
    try:
        invocation = adapter.build_invocation(plan, backend)
        stream = adapter.invoke_stream(plan, invocation).__aiter__()
        try:
            first = await anext(stream)
        except StopAsyncIteration:
            # An upstream that closes without a single event is a complete
            # (if empty) response, not a failure — the same reading the
            # non-stream arm gives an empty 200 body.
            first = None
    except BaseException as exc:
        # BaseException, not Exception: a cancellation between acquire and
        # the first event must still hand the pool slot back.
        if isinstance(exc, Exception) and adapter.classify_error(exc):
            recorder.failure(backend=backend, model=plan.model, exc=exc)
            logger.warning(
                "%s stream %s failed (attempt %s): %s",
                adapter.log_label,
                backend.base_url,
                attempt,
                exc,
            )
        _release_backend(service, backend)
        raise
    return StreamSession(
        adapter=adapter,
        plan=plan,
        invocation=invocation,
        backend=backend,
        recorder=recorder,
        stream=stream,
        first=first,
        started_at=t0,
        attempt=attempt,
    )


def _release_backend(service: Any, backend: Backend) -> None:
    service.pool.release(backend)
    BACKEND_IN_FLIGHT.labels(backend=backend.base_url).set(backend.in_flight)
    service._update_health_metrics()


class StreamSession:
    """A live upstream stream, plus everything that must happen around it.

    Constructed only by :func:`open_stream`, only after the upstream has
    produced its first event. Iterating it yields the client-facing SSE
    chunks; it is single-use.

    Three properties are worth stating outright, because each one was a
    hand-copied fragment in two loops before this type existed:

    **No replay.** A classified failure mid-stream ends the request with
    ``adapter.mid_stream_error_frame`` and never selects another backend.

    **Accounting is not owed to the consumer.** Success is recorded when
    the *upstream* stream ends, before its final chunk is handed on — see
    :meth:`_pump`. A consumer that stops reading at its own terminator
    (the Responses bridge ``break``s at ``data: [DONE]``) therefore cannot
    strand the accounting, which is exactly what D16 was.

    **Release is unconditional.** Clean end, mid-stream failure, client
    disconnect (``GeneratorExit`` raised at the suspended ``yield``) and
    cancellation all run :meth:`release`, which returns the pool slot, the
    in-flight gauge and the per-source admission slot exactly once.
    """

    __slots__ = (
        "_adapter",
        "_attempt",
        "_backend",
        "_completion_tokens",
        "_first",
        "_invocation",
        "_plan",
        "_prompt_tokens",
        "_recorder",
        "_released",
        "_started_at",
        "_stream",
    )

    def __init__(
        self,
        *,
        adapter: SurfaceAdapter,
        plan: RequestPlan,
        invocation: Invocation,
        backend: Backend,
        recorder: AttemptRecorder,
        stream: AsyncIterator[str],
        first: str | None,
        started_at: float,
        attempt: int,
    ) -> None:
        self._adapter = adapter
        self._plan = plan
        self._invocation = invocation
        self._backend = backend
        self._recorder = recorder
        self._stream = stream
        self._first = first
        self._started_at = started_at
        self._attempt = attempt
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._released = False

    @property
    def backend(self) -> Backend:
        """The backend serving this stream (the last candidate tried)."""
        return self._backend

    def __aiter__(self) -> AsyncIterator[str]:
        return self._pump()

    async def _pump(self) -> AsyncIterator[str]:
        """One chunk behind the upstream, on purpose.

        Each turn folds the pending chunk's usage in, pulls the *next* one,
        settles the attempt's accounting if that pull ended the stream (or
        failed it), and only then hands the pending chunk on. Reading one
        ahead is what lets success be recorded before the last chunk leaves:
        the consumer may stop reading the moment it sees its terminator, and
        with the accounting behind that chunk it would never run (D16).
        """
        adapter, plan = self._adapter, self._plan
        try:
            pending = self._first
            if pending is None:
                self._record_success()
            while pending is not None:
                p, c = adapter.extract_stream_usage(pending)
                self._prompt_tokens = max(self._prompt_tokens, p)
                self._completion_tokens = max(self._completion_tokens, c)

                error: Exception | None = None
                try:
                    nxt: str | None = await anext(self._stream)
                except StopAsyncIteration:
                    nxt = None
                except Exception as exc:
                    if not adapter.classify_error(exc):
                        raise
                    nxt, error = None, exc

                if error is not None:
                    self._record_failure(error)
                elif nxt is None:
                    self._record_success()

                yield adapter.restore_stream_line(plan, self._invocation, pending)

                if error is not None:
                    # Bytes are already on the wire; the only honest ending
                    # is this surface's error frame plus its terminator.
                    for frame in adapter.mid_stream_error_frame(error):
                        yield frame
                    return
                pending = nxt
        finally:
            self.release()

    def _record_success(self) -> None:
        self._recorder.success(
            backend=self._backend,
            model=self._plan.model,
            latency_s=time.monotonic() - self._started_at,
            prompt_tokens=self._prompt_tokens,
            completion_tokens=self._completion_tokens,
            shard=self._plan.shard,
        )

    def _record_failure(self, exc: Exception) -> None:
        self._recorder.failure(backend=self._backend, model=self._plan.model, exc=exc)
        logger.warning(
            "%s stream %s failed mid-stream (attempt %s): %s",
            self._adapter.log_label,
            self._backend.base_url,
            self._attempt,
            exc,
        )

    def release(self) -> None:
        """Return the pool slot and the admission slot. Idempotent."""
        if self._released:
            return
        self._released = True
        service = self._adapter.service
        _release_backend(service, self._backend)
        service._source_release(self._plan.source.id)
