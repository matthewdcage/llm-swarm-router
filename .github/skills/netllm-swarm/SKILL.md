---
name: netllm-swarm
description: |
  Configure multi-machine LAN mesh for swarm-llm (netllm). Use when the user
  asks to set up a swarm, connect multiple machines (macOS, Linux, Windows),
  enable LAN routing, find peers via mDNS, configure a gateway, or invokes
  /netllm-swarm. Covers serve --host 0.0.0.0, peers discovery, static peers,
  and gateway role.
version: 1.0.0
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

## Workflow

1. **Per machine: init if needed**
   ```bash
   ./netllm init
   ```

2. **Bind for LAN on each host**
   ```bash
   ./netllm serve --host 0.0.0.0
   ```
   Packaged installs (systemd, Windows service, macOS app) may already bind via config, use `./netllm start` / `./netllm restart` after changing listen address.
   Confirm `agent.advertise = true` in `~/.config/netllm/config.toml` (default in [config.example.toml](../../../config.example.toml)).

3. **Firewall**, when listening on `0.0.0.0`, allow inbound TCP on the agent port (default `11400`) on each host.

4. **Security on untrusted LANs**, recommend setting `swarm.cluster_token` in config when using `0.0.0.0`. Warn user if token is empty.

5. **Discover peers** (from any machine on the LAN)
   ```bash
   ./netllm peers
   ```
   If none found:
   ```bash
   ./netllm peers --subnet-scan
   ./netllm peers --subnet-scan --save   # persist to swarm.peers
   ```

6. **Optional gateway**, on one designated machine:
   ```bash
   ./netllm gateway
   ./netllm serve --host 0.0.0.0   # restart after role change
   ```
   Point clients (Honcho, editors) at this host's URL only.

7. **Verify merged catalog**
   ```bash
   ./netllm models --lan
   ./netllm status
   ```

8. **Manual peers**, if mDNS blocked, add to config:
   ```toml
   [swarm]
   peers = ["http://192.168.1.50:11400", "http://192.168.1.51:11400"]
   ```

## Examples

**Two machines on home Wi-Fi**

| Machine | Command |
|---------|---------|
| MacBook (Ollama) | `./netllm serve --host 0.0.0.0` |
| Linux box (vLLM) | `netllm serve --host 0.0.0.0` after deb/rpm install |
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

For same model on multiple machines, set:

```toml
[routing]
default_strategy = "round_robin"
allow_remote = true
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
| No peers found | Check firewall, same subnet, `--subnet-scan`, manual `swarm.peers` |
| mDNS unavailable | `uv sync` or `./netllm doctor`; static peers still work |
| Linux browse empty | Ensure Avahi running; see [docs/linux-install.md](../../docs/linux-install.md) |
| Windows browse empty | Prefer `swarm.peers` or `--subnet-scan`; see [docs/windows-install.md](../../docs/windows-install.md) |
| Loopback only | `127.0.0.1` bind hides agent from LAN: must use `0.0.0.0` |
| Models missing remotely | Remote host needs online backends; check `./netllm status --url <peer>` |
| Gateway hits wrong host:8080 | Peer must be routable as `http://<LAN-IP>:11400/v1`; do not add own agent URL to `swarm.peers` |

## Do not

- Expose `0.0.0.0` without warning about `cluster_token` on public networks
- Commit machine-specific IPs to the repo without user consent
