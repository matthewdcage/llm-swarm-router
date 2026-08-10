"""Per-attempt success/failure accounting — the one ledger (plan §1).

Cluster ``accounting.py`` of the F-26 split: ``AttemptRecorder`` plus the two
usage parsers it reads and the factory the engine calls. Nothing else in the
agent may touch ``pool.mark_success``/``mark_failure``, ``REQUESTS_TOTAL`` or
``is_capacity_error``.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

from netllm_core.pool import is_capacity_error

from netllm_agent.metrics import (
    COMPLETION_TOKENS_TOTAL,
    PROMPT_TOKENS_TOTAL,
    REQUEST_LATENCY,
    REQUESTS_TOTAL,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from netllm_core.models import Backend

    from netllm_agent.request_plan import RequestPlan
    from netllm_agent.shard import ShardContext

    from . import AgentService

__all__ = ["AccountingMixin", "AttemptRecorder", "_token_count"]

# Substrings that can appear in an SSE frame carrying generated content. A
# cheap pre-filter so a stream that never produces content (an immediate
# error frame, a keepalive-only idle) is not JSON-parsed frame by frame.
_CONTENT_HINTS = (
    '"content"',
    '"text"',
    '"partial_json"',
    '"thinking"',
    '"reasoning',
    '"tool_calls"',
)


def _token_count(value: Any) -> int:
    """Backend-supplied usage values are untrusted; never raise mid-stream."""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _event_carries_content(obj: Any) -> bool:
    """True when one decoded SSE event carries generated content.

    Both wire dialects, and deliberately narrow: an OpenAI role-only opener
    (``delta: {"role": "assistant", "content": ""}``) and Anthropic's
    ``message_start`` / ``ping`` are protocol frames, not tokens. Counting
    them would make TTFT measure how fast the upstream acknowledged the
    request rather than how long the user waited for the first word.
    """
    if not isinstance(obj, dict):
        return False
    # Anthropic Messages: content_block_delta / thinking_delta / input_json_delta.
    delta = obj.get("delta")
    if isinstance(delta, dict):
        for key in ("text", "partial_json", "thinking"):
            if delta.get(key):
                return True
    choices = obj.get("choices")
    if not isinstance(choices, list):
        return False
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        if choice.get("text"):  # legacy completions
            return True
        chunk_delta = choice.get("delta")
        if isinstance(chunk_delta, dict):
            for key in ("content", "reasoning_content", "reasoning", "tool_calls"):
                if chunk_delta.get(key):
                    return True
    return False


def _sse_carries_content(chunk: str) -> bool:
    """True when an SSE chunk contains at least one content-bearing event."""
    if not chunk:
        return False
    for hint in _CONTENT_HINTS:
        if hint in chunk:
            break
    else:
        return False
    for line in chunk.split("\n"):
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            continue
        if _event_carries_content(obj):
            return True
    return False


class AttemptRecorder:
    """The one place per-attempt success and failure accounting lands.

    Before this existed, each of the five proxy paths (chat ns/s,
    embeddings, messages ns/s) inlined its own success block and its own
    failure block, which is why a fix could land on one loop only
    (behavior-matrix.md D1/D2, F-24). Every path now routes accounting
    through a recorder instead, so the pool ledger, ``REQUESTS_TOTAL``,
    ``REQUEST_LATENCY``, the token counters, ``telemetry.record_usage``,
    ``_request_count`` and shard completion are written by exactly one
    piece of code.

    One instance per proxied request; the instance is threaded into the
    per-attempt helpers (``_stream_with_metrics``, ``_messages_attempt``)
    so an inner wrapper and the outer failover loop share its dedup
    ledger.
    """

    __slots__ = ("_plan", "_recorded_failures", "_service", "_ttft_s")

    def __init__(self, service: AgentService, plan: RequestPlan | None = None) -> None:
        self._service = service
        # The plan supplies the ledger's non-backend dimensions (source,
        # policy, API surface). Optional so a test can build a bare recorder.
        self._plan = plan
        # Exceptions already accounted for, held by identity. A stream
        # wrapper records the failure and re-raises the same object to
        # the failover loop; without this the loop's own except clause
        # would count it a second time (D2 dedup guard).
        self._recorded_failures: list[Exception] = []
        # Measured time-to-first-token, seconds. None until a streaming path
        # stamps it, and None forever on a non-streaming request — where TTFT
        # is not observable at all.
        self._ttft_s: float | None = None

    def observe_stream_chunk(self, chunk: str, *, started_at: float) -> None:
        """Stamp time-to-first-token at the first content-bearing SSE frame.

        The timestamp arrives *through the recorder* rather than through the
        five proxy paths, on purpose: hand-copying per-attempt accounting into
        each path is the duplication that produced F-24, and the recorder is
        the one object every path already shares. Called once per streamed
        chunk; after the stamp is taken it is a single ``is not None`` test.
        """
        if self._ttft_s is not None:
            return
        if _sse_carries_content(chunk):
            self._ttft_s = max(0.0, time.monotonic() - started_at)

    def success(
        self,
        *,
        backend: Backend,
        model: str,
        latency_s: float,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        shard: ShardContext | None = None,
    ) -> None:
        """Full success accounting for one completed attempt."""
        service = self._service
        service.pool.mark_success(backend, latency_s * 1000)
        REQUESTS_TOTAL.labels(backend=backend.base_url, model=model, status="ok").inc()
        REQUEST_LATENCY.labels(backend=backend.base_url).observe(latency_s)
        if prompt_tokens or completion_tokens:
            PROMPT_TOKENS_TOTAL.labels(backend=backend.base_url, model=model).inc(
                prompt_tokens
            )
            COMPLETION_TOKENS_TOTAL.labels(backend=backend.base_url, model=model).inc(
                completion_tokens
            )
        # UI-2. This used to pass ``latency_s * 0.3`` and ``latency_s * 0.7``:
        # two invented constants that turned total latency into a "prefill
        # speed" and a "generation speed" on the dashboard and the macOS
        # menubar. Both durations are now measured or absent. Absent means
        # 0.0 here, which leaves the duration accumulators untouched and makes
        # avg_prefill_tps report null instead of a rescaled constant.
        ttft_s = self._ttft_s
        plan = self._plan
        service.telemetry.record_usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            prefill_duration=ttft_s if (ttft_s is not None and prompt_tokens) else 0.0,
            generation_duration=(
                max(0.0, latency_s - ttft_s)
                if (ttft_s is not None and completion_tokens)
                else 0.0
            ),
            latency_s=latency_s,
            ttft_s=ttft_s,
            backend_id=backend.id,
            model=model,
            source_id=plan.source.id if plan is not None else "",
            policy_key=plan.policy_key if plan is not None else "",
            surface=plan.api_format if plan is not None else "",
        )
        service._request_count += 1
        service._mark_shard_success(shard)

    def success_from_result(
        self,
        *,
        backend: Backend,
        model: str,
        result: Any,
        latency_s: float,
        shard: ShardContext | None = None,
    ) -> None:
        """Non-streaming twin of :meth:`success` — tokens come from the
        response body's ``usage`` object instead of an SSE chunk."""
        prompt, completion = self._service._usage_from_response(result)
        self.success(
            backend=backend,
            model=model,
            latency_s=latency_s,
            prompt_tokens=prompt,
            completion_tokens=completion,
            shard=shard,
        )

    def failure(self, *, backend: Backend, model: str, exc: Exception) -> None:
        """Failure accounting for one attempt, at most once per exception.

        This is the *only* caller of ``is_capacity_error`` in the agent:
        capacity rejections (busy model reload, rate limit, memory guard)
        exclude the backend for this request only, while hard errors
        count toward the offline trip (pool.py p:39-55, p:241-262).
        Classifying anywhere else is how the two halves drifted apart.
        """
        if self.already_recorded(exc):
            return
        self._recorded_failures.append(exc)
        status_code = getattr(exc, "status_code", None)
        self._service.pool.mark_failure(
            backend,
            capacity=is_capacity_error(status_code, str(exc)),
            status_code=status_code,
        )
        REQUESTS_TOTAL.labels(
            backend=backend.base_url, model=model, status="error"
        ).inc()

    def already_recorded(self, exc: Exception) -> bool:
        return any(recorded is exc for recorded in self._recorded_failures)


