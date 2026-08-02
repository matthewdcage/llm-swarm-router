# Architecture & audit documentation

Reviewed release: **0.4.5.1** (tag) / **main @ a3cbad9** (unreleased refactor) ·
Audit date: **2026-07-29** · Last refreshed: **2026-08-03** · Branch: `main`

> **Remediation landed.** 20 of 29 findings were fixed (all S1 and S2, plus the
> low-risk S3 cleanups) across four commits on `docs/architecture-audit` — see the
> status column in [07-findings-register.md](07-findings-register.md). The original
> audit was taken at `a3ec16a`; `26c45b7` (mesh fix #36) landed mid-audit and
> is accounted for.
>
> **Consolidation landed (2026-08-01).** F-24, F-25 and F-26 — the two large
> refactors originally scoped out — are RESOLVED: one failover engine plus four
> surface adapters, one model-name matcher, both 2 kLOC modules dissolved. The
> operator-visible behaviour changes are enumerated in
> [refactor/RELEASE-NOTES.md](refactor/RELEASE-NOTES.md). Suite: **1,077 passing**
> on synced main (2026-08-03); **356** golden-vector contract tests; **1,108** full
> `uv run pytest -q`. Closure roadmap:
> [../closure-roadmap-2026-08-03.md](../closure-roadmap-2026-08-03.md).

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
| 03 | [Request lifecycle](03-request-lifecycle.md) | The engine/adapter architecture, the four request surfaces, routing decision flow, failover, streaming |
| 04 | [Discovery & swarm](04-discovery-and-swarm.md) | Local scan, mDNS, subnet scan, heartbeat gossip, peer state machine |
| 05 | [Configuration & control plane](05-configuration-and-control-plane.md) | Config model, the three write paths, admin API, schema-driven UI |
| 06 | [Dependencies](06-dependencies.md) | Internal graph, external packages, platform/build/CI dependencies |
| 07 | [Findings register](07-findings-register.md) | 29 verified findings with fix status: correctness, security, performance, simplification |
| 08 | [Feature integration status](08-feature-integration-status.md) | Shipped / partial / orphaned matrix for planning |
| 09 | [Follow-up audit 2026-07-31](09-follow-up-audit-2026-07-31.md) | F-30…F-53: product-outward audit at `c9bd30a` (wire fidelity, docs alignment, cross-surface consistency), plus post-audit entries F-54… |
| — | [refactor/](refactor/) | The F-24/F-25/F-26 consolidation: adopted plan, behavior matrix (D1–D18), module inventory, dependency graph, release notes |
| — | [Closure roadmap 2026-08-03](../closure-roadmap-2026-08-03.md) | Open-item inventory, adversarial merged-PR reviews, phased path to 0.5.0.0 release |

## Scope and method

**In scope:** the `packages/` Python workspace (6 packages, 16.2k LOC as of 2026-08-01),
the `apps/netllm-mac` Swift menubar app (9.1k LOC), the bundled web dashboard (3.6k LOC of
HTML/CSS/JS), `packaging/`, `scripts/`, and CI workflows.

**Verification performed during this audit:**

- Full test suite at audit time: **584 passed**; after remediation: **642 passed**; after the F-24/F-25/F-26 consolidation (2026-08-01): **1,087 passed, 4 skipped**; on synced main @ `a3cbad9` (2026-08-03): **1,108 passed** (`uv run pytest -q`).
- Reproduced 4 defects with executable scripts before fixing (F-01, F-02, F-04, F-11).
- Cross-checked every "orphan" claim with a repo-wide symbol grep including tests.
- Re-verified all four S1 findings against `26c45b7` before starting work — none
  were resolved by it, and F-03 was narrowed in one place and amplified in another.

**Not in scope:** the gitignored local-maintainer coordinator/outreach trees under
`.cursor/` (`plans/`, `outreach/`, `agents/`, `coordinator/`), and third-party upstream
inference servers. Note that `.cursor/hooks/` and `.cursor/rules/` *are* tracked and are
now covered by the repo-wide lint gate.

## Conventions used

- `path/file.py:123` — clickable evidence pointer into the repo.
- **Severity** — `S1` production-affecting correctness or security · `S2` real user-visible
  defect or meaningful risk · `S3` maintenance, clarity, or latent risk.
- **Orphaned** — code that exists and is reachable in principle but has no production
  caller or no way for a user to reach it.
- **Partially integrated** — the backend exists but at least one shipped client
  (CLI, dashboard, macOS app) cannot use it.
