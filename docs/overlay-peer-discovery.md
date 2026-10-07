# Overlay peer discovery (VPN / NetBird / Tailscale)

netllm discovers swarm peers on the **LAN** via mDNS, optional subnet scan, and optional static `swarm.peers` entries. Overlay addresses (NetBird `wt*`, Tailscale `tailscale0`, WireGuard `wg*`, macOS `utun*`) do not participate in multicast mDNS.

## Gossip-first design (zero extra config)

When `swarm.overlay_discovery` is **`auto`** (default):

1. Every heartbeat and status fetch carries UI-4a fields: `reachable_at`, `also_reachable_at`, and `providers`. No schema change beyond what mixed-version meshes already tolerate ([mesh-upgrade.md](mesh-upgrade.md)).
2. The gateway **records every dialable URL** the peer advertises into `known_peer_urls` with source label `gossip`. Rediscovery re-probes those URLs after sleep or Wi‑Fi blips.
3. **Routing** prefers LAN paths, then VPN, using the same `ADDRESS_KINDS` ordering as the dashboard ([`netllm_discovery.lan`](../packages/netllm-discovery/src/netllm_discovery/lan.py)).
4. **Health probes** walk candidate URLs in that order; status may include per-path `address_health` when the agent has probed recently.
5. **Agent-hop failover**: one connect/timeout retry per request on the next candidate URL for the same `agent_id` (no global flip-flop).

Gossip URLs are **not** auto-written into `config.toml` `swarm.peers` (avoids config churn). Pin a peer manually if you need persistence across agents that never see that peer on the overlay.

Set `swarm.overlay_discovery = "off"` to restore enrollment via mDNS, static peers, subnet scan, and heartbeat `listen_url` only.

## Non-goals

- **No CGNAT sweep** of `100.64.0.0/10` or arbitrary `subnet_cidrs` without explicit opt-in. Tailscale/NetBird best practice is control-plane membership plus known peer URLs, not blind overlay scans.
- **mDNS stays LAN-only** (multicast does not cross overlays).
- **Firewall / ACLs** on the overlay are out of scope; TCP `:11400` between enrolled peers must be allowed.

## Maintainer baseline (evidence)

Before changing discovery behavior, capture from the gateway:

```bash
./netllm status
./netllm peers
./netllm doctor
curl -s http://127.0.0.1:11400/netllm/v1/status | jq '.peers[] | {agent_id, listen_url, reachable_at, also_reachable_at, discovered_via}'
```

Note which `listen_url` each `peer:*` backend row uses in `backends[]` and whether heartbeats return 401 (cluster token mismatch).

## Live validation checklist

1. Peer advertises both LAN and VPN in `reachable_at`; Peers page shows VPN kind after Linux `wt0` classification fix.
2. With LAN path blocked, status highlights VPN as active path; chat to a model on that peer still routes.
3. `./netllm doctor` notes when VPN alternates exist but LAN is unreachable.
4. `overlay_discovery = "off"`: gossip URLs are not added to `known_peer_urls` from heartbeats.
