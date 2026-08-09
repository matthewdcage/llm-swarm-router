"""Route registration, one module per coherent group.

``create_app`` builds a :class:`RouteContext` and calls every registrar in
:data:`REGISTRARS` in order; adding a route means editing exactly one of
these modules, and adding a *group* means adding a module and one line
here. The HTTP surface stays asserted as an exact set by
``tests/contract/routes.json``, and the gate each route applies is
asserted against the pre-split mapping by
``tests/test_route_auth_gates.py``.
"""

from __future__ import annotations

from collections.abc import Callable

from netllm_agent.routes import admin, inference, root, swarm, telemetry
from netllm_agent.routes.context import RouteContext
from netllm_agent.routes.gates import AccessGates

Registrar = Callable[[RouteContext], None]

# Order is registration order on the app, and it deliberately does NOT match
# pre-split source order: baseline registered status before telemetry, this
# registers telemetry first, and /netllm/v1/client-env and /netllm/v1/heartbeat
# move several indices earlier. That is safe rather than merely assumed --
# every path is distinct, the only parameterised route
# (/netllm/v1/cloud/providers/{provider_id}/models) is the sole 6-segment path
# so nothing can shadow it at any ordering, and the /ui Mount sits ahead of
# every route in both trees with no route path beginning "/ui".
# Do not "restore" pre-split order on the belief that it was preserved.
REGISTRARS: tuple[Registrar, ...] = (
    root.register,
    telemetry.register,
    swarm.register,
    admin.register,
    inference.register,
)

__all__ = ["REGISTRARS", "AccessGates", "RouteContext", "Registrar"]
