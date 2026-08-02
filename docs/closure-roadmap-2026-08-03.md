# Closure roadmap — 2026-08-03

Inventory and merge path after syncing with `origin/main` @ `a3cbad9` (PR #42 squash).
Adversarial sub-agent reviews ran on merged PRs #41, #42, and #43; findings are recorded
below and reflected in the architecture register where applicable.

## Remote sync status (2026-08-03)

| Ref | Commit | Status |
|-----|--------|--------|
| **origin/main** | `a3cbad9` | Canonical — F-24/F-25/F-26 consolidation + request-aware pools (#42) |
| **Local checkout** (after reset) | `a3cbad9` | **Synced** |
| **Local `main` worktree** (`llm-swarm-router-menubar-fix`) | `fe21331` | **16 commits behind** — run `git pull` there before release work |
| **Latest tag** | `v0.4.5.1` | Menubar status fix (#43); **does not include** #42 refactor on tag |
| **Open PRs** | 0 | All recent work merged (#41, #42, #43) |
| **Open GitHub issues** | #10, #16 | See below |
| **Tests on synced tree** | 1108 passed | `uv run pytest -q` (2026-08-03) |

---

## Adversarial review summary (merged PRs)

### PR #41 — audit + F-30…F-53 remediation

**Verdict:** Safe merge; S1 Anthropic streaming (F-30) credibly closed.

| Severity | Finding | Action |
|----------|---------|--------|
| HIGH | F-38 marked RESOLVED but upstream 401/429 still map to 502; malformed JSON → raw 500 | Reclassify F-38 **PARTIAL**; follow-up PR for parse errors + status policy |
| HIGH | F-34 deferred but Windows docs still claim working SCM service | Docs PR or WinSW/NSSM host |
| MEDIUM | Anthropic path lacks F-42 control-kwarg stripping | Parity audit on `/v1/messages` native path |
| MEDIUM | Secured swarm: dashboard `fetch()` has no token — remote LAN UI 401s | Token entry in `/ui/` or document same-host-only |
| LOW | Empty upstream stream counts as success in StreamSession | Edge-case metrics skew |

### PR #42 — F-24/F-25/F-26 consolidation

**Verdict:** Structurally sound; **minor/0.5.0.0 release** warranted before tag.

| Severity | Finding | Action |
|----------|---------|--------|
| IMPORTANT | Request-aware pools not in operator `RELEASE-NOTES.md`; no contract vector | Add D19 + golden vector before tag |
| IMPORTANT | F-56 — vector rename bypasses divergence lint | Manual rename review or stable-id diff |
| IMPORTANT | D17 batch_shard failover restarts at list head (declared) | User-facing release notes + `live-routing-smoke.sh` |
| IMPORTANT | D4 embeddings guard → 400 for unknown encoder names | Release notes + alias workaround |
| MEDIUM | F-57 capability guard leaks rewritten model id in 400 body | Small PR: guard on `requested_model` |
| MEDIUM | ModelResolver oracles don't cover pool two-phase candidacy | Integration test + contract vector |

Contract suite: **356 passed** on synced tree. `allowed-divergences.txt` empty (strong gate).

### PR #43 — menubar status sync + v0.4.5.1

**Verdict:** Safe merge; menubar race fixed.

| Severity | Finding | Action |
|----------|---------|--------|
| IMPORTANT | Settings `AgentSupervisor.statusLabel` still uses cached state — same split-display class | Apply live-read pattern to Settings |
| IMPORTANT | No automated test for menubar header ↔ Start/Stop alignment | Extend `test-menubar-lifecycle.sh` |
| LOW | README/platform-matrix still badge v0.4.5.0 | Docs-only bump |

---

## Open work inventory

### GitHub issues

| Issue | Title | Priority | Closure path |
|-------|-------|----------|--------------|
| [#16](https://github.com/matthewdcage/llm-swarm-router/issues/16) | macOS source-build until notarization | P1 | Close when CI ships notarized DMG; until then keep as tracking |
| [#10](https://github.com/matthewdcage/llm-swarm-router/issues/10) | In-app update UX polish | P3 | Focused macOS PR or close if v0.3.0.2+ fixes cover worst cases |

### Findings register — still open

| ID | Severity | Summary | Phase |
|----|----------|---------|-------|
| F-20 | S3 | Admin allowlist wider than "this machine" | C — long-term |
| F-21 | S3 | Config schema triple-mirror (Swift routing/cloud, dashboard.js) | C |
| F-23 | S3 | N×N heartbeat full catalogs | C |
| F-28 | S3 | Packaging limits (arm64-only, ad-hoc DMG, subnet scan IDS) | B — docs PR |
| F-29 | S3 | oMLX logic in generic discovery package | C |
| F-34 | S2 | Windows SCM service broken (deferred) | C — docs fix or WinSW |
| F-38 | S3 | Error envelopes **PARTIAL** (see PR #41 review) | B |
| F-39 | S3 | Codex Responses — live verification deferred | B — manual |
| F-54 | S3 | dashboard.js monolith (2,825 LOC) | C |
| F-55 | S3 | Re-export shims (`config`, `install_detect`) | C |
| F-56 | S3 | Contract lint rename blind spot | B |
| F-57 | S3 | Capability guard naming leak | B |

**Resolved on main (release close-out):** F-24, F-25, F-26, F-30–F-37, F-40–F-48, F-50–F-53.

### Plan docs — material gaps

| Plan | Remaining | Priority |
|------|-----------|----------|
| `routing-hardening-plan.md` | Phase 4 `model_groups` (weighted UI) — future; Phase 3 macOS admin API migration | P3 |
| `cli-source-routing-plan.md` | `netllm connect`, source/scenario counters in Serving UI, live scenario validation | P2 |
| `config-schema-rewrite-plan.md` | Swift routing/cloud schema migration | P2 |
| `cloud-providers-plan.md` | `static_models` drift (F-23); hot-apply cloud keys | P3 |
| Coordinator dispatch bridge | **Complete** — all plan todos done | — |

### Branches to clean up

| Branch | Action |
|--------|--------|
| `fix/routing-pools-integrated` | Delete after switching default branch checkout to `main` |
| `origin/homebrew/v0.3.0.2` … `v0.4.1.0` | Delete — superseded |
| `origin/homebrew/v0.4.5.1` | Refresh on next release tag |
| Local merged feature branches (`feat/*`, `fix/swarm-*`, etc.) | Delete after sync |
| Dependabot GH Actions (local) | Batch PR + merge |

---

## Prioritized closure phases

### Phase A — Immediate (this week)

1. **Sync all worktrees:** `git checkout main && git pull origin main` in every checkout.
2. **CI gate:** `./scripts/ci.sh` + `./scripts/verify-before-pr.sh` on synced main.
3. **Version bump:** Recommend **0.5.0.0** (engine refactor + declared divergences D4, D17, D18, request-aware pools).
4. **Release notes:** User-facing `docs/release-notes/v0.5.0.0.md` — promote items from `docs/architecture/refactor/RELEASE-NOTES.md` plus request-aware pools (D19).
5. **Tag + GitHub release** when hardware smoke passes (`scripts/live-routing-smoke.sh`, `./netllm test`).
6. **Homebrew formula** bump from new tag; delete stale `homebrew/v0.3*` branches.
7. **Register close-out:** Update `07-findings-register.md` header (test count, F-24/F-25/F-26 at release commit).

### Phase B — Short-term PRs (~1–2 weeks)

| # | Work | Effort |
|---|------|--------|
| B1 | F-56 + F-57 contract hygiene | S |
| B2 | F-38 partial completion (JSON parse → shaped 400; document 401/429 policy) | M |
| B3 | Source/scenario counters in dashboard Serving tab | S |
| B4 | `netllm connect <tool>` CLI | M |
| B5 | Contract vector: request-aware pool multi-host isolation | S |
| B6 | Settings statusLabel parity (PR #43 follow-up) | S |
| B7 | F-28 packaging limits docs | S |
| B8 | Dependabot GH Actions batch | S |
| B9 | F-39 live Codex smoke (manual) | S |

### Phase C — Long-term backlog

- F-21 config schema finish (Swift + drift test)
- F-54 dashboard.js ES module split + JS lint
- F-23 catalog hash heartbeats
- #16 notarization (credentials/process)
- F-34 Windows real service host
- Phase 4 `model_groups` weighted UI
- F-55 shim removal, F-29 oMLX extraction

---

## Pre-release checklist (0.5.0.0)

```bash
git checkout main && git pull origin main
./scripts/ci.sh
./scripts/verify-before-pr.sh          # macOS: add --full if menubar touched
uv run pytest tests/contract -q      # 356 vectors
scripts/live-routing-smoke.sh        # maintainer LAN
./netllm doctor && ./netllm test
```

Manual:

- [ ] Menubar header aligned after adopt (PR #43 scenario)
- [ ] Mixed model pool: literal model hits serving peer, not catch-all overflow
- [ ] Codex `/v1/responses` telemetry in Serving tab (D16)
- [ ] batch_shard under partial failure (D17 attempt count)

---

## Decision log

| Decision | Rationale |
|----------|-----------|
| **0.5.0.0 not 0.4.6.0** | PR #42 introduces declared behavior changes (D4, D11, D17, D18, pools) beyond patch scope |
| **No reopen of merged PRs** | Adversarial reviews found follow-ups, not merge blockers |
| **F-38 → PARTIAL** | Body shaping fixed; status passthrough and parse errors incomplete |
| **Coordinator dispatch** | Plan complete; optional manual `/coordinator` + `/packages` verification only |

Updated: 2026-08-03
