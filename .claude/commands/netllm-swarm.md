---
description: Configure multi-machine LAN mesh for netllm (mDNS, peers, gateway)
allowed-tools: Read, Shell, Grep
---

Load and follow the skill at `.claude/skills/netllm-swarm/SKILL.md` (fallback: `.agents/skills/netllm-swarm/SKILL.md`).

Walk the user through LAN swarm setup: `serve --host 0.0.0.0` on each machine, peer discovery, optional gateway, and `models --lan` verification.

- Warn about `swarm.cluster_token` when listening on `0.0.0.0`.
- Do not commit machine-specific IPs unless the user asks.
