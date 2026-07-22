# Turnstone integration

[Turnstone](https://github.com/turnstonelabs/turnstone) is a self-hosted agent
harness — it gives an LLM tools (shell, files, search, web, MCP servers) and
manages the multi-turn tool-use loop, with a judge that risk-grades tool calls
and a cluster dashboard for multiple Turnstone nodes. It is not a model router:
it expects you to hand it an existing OpenAI-compatible (or Anthropic, or
Gemini) endpoint via `--base-url` and does no backend discovery, health
checking, or failover of its own.

That is exactly the gap **netllm** fills. netllm discovers local inference
servers (oMLX, Ollama, LM Studio, vLLM), meshes them across a LAN, and exposes
one stable OpenAI-compatible surface at `http://127.0.0.1:11400/v1` (plus a
native Anthropic Messages surface) with health-aware routing and cloud
failover behind it. Point Turnstone's `--base-url` at that surface and it
gets swarm routing for free, without knowing the swarm exists.

```
Turnstone (turnstone-server / turnstone-console)
        │  --base-url http://127.0.0.1:11400/v1
        ▼
netllm agent  ──mesh──  sibling netllm agents (LAN)
        │
   oMLX / Ollama / LM Studio / vLLM / cloud fallback
```

This is additive only — nothing below changes netllm's own behavior or
config surface. It's a usage pattern for pointing an external tool at an
existing netllm agent.

## Prerequisites

```bash
./netllm serve                 # netllm agent on http://127.0.0.1:11400
./netllm models                # note an exact model ID from the table
curl -sf http://127.0.0.1:11400/health && echo ok
```

## Single-node: Turnstone → one netllm agent

```bash
pip install turnstone

# Terminal REPL
turnstone --base-url http://127.0.0.1:11400/v1

# Browser UI
turnstone-server --port 8080 --base-url http://127.0.0.1:11400/v1
```

Turnstone has no netllm-specific auth; it just needs a bearer token in the
`OPENAI_API_KEY` slot. netllm's local agent doesn't validate that value, so
any placeholder works:

```bash
export OPENAI_API_KEY=netllm-local
```

Model IDs shown in the Turnstone UI must match `./netllm models` exactly —
same constraint as any other OpenAI-compatible client (see
[editor-integration.md](editor-integration.md)).

## Multi-machine: Turnstone nodes across a netllm swarm

Turnstone's own multi-node story (`turnstone-console`) shards **workstreams**
(agent sessions) across Turnstone server nodes — a different axis from
netllm's swarm, which shards **inference requests** across model backends.
The two compose cleanly: run one netllm agent per machine (LAN swarm) and
point every local Turnstone node at its own machine's netllm agent rather
than at a single shared one.

```bash
# on each machine
./netllm serve --host 0.0.0.0    # joins the LAN mesh
turnstone-server --port 8080 --base-url http://127.0.0.1:11400/v1
```

Each Turnstone node gets local-first routing with automatic LAN spillover
(`local_spillover`, netllm's default swarm strategy) if its own machine is
saturated — Turnstone never needs to know which physical box actually served
a given completion. If you'd rather centralize, point every Turnstone node at
one netllm gateway (`./netllm gateway` on one host) instead; that trades
per-node local-first behavior for a single routing decision point.

Turnstone's console (`turnstone-console`) and netllm's routing are
independent control planes — you get two dashboards (netllm's `/ui/` for
backend/model health, Turnstone's console for workstream/tool activity), not
one merged view. See [Practical gains from Turnstone](turnstone-lessons.md)
for whether unifying them is worth pursuing.

## Docker Compose

Turnstone's `docker compose up` brings up Postgres, console, Caddy, a channel
gateway, and 10 server nodes with no local model backend — nodes "boot
without an LLM; add model backends from the console UI." Point that at a
netllm agent running on the Docker host:

```yaml
# in Turnstone's server node service definition
environment:
  - OPENAI_BASE_URL=http://host.docker.internal:11400/v1
  - OPENAI_API_KEY=netllm-local
```

`host.docker.internal` requires the netllm agent to bind `0.0.0.0` (LAN
listen) or be reachable via Docker's host networking — same requirement as
any other containerized client reaching a host-run netllm agent (see the
Docker row in [editor-integration.md](editor-integration.md)).

## Programmatic (Turnstone SDK)

No netllm-specific change needed — Turnstone's SDK talks to `turnstone-server`,
not directly to the model backend, so the `--base-url` wiring above is
sufficient:

```python
from turnstone.sdk import TurnstoneServer

with TurnstoneServer("http://localhost:8080", token="tok_xxx") as client:
    ws = client.create_workstream(name="demo")
    result = client.send_and_wait("Analyze the error logs", ws.ws_id, auto_approve=True)
```

## What this does *not* give you

- **No tool execution in netllm.** netllm only routes chat/completions
  requests; it has no concept of Turnstone's tools, MCP servers, or judge.
  All of that lives entirely in Turnstone.
- **No shared session/workstream state.** netllm doesn't know a "workstream"
  exists — it sees a stream of stateless chat requests. Session affinity (if
  you want the same physical backend serving a whole conversation) is not
  guaranteed by any current netllm strategy; see
  [turnstone-lessons.md](turnstone-lessons.md) point 2 for what would close
  that gap.
- **No auth passthrough.** Turnstone's RBAC/SSO/token scopes stop at
  `turnstone-server`; netllm's agent has no user-level auth model (LAN trust
  or `swarm.cluster_token` only). Don't expose a netllm agent directly to
  untrusted Turnstone clients without one of those.

## Verify

```bash
./netllm test --model <your-model>          # netllm side
curl -sf http://127.0.0.1:8080/health        # turnstone-server side (adjust port)
```
