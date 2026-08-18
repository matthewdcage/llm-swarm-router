# Developer plans (dev-docs)

Committed intent documents for netllm features and hardening work. These plans
describe *what we are building*; [`docs/architecture/`](../architecture/README.md)
describes *what is built*.

## Status legend

| Marker | Meaning |
|--------|---------|
| **Planned** | Documented only; no code yet |
| **In progress** | Active implementation branch |
| **Phase N done** | That phase shipped; later phases may remain open |
| **Complete** | All phases in the plan resolved or explicitly deferred |

## Active plans

| Plan | Status | Summary |
|------|--------|---------|
| [agent-singleton-hardening-plan.md](agent-singleton-hardening-plan.md) | Phase 1 done | One agent per host: flock lock, serve integration, systemd `--replace` |
| [agent-singleton-as-built.md](agent-singleton-as-built.md) | Reference | Evidence map of singleton guards before/after Phase 1 |
| [agent-singleton-acceptance.md](agent-singleton-acceptance.md) | Reference | Manual and automated acceptance gates |
| [distributed-inference-roadmap.md](distributed-inference-roadmap.md) | Planned | Measured link probing, prefix affinity, cross-node prompt-cache migration, llama.cpp RPC (phases 0–5) |

## Related docs (repo root `docs/`)

| Doc | Role |
|-----|------|
| [routing-hardening-plan.md](../routing-hardening-plan.md) | Mesh routing and peer health |
| [cli-source-routing-plan.md](../cli-source-routing-plan.md) | Per-source identity and policy |
| [architecture/07-findings-register.md](../architecture/07-findings-register.md) | F-nn defect register |

## Agents

Load [AGENTS.md](AGENTS.md) when editing this tree. Update the plan doc status
table when a phase lands; mark findings **RESOLVED** in the architecture audit
in the same PR.
