# netllm-discovery

Parent: [../AGENTS.md](../AGENTS.md).

## Purpose

Local LLM provider discovery (oMLX, Ollama, LM Studio, vLLM), LAN peer registry, and optional mDNS swarm advertisement/browse.

## Ownership

Key modules: `local.py`, `swarm.py`, `mdns.py`, `lan.py`, `runtime.py`, `agent_lock.py`, `process_util.py`.

## Local Contracts

- Default probe ports: oMLX `:8080`, Ollama `:11434`, LM Studio `:1234`, vLLM `:8000`
- Custom ports via `[discovery].custom_endpoints` or `[[routing.backends]]` in config
- **`[discovery].ignored_urls` denylist**: `candidate_urls_for_provider` filters it out last (so a pin, an env hint and a default port are all covered) and `scan_local_providers` skips ignored `custom_endpoints`. The `[[routing.backends]]` probe loop is **deliberately unfiltered** — the explicit row wins, and `netllm_core.backend_credentials.ignored_url_keys` has already subtracted those URLs. Never mutate `provider_urls`/`custom_endpoints`/`routing.backends` to ignore something: one reversible line is the whole point
- mDNS requires `zeroconf` from `uv sync`; LAN swarm needs agent `serve --host 0.0.0.0`
- Open trusted-LAN mesh works with empty `cluster_token` (mDNS + subnet scan); set `swarm.cluster_token` only on untrusted networks or when using `join` pairing
- **Agent-hop routing:** `SwarmRegistry.peer_agent_backends()` emits one `Backend` per peer at `{listen_url}/v1`; never merge peer loopback oMLX URLs into a gateway pool
- **No transitive echo:** `_peer_backend_models()` unions only a peer's `local=true` rows — peers advertise models they serve directly, never their own remote `peer:` rows
- **`PeerRecord.routing_strategy` / `.version`** ride heartbeats and status fetches for config-drift detection; empty strings mean an older peer — treat as "unknown", never warn on them
- **`PeerRecord.max_concurrency` / `.draining`** also ride heartbeats/status fetches (`fetch_peer`, `handle_heartbeat` in `netllm-agent`); default `0`/`False` when a peer omits them (older version). `peer_agent_backends()` copies `max_concurrency` onto the peer's routable `Backend` row (checked by `netllm-core`'s capacity guard) and **omits a draining peer's row entirely** — draining removes a peer from every strategy's candidates on every gateway that receives its heartbeat, without touching requests it's already serving
- `lan.filter_own_peer_urls()` strips this host's agent URL from `swarm.peers` on save/scan
- **Address classification** (`lan.ADDRESS_KINDS` = `lan`/`vpn`/`container`/`link_local`/`loopback`): `classify_interface_address(interface, ip)` decides by **interface name**, not address range — `10.0.0.29` and `172.17.0.1` are both RFC1918 and only the interface says which is a Docker bridge gateway. Loopback and link-local are address facts and win over the name; `br-<hash>` is a compose bridge but plain `br0`/`bridge0` is **not** (a Linux host bridging its own NIC carries its real LAN address there); an unrecognised interface is `lan`, because hiding a real address is worse than showing a useless one. `classified_agent_endpoints(port, interfaces=…, primary_url=…)` orders `primary → lan → vpn → container → link_local → loopback` and is the single producer behind `status_payload()`'s `reachable_at`/`also_reachable_at`. Enumeration (`local_ipv4_interfaces()`) needs `psutil` and returns `[]` without it — never raises
- **`agent_lock.py`**: flock singleton at `{state_dir}/agent.lock` (parent of log dir); `serve` acquires before port preflight; stale PID reclaim; see [docs/dev-docs/agent-singleton-hardening-plan.md](../../docs/dev-docs/agent-singleton-hardening-plan.md)
- `lan.subnet_scan_agents()` returns **one row per agent_id** (`dedupe_agents_by_id`): multi-homed hosts keep the row matching their reported listen_url, other IPs land in `also_reachable_at`; `fetch_agent_status` preserves `reported_listen_url` alongside the probe URL
- LM Studio auth tokens: `LMSTUDIO_API_KEY` env or `[[routing.backends]]` `api_key` / `api_key_env` (scan uses `netllm_core.backend_credentials.resolve_api_key_for_url` per pinned URL; request paths unchanged)

## Extension contract

- **Owns:** no registry. `KNOWN_PROVIDERS` and `DEFAULT_API_KEYS` in
  `local.py` keep their public shape but are **comprehensions over**
  `netllm_core.local_providers.LOCAL_PROVIDERS` — they are derived, not
  copies, and `tests/conformance/kit_local.py::test_discovery_roster_is_derived_not_mirrored`
  pins that.
- **Consumes only.** Discovery reads the local-provider registry; it never
  states a provider fact of its own. A port, label or env-var name that
  belongs to a provider belongs on `LocalProviderSpec`.
- **The one permitted id literal:** `provider != "omlx"` on the admin and
  telemetry probes. oMLX exposes a proprietary admin API no other local
  server has, so that literal marks a real **capability**, not a roster. It
  is ledgered with that reason, and
  `tests/conformance/kit_cloud.py::test_omlx_admin_probe_is_the_only_provider_specific_branch`
  pins that it stays the **only** one — a second means the capability belongs
  on the spec instead.
- **No new mirrors:** never add a provider id literal here. Generic
  behaviour keyed on a spec field (`host_env`, `port_env`) is the pattern —
  it is what deleted `if provider_id == "ollama"`.
- **Adding a provider:** [docs/extending/02-local-provider.md](../../docs/extending/02-local-provider.md).

## Work Guidance

- Discovery results feed `netllm-core` routing; keep scan logic side-effect free where possible
- Optional `mdns` extra on `netllm-agent` for zeroconf

## Verification

```bash
./netllm discover
./netllm peers      # with agent on 0.0.0.0
./scripts/ci.sh test
```

## Child DOX Index

None — flat `src/netllm_discovery/` package.
