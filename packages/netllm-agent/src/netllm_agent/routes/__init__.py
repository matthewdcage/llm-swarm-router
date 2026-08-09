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

# Order is registration order on the app. Paths are distinct, so it does not
# affect matching; it is kept in the pre-split order for reviewability.
REGISTRARS: tuple[Registrar, ...] = (
    root.register,
    telemetry.register,
    swarm.register,
    admin.register,
    inference.register,
)

__all__ = ["REGISTRARS", "AccessGates", "RouteContext", "Registrar"]
