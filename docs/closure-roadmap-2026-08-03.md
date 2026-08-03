# Closure roadmap — 2026-08-03

Inventory and merge path after syncing with `origin/main` @ `a3cbad9` (PR #42 squash).
Adversarial sub-agent reviews ran on merged PRs #41, #42, and #43; findings are recorded
below and reflected in the architecture register where applicable.

## Remote sync status (2026-08-03)

| Ref | Commit | Status |
|-----|--------|--------|
| **origin/main** | `1835c8b` | **v0.5.0.0 shipped** — engine refactor (#42) + Homebrew (#44) |
| **Phase B PR** | `fix/routing-pools-integrated` | Post-0.5.0.0 closeout → targets **0.5.0.1** |
| **Local `main` worktree** (`llm-swarm-router-menubar-fix`) | `fe21331` | **Behind** — run `git pull` before release work |
| **Latest tag** | `v0.5.0.0` | Engine consolidation; Phase B not yet on tag |
| **Open PRs** | 1 pending | Phase B closeout → `main` |
| **Open GitHub issues** | #10, #16 | See below |
| **Tests (Phase B tree)** | 1108+ passed | `uv run pytest -q` |
| **Contract tests** | 363 passed | `uv run pytest tests/contract -q` |

---

## Adversarial review summary (merged PRs)

### PR #41 — audit + F-30…F-53 remediation

**Verdict:** Safe merge; S1 Anthropic streaming (F-30) credibly closed.

| Severity | Finding | Action |
|----------|---------|--------|
| HIGH | F-38 marked RESOLVED but upstream 401/429 still map to 502; malformed JSON → raw 500 | **Closed Phase B** — shaped JSON 400 + 401/429 policy documented; upstream forward intentionally unchanged |
| HIGH | F-34 deferred but Windows docs still claim working SCM service | Docs PR or WinSW/NSSM host |
| MEDIUM | Anthropic path lacks F-42 control-kwarg stripping | Parity audit on `/v1/messages` native path |
| MEDIUM | Secured swarm: dashboard `fetch()` has no token — remote LAN UI 401s | Token entry in `/ui/` or document same-host-only |
| LOW | Empty upstream stream counts as success in StreamSession | Edge-case metrics skew |

### PR #42 — F-24/F-25/F-26 consolidation

**Verdict:** Structurally sound; **0.5.0.0 released**.

| Severity | Finding | Action |
|----------|---------|--------|
| IMPORTANT | Request-aware pools not in operator `RELEASE-NOTES.md`; no contract vector | Add D19 + golden vector before tag |
| IMPORTANT | F-56 — vector rename bypasses divergence lint | **Closed Phase B** — stable `id` → HEAD path in `test_divergence_lint.py` |
| IMPORTANT | D17 batch_shard failover restarts at list head (declared) | User-facing release notes + `live-routing-smoke.sh` |
| IMPORTANT | D4 embeddings guard → 400 for unknown encoder names | Release notes + alias workaround |
| MEDIUM | F-57 capability guard leaks rewritten model id in 400 body | **Closed Phase B** — 400 quotes `requested_model`; vector `guards-rewrite-capability-400-chat-s` |
| MEDIUM | ModelResolver oracles don't cover pool two-phase candidacy | **Closed Phase B** — `naming-model-pools-isolation-multi-host` |

Contract suite: **363 passed** on Phase B tree (356 @ v0.5.0.0 tag; +1 B5 pool-isolation vector). `allowed-divergences.txt` empty (strong gate).

### PR #43 — menubar status sync + v0.4.5.1

**Verdict:** Safe merge; menubar race fixed.

| Severity | Finding | Action |
|----------|---------|--------|
| IMPORTANT | Settings `AgentSupervisor.statusLabel` still uses cached state — same split-display class | **Closed Phase B** — live `server.state.settingsStatusLabel` + Swift unit tests |
| IMPORTANT | No automated test for menubar header ↔ Start/Stop alignment | Partial — Swift tests + lifecycle L5b; GUI manual: [menubar-adopt-smoke.md](solutions/menubar-adopt-smoke.md) |
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
| F-28 | S3 | Packaging limits (arm64-only, ad-hoc DMG, subnet scan IDS) | **RESOLVED B7 docs (0.5.0.0)** |
| F-29 | S3 | oMLX logic in generic discovery package | C |
| F-34 | S2 | Windows SCM service broken (deferred) | C — docs fix or WinSW |
| F-38 | S3 | Error envelopes | **RESOLVED Phase B** |
| F-39 | S3 | Codex Responses — offline tests pass; live gate: [codex-responses-smoke.md](solutions/codex-responses-smoke.md) | B — manual |
| F-54 | S3 | dashboard.js monolith (2,825 LOC) | C |
| F-55 | S3 | Re-export shims (`config`, `install_detect`) | C |

**Resolved on main (0.5.0.0):** F-24, F-25, F-26, F-30–F-37, F-40–F-48, F-50–F-53. **Resolved Phase B (this PR → 0.5.0.1):** F-28, F-38, F-56, F-57, B5 pool-isolation vector.

### Plan docs — material gaps

| Plan | Remaining | Priority |
|------|-----------|----------|
| `routing-hardening-plan.md` | Phase 4 `model_groups` (weighted UI) — future; Phase 3 macOS admin API migration | P3 |
| `cli-source-routing-plan.md` | Live scenario validation only — **`netllm connect` shipped**, dashboard scenario counters shipped | P3 |
| `config-schema-rewrite-plan.md` | Swift routing/cloud schema migration | P2 |
| `cloud-providers-plan.md` | `static_models` drift (F-23); hot-apply cloud keys | P3 |
| Coordinator dispatch bridge | **Complete** — all plan todos done | — |

### Branches to clean up

| Branch | Action |
|--------|--------|
| `fix/routing-pools-integrated` | Delete after Phase B PR merges |
| `origin/homebrew/v0.3.0.2` … `v0.4.5.1` | Delete — superseded |
| `origin/homebrew/v0.5.0.0` | Refresh on **0.5.0.1** tag |
| Local merged feature branches (`feat/*`, `fix/swarm-*`, etc.) | Delete after sync |
| Dependabot GH Actions (local) | Batch PR + merge |

---

## Prioritized closure phases

### Phase A — Immediate — **complete (v0.5.0.0 tagged)**

1. ~~Sync all worktrees~~
2. ~~CI gate~~
3. ~~Version bump 0.5.0.0~~
4. ~~Release notes~~
5. ~~Tag + GitHub release~~
6. ~~Homebrew formula bump (#44)~~
7. ~~Register close-out~~

### Phase B — Short-term PRs — **complete in this PR (→ 0.5.0.1)**

| # | Work | Effort | Status |
|---|------|--------|--------|
| B1 | F-56 + F-57 contract hygiene | S | ✅ |
| B2 | F-38 partial completion (JSON parse → shaped 400; document 401/429 policy) | M | ✅ |
| B3 | Source/scenario counters in dashboard Serving tab | S | ✅ |
| B4 | `netllm connect <tool>` CLI | M | ✅ |
| B5 | Contract vector: request-aware pool multi-host isolation | S | ✅ `naming-model-pools-isolation-multi-host` |
| B6 | Settings statusLabel parity (PR #43 follow-up) | S | ✅ |
| B7 | F-28 packaging limits docs | S | ✅ |
| B8 | Dependabot GH Actions batch | S | ✅ |
| B9 | F-39 live Codex smoke (manual) | S | Partial — [codex-responses-smoke.md](solutions/codex-responses-smoke.md) |

### Phase C — Long-term backlog

- F-21 config schema finish (Swift + drift test)
- F-54 dashboard.js ES module split + JS lint
- F-23 catalog hash heartbeats
- #16 notarization (credentials/process)
- F-34 Windows real service host
- Phase 4 `model_groups` weighted UI
- F-55 shim removal, F-29 oMLX extraction

---

## Pre-release checklist (0.5.0.1 — Phase B)

```bash
git checkout main && git pull origin main
./scripts/ci.sh
./scripts/verify-before-pr.sh --full   # menubar touched
uv run pytest tests/contract -q      # 363 tests
scripts/live-routing-smoke.sh        # maintainer LAN
./netllm doctor && ./netllm test
```

Manual:

- [x] Settings hero label reads live server state (Phase B B6)
- [x] Lifecycle L5b adopt + `settingsStatusLabel` (`test-menubar-lifecycle.sh`)
- [ ] Menubar header aligned after adopt (GUI) — [menubar-adopt-smoke.md](solutions/menubar-adopt-smoke.md)
- [x] Mixed model pool — contract vector `naming-model-pools-isolation-multi-host` (CI)
- [ ] Codex live smoke — [codex-responses-smoke.md](solutions/codex-responses-smoke.md)
- [ ] batch_shard under partial failure (D17 attempt count)

---

## Decision log

| Decision | Rationale |
|----------|-----------|
| **0.5.0.0 not 0.4.6.0** | PR #42 introduces declared behavior changes (D4, D11, D17, D18, pools) beyond patch scope |
| **0.5.0.1 for Phase B** | Connect CLI, contract vectors, menubar lifecycle — patch after 0.5.0.0 tag |
| **No reopen of merged PRs** | Adversarial reviews found follow-ups, not merge blockers |
| **F-38 → RESOLVED (Phase B)** | Shaped JSON 400; upstream 401/429 → 502 documented and intentional |
| **Coordinator dispatch** | Plan complete; optional manual `/coordinator` + `/packages` verification only |

Updated: 2026-08-03 (Phase B closeout PR post-v0.5.0.0)
