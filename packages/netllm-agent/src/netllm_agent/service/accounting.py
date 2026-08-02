"""Per-attempt success/failure accounting — the one ledger (plan §1).

Cluster ``accounting.py`` of the F-26 split: ``AttemptRecorder`` plus the two
usage parsers it reads and the factory the engine calls. Nothing else in the
agent may touch ``pool.mark_success``/``mark_failure``, ``REQUESTS_TOTAL`` or
``is_capacity_error``.
"""

from __future__ import annotations

import json
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

    from netllm_agent.shard import ShardContext

    from . import AgentService

__all__ = ["AccountingMixin", "AttemptRecorder", "_token_count"]


def _token_count(value: Any) -> int:
    """Backend-supplied usage values are untrusted; never raise mid-stream."""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


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

    __slots__ = ("_service", "_recorded_failures")

    def __init__(self, service: AgentService) -> None:
        self._service = service
        # Exceptions already accounted for, held by identity. A stream
        # wrapper records the failure and re-raises the same object to
        # the failover loop; without this the loop's own except clause
        # would count it a second time (D2 dedup guard).
        self._recorded_failures: list[Exception] = []

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
        service.telemetry.record_usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            prefill_duration=latency_s * 0.3 if prompt_tokens else 0.0,
            generation_duration=latency_s * 0.7 if completion_tokens else 0.0,
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

    def new_attempt_recorder(self) -> AttemptRecorder:
        """One accounting ledger for one request.

        A factory rather than a direct ``AttemptRecorder(service)`` call so
        the engine never has to import back into this module (the
        ``service`` ⇄ ``engine`` cycle the dependency graph flags), and so a
        test can substitute a recorder wholesale.
        """
        return AttemptRecorder(self)
