"""Per-surface adapters for the one failover loop (plan §1, §3 Phase 6).

``engine.py`` imports :mod:`.base` and nothing else from this package — the
anti-erosion gate enforces it. The surface→adapter mapping lives *here*, on
the outside of the loop, which is the only place a ``Surface`` branch is
allowed to exist.

Plan §1 files this package under ``service/surfaces/``; it sits at the
package root for now for the same reason ``taxonomy.py``, ``errors.py``,
``request_plan.py`` and ``candidates.py`` do — Phase 9 is the layout phase
and moves the whole set together.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from netllm_agent.taxonomy import Surface

from .base import Invocation, SurfaceAdapter
from .chat import ChatAdapter
from .embeddings import EmbeddingsAdapter
from .messages import MessagesAdapter

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .. import AgentService

__all__ = [
    "ChatAdapter",
    "EmbeddingsAdapter",
    "Invocation",
    "MessagesAdapter",
    "SurfaceAdapter",
    "adapter_for",
]

_ADAPTERS = {
    Surface.CHAT: ChatAdapter,
    Surface.EMBEDDINGS: EmbeddingsAdapter,
    Surface.MESSAGES: MessagesAdapter,
}


def adapter_for(service: AgentService, surface: Surface) -> SurfaceAdapter:
    """The adapter serving ``surface``.

    Adapters are frozen dataclasses over the service and hold no per-request
    state, so constructing one per request (or several) is free.
    """
    return _ADAPTERS[surface](service)
