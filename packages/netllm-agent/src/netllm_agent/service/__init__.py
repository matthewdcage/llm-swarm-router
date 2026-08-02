"""Agent service — discovery, pool, routing orchestration.

F-26 (plan-f24-f26.md §3 Phase 9) split the 2,064-line ``service.py`` into
this package. The move is **mechanical**: every function body below is the
byte-identical body it had before the split, and the contract-vector corpus
(``tests/contract/vectors``) is the proof — a pure move changes zero vectors.

Layout (plan §1), one module per cluster of the AST dependency graph:

===================== ====================================================
``core``              construction, ``apply_config``, ``apply_runtime_strategy``
``backends``          provider scan, pool refresh, health publish, upstreams
``cloud``             cloud credentials and provider rows (F-04)
``policy``            headers, source, scenario, guards, ``build_request_plan``
``selection``         strategy walk and ``build_candidates``
``accounting``        ``AttemptRecorder`` — the one success/failure ledger
``engine``            ``run_with_failover`` / ``open_stream`` — the one loop
``surfaces/``         one module per wire surface: adapter + entry points
``status``            read-only status and catalog surfaces
``swarm_tasks``       mDNS, heartbeat, re-discovery, background lifecycle
===================== ====================================================

``AgentService`` is the composition of one mixin per module. Composition
rather than delegation on purpose: ``AgentService.<method>`` stays a class
attribute, so the dozens of existing
``mock.patch("netllm_agent.service.AgentService.proxy_messages")`` sites keep
intercepting, and ``app.py``'s 20-attribute surface is unchanged. No mixin
declares a name another one declares, so the MRO carries no policy.

**Patch targets moved with the code, deliberately** (plan §3 Phase 9, risk
§7.3): module globals are patched where they are *used*, so
``scan_local_providers`` is now
``netllm_agent.service.backends.scan_local_providers``. There is no
namespace shim re-exporting it here — a shim would make module-global
indirection load-bearing, which is the defect, not the fix.
``tests/contract/test_patch_targets.py`` holds the deny-grep and a canary
per (name, module) pair.
"""

from __future__ import annotations

from .accounting import AccountingMixin, AttemptRecorder
from .backends import BackendsMixin
from .cloud import LEGACY_CLOUD_BACKEND_IDS, CloudMixin
from .core import AgentServiceCore, SourceCapacityExceeded
from .policy import PolicyMixin
from .selection import SelectionMixin
from .status import StatusMixin
from .surfaces.base import SurfaceHelpersMixin
from .surfaces.chat import ChatSurfaceMixin
from .surfaces.embeddings import EmbeddingsSurfaceMixin
from .surfaces.messages import MessagesSurfaceMixin
from .surfaces.responses import ResponsesSurfaceMixin
from .swarm_tasks import SwarmTasksMixin

__all__ = [
    "LEGACY_CLOUD_BACKEND_IDS",
    "AgentService",
    "AttemptRecorder",
    "SourceCapacityExceeded",
]


class AgentService(
    ChatSurfaceMixin,
    EmbeddingsSurfaceMixin,
    MessagesSurfaceMixin,
    ResponsesSurfaceMixin,
    SurfaceHelpersMixin,
    PolicyMixin,
    SelectionMixin,
    BackendsMixin,
    CloudMixin,
    StatusMixin,
    SwarmTasksMixin,
    AccountingMixin,
    AgentServiceCore,
):
    """Core agent state shared by HTTP handlers."""
