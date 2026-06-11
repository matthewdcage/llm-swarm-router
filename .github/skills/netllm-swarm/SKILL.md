---
name: netllm-swarm
description: |
  Configure multi-machine LAN mesh for swarm-llm (netllm). Use when the user
  asks to set up a swarm, connect multiple machines (macOS, Linux, Windows),
  enable LAN routing, find peers via mDNS, configure a gateway, or invokes
  /netllm-swarm. Covers init --swarm, netllm join, swarm-token pairing,
  local_spillover load spreading, peers discovery, static peers, and
  gateway role.
version: 1.1.0
license: MIT
compatibility:
  - cursor
  - codex
  - claude-code
  - copilot
allowed-tools:
  - Read
  - Shell
  - Grep
---

# netllm swarm

## When to use this skill

- Multiple machines should share one logical model pool
- User says "LAN swarm", "multi-machine netllm", "find peers", or `/netllm-swarm`
- mDNS or static peer configuration needed

## Prerequisites

- netllm installed on each machine (`netllm-setup` skill or `./netllm init`)
- Same LAN/VLAN (guest Wi-Fi often blocks mDNS)
- Local inference on at least one host (optional on workers if they only gateway)

## Workflow (DMG / trusted home LAN — default)

1. **Each machine:** install app → welcome **Listen on LAN** (or Settings LAN toggle) → restart agent
2. **Clients:** `export OPENAI_BASE_URL=http://<any-LAN-IP>:11400/v1` (Honcho, Cursor, etc.)
3. **Verify:** `./netllm peers` — agents find each other via mDNS / subnet scan (no token)

Open swarm: empty `swarm.cluster_token` is intentional on trusted LANs.

## Workflow (CLI — same open default)

1. **Each machine:**
   ```bash
   ./netllm init --swarm    # upgrades existing config; no --force
   ./netllm serve
   ```
   Binds `0.0.0.0:11400`, selects `local_spillover`, enables `subnet_scan` — **no token**.

2. **Verify the mesh**
   ```bash
   ./netllm peers          # both machines listed; flags unreachable loopback binds
   ./netllm models --lan
   ./netllm status
   ```

## Workflow (manual — existing configs)

1. **Bind for LAN on each host**
   ```bash
   ./netllm serve --host 0.0.0.0
   ```
   Packaged installs (systemd, Windows service, macOS app) may already bind via config, use `./netllm start` / `./netllm restart` after changing listen address.
   Confirm `agent.advertise = true` in `~/.config/netllm/config.toml` (default in [config.example.toml](../../../config.example.toml)).

2. **Firewall**, allow inbound TCP `11400` and UDP `5353` (mDNS) on each host — `./netllm doctor` prints per-platform commands.

3. **Security on untrusted LANs** (optional): Settings **Require cluster token**, or `./netllm init --swarm --secure`, or `./netllm swarm-token --create`. Then on other machines: `./netllm join http://<leader-ip>:11400 --token <token>`. Heartbeats require Bearer token when set; open swarms reject join-with-token.

4. **Discover peers** (from any machine on the LAN)
   ```bash
   ./netllm peers
   ```
   LAN-bound agents also run a one-shot subnet scan automatically when mDNS
   finds no peers within 10s. If still none:
   ```bash
   ./netllm peers --subnet-scan
   ./netllm peers --subnet-scan --save   # persist to swarm.peers
   ```

5. **Optional gateway**, on one designated machine:
   ```bash
   ./netllm gateway
   ./netllm serve --host 0.0.0.0   # restart after role change
   ```
   Point clients (Honcho, editors) at this host's URL only.

6. **Manual peers**, if mDNS blocked, add to config:
   ```toml
   [swarm]
   peers = ["http://192.168.1.50:11400", "http://192.168.1.51:11400"]
   ```

## Examples

**Two machines on home Wi-Fi**

| Machine | Command |
|---------|---------|
| MacBook (Ollama) | Menubar LAN on + restart agent (or `./netllm init --swarm && ./netllm serve`) |
| Linux box (vLLM) | Same — LAN bind; open mesh needs no join on trusted LAN |
| Either | `./netllm peers` then `./netllm models --lan` |

**Guest network (no mDNS)**

```bash
./netllm peers --subnet-scan --save
./netllm serve   # restart to load new peers
```

## Agent-hop routing (gateway + mesh)

When Honcho or another client points at **one gateway** (`http://<gateway>:11400/v1`), netllm routes to:

- Local inference on the gateway (`127.0.0.1:8080/v1`, etc.)
- **Peer agents** at `http://<peer-LAN-IP>:11400/v1` (not the peer's loopback oMLX URL)

For same model on multiple machines, the guided setup already selects
`local_spillover` (serve locally while idle, spill to the least-loaded peer
when local concurrency reaches `routing.spillover_max_local_in_flight`).
Alternatives:

```toml
[routing]
default_strategy = "local_spillover"   # swarm default (init --swarm / join)
# default_strategy = "round_robin"     # strict alternation, ignores load
allow_remote = true
spillover_max_local_in_flight = 2
```

Agent-hops carry `x-netllm-local-only`, so a peer never re-forwards a hop —
any node can run a distributing strategy safely.

**Mixed providers, same weights?** Map names once so the catalog merges:

```toml
[routing.model_aliases]
"llama3" = ["llama3:8b-instruct-q4_K_M", "Meta-Llama-3-8B-Instruct-GGUF"]
```

Confirm traffic reaches peers:

```bash
./netllm status   # remote backend rows show http://<peer>:11400/v1
curl -s http://127.0.0.1:11400/metrics | rg netllm_requests_total
curl -s http://<peer-LAN-IP>:11400/metrics | rg netllm_requests_total
```

## Edge cases

| Situation | Action |
|-----------|--------|
| No peers found | `./netllm doctor` (firewall UDP 5353 / TCP 11400 hints), same subnet, `--subnet-scan`, manual `swarm.peers` |
| Peer "found but unreachable" | That machine is loopback-bound: enable LAN in menubar app, `netllm init --swarm`, or `serve --host 0.0.0.0` there |
| Join rejected (401) | Token mismatch — `netllm swarm-token` on a joined machine, re-run `join` |
| Join rejected (token vs open swarm) | The target has no token set — rotate one there first, or join without `--token` |
| mDNS unavailable | `uv sync` or `./netllm doctor`; static peers still work |
| Linux browse empty | Ensure Avahi running; see [docs/linux-install.md](../../docs/linux-install.md) |
| Windows browse empty | Prefer `swarm.peers` or `--subnet-scan`; see [docs/windows-install.md](../../docs/windows-install.md) |
| Models missing remotely | Remote host needs online backends; check `./netllm status --url <peer>`; different provider naming → `[routing.model_aliases]` |
| Gateway hits wrong host:8080 | Peer must be routable as `http://<LAN-IP>:11400/v1`; do not add own agent URL to `swarm.peers` |

## Do not

- Expose `0.0.0.0` without warning about `cluster_token` on public networks
- Commit machine-specific IPs to the repo without user consent
