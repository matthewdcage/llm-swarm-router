# Architecture & audit documentation

Reviewed release: **0.4.5.0** · Audit date: **2026-07-29** · Branch: `main` @ `a3ec16a`

This set documents **llm-swarm-router** (`netllm`) as built: every component, every
dependency, the routing and control-plane logic, and a severity-ranked register of
inconsistencies, logic defects, hardening gaps, and features that are present in the
code but not fully wired into the product.

It was produced by reading the source (not the marketing docs) and verifying claims
against a live checkout. Every finding in [07-findings-register.md](07-findings-register.md)
carries a `file:line` reference and, where practical, a reproduction that was actually run.

## Read this in your role

| You are | Start here | Then |
|---------|-----------|------|
| **Product manager** | [08-feature-integration-status.md](08-feature-integration-status.md) — what is shipped, partial, or orphaned | [07-findings-register.md](07-findings-register.md) §Severity summary |
| **New engineer** | [01-system-overview.md](01-system-overview.md) | [02-component-architecture.md](02-component-architecture.md) → [03-request-lifecycle.md](03-request-lifecycle.md) |
| **Reviewing a routing change** | [03-request-lifecycle.md](03-request-lifecycle.md) | [07-findings-register.md](07-findings-register.md) §Routing & concurrency |
| **Reviewing a config/UI change** | [05-configuration-and-control-plane.md](05-configuration-and-control-plane.md) | [07-findings-register.md](07-findings-register.md) §Configuration integrity |
| **Network / deployment** | [04-discovery-and-swarm.md](04-discovery-and-swarm.md) | [06-dependencies.md](06-dependencies.md) §Runtime & platform |
| **Release / build owner** | [06-dependencies.md](06-dependencies.md) | [../ci-and-release.md](../ci-and-release.md) |

## Document index

| # | Document | Covers |
|---|----------|--------|
| 01 | [System overview](01-system-overview.md) | Product shape, deployment topologies, container diagram, tech stack |
| 02 | [Component architecture](02-component-architecture.md) | Package-by-package responsibilities, domain model, module map |
| 03 | [Request lifecycle](03-request-lifecycle.md) | The four proxy surfaces, routing decision flow, failover, streaming |
| 04 | [Discovery & swarm](04-discovery-and-swarm.md) | Local scan, mDNS, subnet scan, heartbeat gossip, peer state machine |
| 05 | [Configuration & control plane](05-configuration-and-control-plane.md) | Config model, the three write paths, admin API, schema-driven UI |
| 06 | [Dependencies](06-dependencies.md) | Internal graph, external packages, platform/build/CI dependencies |
| 07 | [Findings register](07-findings-register.md) | 29 verified findings: correctness, security, performance, simplification |
| 08 | [Feature integration status](08-feature-integration-status.md) | Shipped / partial / orphaned matrix for planning |

## Scope and method

**In scope:** the `packages/` Python workspace (6 packages, ~13.3k LOC), the `apps/netllm-mac`
Swift menubar app (~7.5k LOC), the bundled web dashboard (~3.4k LOC of HTML/CSS/JS),
`packaging/`, `scripts/`, and CI workflows.

**Verification performed during this audit:**

- Full test suite: `uv run pytest -q` → **584 passed** in 50 s (clean).
- Reproduced 4 defects with executable scripts (see findings F-01, F-02, F-06, F-11).
- Cross-checked every "orphan" claim with a repo-wide symbol grep including tests.

**Not in scope:** the local-maintainer coordinator/outreach trees under `.cursor/`
(gitignored, never on the remote), and third-party upstream inference servers.

## Conventions used

- `path/file.py:123` — clickable evidence pointer into the repo.
- **Severity** — `S1` production-affecting correctness or security · `S2` real user-visible
  defect or meaningful risk · `S3` maintenance, clarity, or latent risk.
- **Orphaned** — code that exists and is reachable in principle but has no production
  caller or no way for a user to reach it.
- **Partially integrated** — the backend exists but at least one shipped client
  (CLI, dashboard, macOS app) cannot use it.
