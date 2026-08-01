# docs/architecture — architecture reference and audit

## Purpose

Two things in one folder, deliberately kept together so they cannot drift apart:

1. **Reference** (01–06) — how the system is built: components, request lifecycle, swarm
   behaviour, configuration/control plane, dependency map. Intended to stay true.
2. **Audit** (07–09) — point-in-time registers of defects, risks, and integration gaps,
   plus the product-facing shipped/partial/orphaned matrix. 09 continues 07's F-nn
   namespace at F-30.

Index: [README.md](README.md). Parent rail: [../AGENTS.md](../AGENTS.md).

## Ownership

| Doc | Owns |
|-----|------|
| `01-system-overview.md` | topologies, container diagram, stack, state inventory, endpoint table |
| `02-component-architecture.md` | package graph, per-module responsibility tables, domain model |
| `03-request-lifecycle.md` | the request surfaces, the engine/adapter split, routing decision flow, failover, loop guards |
| `04-discovery-and-swarm.md` | both discovery planes, peer state machine, gossip, network/security model, timing constants |
| `05-configuration-and-control-plane.md` | config model, the three write paths, merge semantics, admin API, source identity |
| `06-dependencies.md` | internal graph, external/undeclared deps, platform + CI + release dependencies |
| `07-findings-register.md` | F-01…F-29 with severity, evidence, and fix |
| `08-feature-integration-status.md` | shipped / partial / orphaned per client surface; product decisions |
| `09-follow-up-audit-2026-07-31.md` | F-30…F-53 with severity, evidence, and fix (same contracts as 07; IDs continue the register namespace), plus a **Post-audit entries** tail (F-54…) for IDs opened after this audit closed |

## Local Contracts

- **Every claim carries evidence.** Assertions about behaviour cite `path/file.py:line`.
  Reproduced defects state that they were reproduced and show the observed output.
- **Findings are append-only in ID.** Never renumber `F-nn`; mark a fixed finding
  `RESOLVED (<version>, <PR>)` in place and keep it. Other docs and PRs reference the IDs.
- **Severity means the same thing every time.** `S1` production-affecting correctness or
  security · `S2` real user-visible defect or meaningful risk · `S3` maintenance/latent.
- **Diagrams are Mermaid in Markdown.** No binary image assets, no external rendering
  service — they must render on GitHub and in any Markdown viewer.
- **Reference and audit are separated.** Do not fold a finding into 01–06 as if it were
  designed behaviour; link to the `F-nn` instead.
- This folder does not duplicate user-facing install/troubleshooting guidance — link to
  `../macos-install.md` and siblings.

## Work Guidance

- **Re-audit on a release boundary**, not per PR. Update the header line in
  [README.md](README.md) (release, date, commit) whenever any doc here is refreshed.
- Fixing a finding: change its entry to `RESOLVED`, update the traceability matrix row, and
  update the matching row in `08-feature-integration-status.md` if a surface changed.
- Adding a config field, endpoint, strategy, or package: update the owning table in 01–06 in
  the same PR (the tables are the reason this folder exists).
- Plan docs (`../routing-hardening-plan.md`, `../cloud-providers-plan.md`,
  `../cli-source-routing-plan.md`, `../config-schema-rewrite-plan.md`,
  `../models-ux-plan.md`) hold *intent*; this folder holds *as-built*. When they disagree,
  fix the plan doc and note it in `08`'s roadmap table.

## Verification

- All relative links resolve from [README.md](README.md).
- Every `file:line` reference still points at the code it describes.
- Mermaid blocks parse (no unescaped `|` inside node labels, no bare `[]` in text).
- Counts stated in prose (findings, packages, LOC, test count) match reality — re-run
  `uv run pytest -q` and the LOC counts rather than carrying them forward.
