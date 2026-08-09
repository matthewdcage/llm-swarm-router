# docs/dev-docs — developer plans

Parent: [../AGENTS.md](../AGENTS.md).

## Purpose

Committed planning documents for multi-phase netllm work. Unlike `.cursor/plans/`
(local, gitignored), everything here ships on the remote repo.

## Ownership

| Path | Contract |
|------|----------|
| `README.md` | Index and status legend |
| `agent-singleton-hardening-plan.md` | Singleton lock program (phases 0–5) |
| `agent-singleton-as-built.md` | `file:line` evidence for guards |
| `agent-singleton-acceptance.md` | Verification checklist |

## Local Contracts

- Plans hold **intent**; [`../architecture/`](../architecture/) holds **as-built**
  truth. When they disagree after a merge, update the plan status and architecture
  reference tables in the same PR.
- Every phase lists an exit gate (tests, manual steps, or both).
- New findings opened during planning use the next `F-nn` ID in the latest
  architecture audit tail; mark **RESOLVED** when the phase ships.
- User-facing install/troubleshoot changes link from plans but live in
  `../linux-troubleshooting.md`, `../macos-troubleshooting.md`, etc.

## Work Guidance

- Add a row to `README.md` when creating a new plan doc.
- Keep plans actionable: problem, design, phases, PR slicing, out of scope.
- Do not duplicate full architecture prose — link to `02-component-architecture.md`.

## Verification

- Relative links resolve from `README.md`.
- Phase status in the plan matches what actually merged.

## Child DOX Index

None — flat folder until a plan subtree needs its own rail.
