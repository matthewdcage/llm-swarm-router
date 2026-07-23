"""Attribute a proxied request to a known CLI/harness source.

Phase 1 of docs/cli-source-routing-plan.md. Identity is resolved from the
request alone (no server-side session state) so it works identically on
the OpenAI and Anthropic surfaces, and on every peer in the mesh.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from dataclasses import dataclass

from netllm_core.models import DEFAULT_SOURCE_ID, SOURCE_HEADER, SourceConfig

_KEY_PREFIX = "netllm-"
_LOCAL_KEY = "netllm-local"


@dataclass(frozen=True)
class ResolvedSource:
    id: str
    # "header" | "key" | "user_agent" | "default"
    resolved_via: str
    # True only when a configured secret was presented and verified.
    authenticated: bool = False


_DEFAULT = ResolvedSource(id=DEFAULT_SOURCE_ID, resolved_via="default")


def _extract_key(headers: Mapping[str, str]) -> str:
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        if token:
            return token
    return headers.get("x-api-key", "").strip()


def _enabled_by_id(sources: list[SourceConfig]) -> dict[str, SourceConfig]:
    return {s.id: s for s in sources if s.enabled and s.id}


def resolve_source(
    *,
    headers: Mapping[str, str],
    sources: list[SourceConfig],
) -> ResolvedSource:
    """First match wins: header -> virtual key -> User-Agent -> default.

    `headers` must already be lower-cased (see AgentService._normalize_headers).
    Never raises and never denies a request -- an unrecognized or
    secret-mismatched caller is simply attributed to "default", the same
    routing behavior as before this feature existed.

    A source with no secret configured is attributed by any matching
    signal (header, key, or User-Agent) -- pure convenience labeling.
    Once a source has a secret configured, *no* signal grants that
    identity except a virtual key carrying the correct secret
    ("netllm-<id>.<secret>") -- a bare header or key naming a
    secret-protected source is not enough, closing the gap where an
    unauthenticated caller could otherwise claim an elevated source's
    identity just by sending its id.
    """
    by_id = _enabled_by_id(sources)
    if not by_id:
        return _DEFAULT

    key = _extract_key(headers)
    key_source_id: str | None = None
    key_secret: str | None = None
    if key and key != _LOCAL_KEY and key.startswith(_KEY_PREFIX):
        remainder = key[len(_KEY_PREFIX) :]
        key_source_id, _, key_secret = remainder.partition(".")

    def _secret_satisfied(source: SourceConfig) -> bool:
        required = source.resolve_secret()
        if not required:
            return True
        return (
            bool(key_secret)
            and key_source_id == source.id
            and secrets.compare_digest(key_secret, required)
        )

    header_source_id = (headers.get(SOURCE_HEADER) or "").strip()
    if header_source_id:
        source = by_id.get(header_source_id)
        if source is not None and _secret_satisfied(source):
            return ResolvedSource(
                id=source.id,
                resolved_via="header",
                authenticated=bool(source.resolve_secret()),
            )

    if key_source_id:
        source = by_id.get(key_source_id)
        if source is not None and _secret_satisfied(source):
            return ResolvedSource(
                id=source.id,
                resolved_via="key",
                authenticated=bool(source.resolve_secret()),
            )

    user_agent = headers.get("user-agent", "").lower()
    if user_agent:
        for source in by_id.values():
            # A secret-protected source cannot be won by a guessable
            # User-Agent string.
            if source.resolve_secret():
                continue
            needles = source.match.user_agent_contains
            if any(needle and needle.lower() in user_agent for needle in needles):
                return ResolvedSource(id=source.id, resolved_via="user_agent")

    return _DEFAULT
