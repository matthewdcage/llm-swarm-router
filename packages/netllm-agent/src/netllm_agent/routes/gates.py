"""Access gates for the HTTP surface, in one place.

Before Phase 5b these were closures defined inside ``create_app`` and each
route called whichever one the author remembered. That is precisely the
shape F-13/F-59 came out of: a gate is one forgotten call away from being
absent, and nothing enumerable existed to check it against.

They are methods on a class now for two reasons: ``create_app`` hands one
object to every registrar instead of three closures, and the gate applied
by each route becomes observable — ``tests/test_route_auth_gates.py``
records calls through this class and compares the result against the
mapping derived from the pre-split ``app.py``.

Behaviour is unchanged: ``require_admin_access`` still delegates to
``netllm_agent.admin.require_admin_access`` (403, local-or-token), the
read gate is still a no-op with no cluster token configured, and the
inference gate is still opt-in via ``swarm.require_token_for_inference``.
"""

from __future__ import annotations

import secrets

from fastapi import HTTPException, Request
from netllm_core.models import NetllmConfig

from netllm_agent import admin as admin_module


class AccessGates:
    """The three gates, bound to one config object."""

    def __init__(self, cfg: NetllmConfig) -> None:
        self.cfg = cfg

    def _is_local_client(self, request: Request) -> bool:
        from netllm_core.platform import local_admin_client_hosts

        client_host = (request.client.host if request.client else "").lower()
        return client_host in local_admin_client_hosts()

    def _presents_cluster_token(self, request: Request, token: str) -> bool:
        auth = request.headers.get("Authorization", "")
        if secrets.compare_digest(auth, f"Bearer {token}"):
            return True
        return secrets.compare_digest(request.headers.get("x-api-key", ""), token)

    def require_inference_access(self, request: Request) -> None:
        """Opt-in gate (swarm.require_token_for_inference): non-local
        clients must present the cluster token on /v1/* routes. Peer
        agents forward with the token automatically."""
        cfg = self.cfg
        token = (cfg.swarm.cluster_token or "").strip()
        if not cfg.swarm.require_token_for_inference or not token:
            return
        if self._is_local_client(request) or self._presents_cluster_token(
            request, token
        ):
            return
        raise HTTPException(
            status_code=401,
            detail=(
                "This netllm agent requires the swarm cluster token for "
                "inference. Send Authorization: Bearer <token>."
            ),
        )

    def require_read_access(self, request: Request) -> None:
        """Gate the read-only swarm endpoints once a cluster token exists.

        /netllm/v1/status returns every backend URL, the full model catalog,
        hostnames, agent ids, the peer list with versions, per-backend routed
        counts and token totals — complete fleet reconnaissance for anyone on
        the LAN (docs/architecture/07-findings-register.md F-13). It has to
        stay reachable for peer discovery, and it is: SwarmRegistry.fetch_peer
        and lan.fetch_agent_status already send the cluster token whenever one
        is configured.

        With no token configured this is a no-op, so the zero-config
        single-machine and open-trusted-LAN setups behave exactly as before.
        """
        token = (self.cfg.swarm.cluster_token or "").strip()
        if not token:
            return
        if self._is_local_client(request) or self._presents_cluster_token(
            request, token
        ):
            return
        raise HTTPException(
            status_code=401,
            detail=(
                "This netllm agent requires the swarm cluster token. "
                "Send Authorization: Bearer <token>."
            ),
        )

    def require_admin_access(self, request: Request) -> None:
        """Local client or Bearer cluster token, else 403.

        Resolved through the module rather than imported by name so the
        existing monkeypatch seam on ``netllm_agent.admin`` keeps working.
        """
        admin_module.require_admin_access(request, self.cfg)