class AccountingMixin:
    """The usage parsers and the recorder factory (composed into AgentService)."""

    @staticmethod
    def _usage_from_response(result: Any) -> tuple[int, int]:
        if not isinstance(result, dict):
            return 0, 0
        usage = result.get("usage")
        if not isinstance(usage, dict):
            return 0, 0
        prompt = _token_count(usage.get("prompt_tokens") or usage.get("input_tokens"))
        completion = _token_count(
            usage.get("completion_tokens") or usage.get("output_tokens")
        )
        return prompt, completion

    @staticmethod
    def _usage_from_sse_chunk(chunk: str) -> tuple[int, int]:
        """Token usage carried by one SSE chunk, if any.

        Understands both wire formats a streaming wrapper can see:
        Anthropic (message_start's message.usage / message_delta's usage,
        input_tokens/output_tokens) and OpenAI (the stream_options
        include_usage chunk, prompt_tokens/completion_tokens).
        """
        prompt = completion = 0
        for line in chunk.split("\n"):
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            usage = obj.get("usage")
            if not isinstance(usage, dict):
                message = obj.get("message")
                usage = message.get("usage") if isinstance(message, dict) else None
            if not isinstance(usage, dict):
                continue
            prompt = max(
                prompt,
                _token_count(usage.get("input_tokens") or usage.get("prompt_tokens")),
            )
            completion = max(
                completion,
                _token_count(
                    usage.get("output_tokens") or usage.get("completion_tokens")
                ),
            )
        return prompt, completion

    def new_attempt_recorder(self, plan: RequestPlan | None = None) -> AttemptRecorder:
        """One accounting ledger for one request.

        A factory rather than a direct ``AttemptRecorder(service)`` call so
        the engine never has to import back into this module (the
        ``service`` ⇄ ``engine`` cycle the dependency graph flags), and so a
        test can substitute a recorder wholesale. ``plan`` carries the
        request's ledger dimensions (source, policy, API surface).
        """
        return AttemptRecorder(self, plan)
