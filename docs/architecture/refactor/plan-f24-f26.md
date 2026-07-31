# Adopted Plan — F-24 + F-25 + F-26 Consolidation (Synthesis)

**Winner: incremental-strangler sequencing**, amended with (a) test-first-contract's vector harness and divergence-annotation gating as the universal Phase 0 and gate mechanism, (b) engine-first's `surfaces/` adapter end-state and anti-erosion gate replacing the strangler's sibling `proxy.py`/`messages.py` files, and (c) test-first-contract's mechanical patch-target repoint (deny-greps + canaries) replacing both namespace-shim proposals.

All line references are HEAD `64f14a4` provenance (`git show HEAD:<path>`); where the in-flight remediation moves code, the reference names the concern.

---

## 0. Hard preconditions (entry criteria for everything)

1. The F-30..F-48 remediation branch is **merged**. Verify, don't assume: F-30 (anthropic SDK `stream=` fixed), F-32 (stream pre-flight/stream split; `app.py:409–486` except clauses live), F-33 (M-S full success accounting + usage-chunk parse on both stream wrappers), F-35 (embeddings payload adaptation in the SDK).
2. Full suite green on the merged base (647+ tests, 09 baseline) plus `ruff` and type-check clean — this exact command set becomes the per-phase gate.
3. All work happens on **`claude/refactor-f24-f26-consolidation`**, branched from the merged remediation. See §6 for branch/merge strategy.

## 1. Target end state

Adopt the strangler's netllm-core additions and phase order, but the service package lands in **engine-first's shape** — no sibling loop files:

```
packages/netllm-core/src/netllm_core/
  request_plan.py      # RequestPlan, Surface enum (RESPONSES is NOT a Surface — edge translation only)
  model_resolution.py  # ModelResolver — the single alias/pool/catalog matcher (F-25 core)

packages/netllm-agent/src/netllm_agent/service/
  __init__.py    # AgentService mixin composition; re-exports AgentService, SourceCapacityExceeded,
                 # LEGACY_CLOUD_BACKEND_IDS. NO scan_local_providers shim (see §4.3).
  core.py        # __init__, apply_config (cluster A, s:105–159, 251–272)
  backends.py    # refresh/scan (C, s:199–320) + upstream construction/peer headers (F, s:651–715);
                 # scan_local_providers imported HERE — tests patch netllm_agent.service.backends.*
  cloud.py       # cluster J verbatim (s:1359–1626), F-04 comments travel
  policy.py      # cluster G (s:717–823) + header/model utilities from E; owns build_request_plan()
  selection.py   # cluster H (s:825–972) + CandidateSchedule construction
  accounting.py  # AttemptRecorder — the ONE place success/failure accounting lands
  engine.py      # run_with_failover (non-stream) + open_stream/StreamSession (stream);
                 # the only failover loop in the codebase
  surfaces/
    base.py      # SurfaceAdapter protocol + shared SSE restore helpers (s:622–649)
    chat.py      # ChatAdapter (invoke/invoke_stream, error shaper, restore)
    embeddings.py# EmbeddingsAdapter (incl. NEW capability guard)
    messages.py  # MessagesAdapter (openai-arm + anthropic-arm invokers, s:1956–2017 glue,
                 # anthropic fallback expressed as CandidateSchedule.fallback tier)
    responses.py # edge translation over ChatAdapter (s:1206–1235 behavior)
  status.py      # cluster D minus heartbeat + cluster B telemetry sinks
  swarm_tasks.py # cluster L (s:2046–2246) + heartbeat/gateway (s:414–458)

packages/netllm-cli/src/netllm_cli/
  main.py        # ~130-line Typer wiring; retains `app`, the literal
                 # `from netllm_core.version import get_version` + `__version__ = get_version()`
                 # lines (test_version_sync.py:60–63), and the __main__ block (build.sh:120)
  commands/      # _common, init_install, join_swarm, observe, serve_lifecycle,
                 # diagnose, config_io, cloud, sources (inventory Part 2.4 verbatim)
```

Key types: `RequestPlan` (frozen; source, scenario, requested_model, canonical_model, routing, shard, normalized headers, immutable payload), `CandidateSchedule` (primary + extra_candidates + fallback tiers + explicit `max_attempts` = sum over everything), `AttemptRecorder` (success/failure, sole caller of `mark_success`/`_mark_backend_failure`/`is_capacity_error` p:39–55/REQUESTS_TOTAL/latency/tokens/`_request_count`), `SurfaceAdapter` (guard, candidates, build_invocation, invoke/invoke_stream, extract_usage, restore_model/restore_stream_line, classify_error, exhaustion_error, mid_stream_error_frame, wire_error — engine-first's protocol, adopted verbatim as the whole per-surface variance budget).

**Frozen external contracts** (asserted by a new API-surface test added in Phase 0): `netllm_agent.service` exports `AgentService`, `SourceCapacityExceeded`, `LEGACY_CLOUD_BACKEND_IDS`; the 20-item surface app.py:34 consumes; `AgentService.<method>` class-attribute patchability; CLI `app`/entry points/`-m` runnability.

**Anti-erosion gate** (from engine-first, permanent CI): `engine.py` may not reference `Surface` members or import any `surfaces/*` module except `base`; `rg` gate in CI. New per-surface needs extend the protocol, never branch the loop.

**Deliberately dropped:** engine-first's `NETLLM_LEGACY_ENGINE` runtime flag. Per-surface migration (Phases 6–7) already gives per-surface git revert; a dual-dispatch flag doubles the tested state space for the exact phases where streaming semantics are most fragile.

## 2. Phase 0 — Contract-vector harness (test-only; the universal gate mechanism)

Adopt test-first-contract's harness spec wholesale, under `tests/contract/`:

- **FakeFarm**: in-process scriptable backends (httpx MockTransport/ASGI) speaking both wire dialects, injected **below the SDK clients** (patch transports, not service methods — the F-30 lesson, 09:88–99). Behavior scripts: `ok`, `ok_stream(chunks, usage_in_final)`, `http(status)` for {409, 429, 500, 502, 503, 507}, `capacity_body_marker` (p:42–47), `midstream_drop(after_n)`, `hang`, `bad_json`. Records every inbound request verbatim (path, headers, body, model field).
- **ServiceProbe**: fresh Prometheus CollectorRegistry per test; snapshots pool state, telemetry, `_source_counts`/`_scenario_counts`/`_source_in_flight` before/after; returns a normalized delta dict.
- **ContractVector**: one JSON doc per scenario × path — `{request, expected: {status, body_shape, sse_frames, upstream_calls (order + per-backend model + headers incl. peer loop-guard), pool_delta, metrics_delta, admission_delta}}` — checked in under `tests/contract/vectors/`, regenerable via `--record`. Canonicalizer with an explicit volatile-field schema (latency → ">0", EMA → "updated", ids/timestamps normalized).
- **Path drivers**: all seven — C-NS, C-S, EMB, M-NS, M-S, RESP-NS, RESP-S — driven through the real `app.py` routes (TestClient), so edge translation and route-layer error mapping are inside the tested surface.
- **Scenario axis** (~30 scenarios): happy path per strategy incl. `batch_shard` ± shard headers; failover (hard-fail-then-ok; capacity-429-then-ok asserting no offline trip p:241–249; exhaustion per surface pinning the 404-hint s:572–588 vs 401-keyless s:1804–1808 split; attempt cap with cloud extras present); model naming (alias exact/tag-prefix/casefold, pools, rewrites+scenario stacking, restore on every shape incl. both stream dialects, plus one **adversarial alias set** where matchers A p:386–399/455–462 and B s:533–570 could disagree — recorded as-is at baseline as the F-25 regression trap); cloud topology (prefer_cloud rp:29–32, legacy row, materialized rows, anthropic fallback ordering); guards & policy (dialect-typed 400s s:590–620; chat-model→/v1/embeddings baseline burn; EMB scenario misclassification baseline; source-cap 429 incl. on stream=true; local-only/hops/strategy/pin headers); streaming (mid-stream drop → dialect error frame + no-retry per `yielded_any`; pre-first-byte failover invisible to client; usage-chunk parse; client disconnect → release/in-flight balance under GeneratorExit; frame-boundary splits mid-line); peer forwarding (loop-guard headers present on peer rows, absent otherwise).
- **Divergence annotations**: every vector cell embodying a known divergence carries `"divergence": ["D7"]` (D1–D15 = matrix list). **The universal gate rule, enforced by a lint in CI: mechanical commits change zero vectors; semantic commits may change only vectors annotated with the divergence IDs that commit declares; every vector diff without a matching ID fails the PR.** This replaces both the strangler's reviewer-discipline goldens and engine-first's prose waiver list with a machine check.
- Also in Phase 0: the API-surface freeze test (§1), and a dedicated characterization test per item of the matrix's "constant across all paths" list (attribution/scenario counted once per request, admit-before-first-await + finally-release, capacity status set {409,429,503,507}+markers, `_offload_if_probing`, pin/strategy/local-only plumbing) so no later phase can silently drop one.

**Exit criteria**: vectors recorded on the merged-remediation base; determinism ×20 CI job green (run twice, diff); annotations reconcile 1:1 against divergences D1–D15, with D1, D2-partial, D3, D15 verified `resolved-pre-refactor` by the remediation (tripwire that the assumed fixes actually landed); zero production diff. ~+3,000 test lines.

## 3. Phases 1–8 — strangler sequencing (each phase = one PR into the feature branch; every semantic flip its own commit)

**Phase 1 — CLI split (bank it early, fully independent).** Mechanical move to `commands/` per inventory 2.4. Same-commit repoint of the 12 `netllm_cli.main.*` patch targets and the 5 directly-imported helpers; deny-grep in CI (`rg 'netllm_cli\.main\.(asyncio|httpx|mdns_available|control_socket_path|global_)' tests/` empty) + one canary test per repointed family (patched fake raises a sentinel; assert intercepted). `main.py` keeps `app`, the two version-sync literals, `__main__`. Update the `SettingsWindowView.swift:692` comment.
*Exit*: full suite green; vectors untouched; `python -m netllm_cli.main --help` and installed-wheel `netllm --help` smokes in CI; deny-greps + canaries green. *Rollback*: single revert; nothing depends on it.

**Phase 2 — Accounting unification (D1, D2).** Introduce `AttemptRecorder` (still inside service.py). Replace all inline success blocks (s:1051–1062, 1320–1331, 1674–1685, post-F-33 stream wrappers) and failure blocks (s:1067–1071, 1336–1339, 1688–1691, 1914, 2040–2043). Semantic commit 2b [D2]: C-S pre-first-byte failures record `mark_backend_failure` + error REQUESTS_TOTAL exactly once (dedup guard vs the stream wrapper — the exact drift 07:685–688 documented).
*Exit*: vectors identical except D2-annotated cells; "counted exactly once" assertion under fail-then-succeed; metrics-parity check (identical request script → identical counter deltas pre/post).

**Phase 3 — Error taxonomy (D11 partial).** `is_capacity_error` reachable only via the recorder. Extract `exhaustion_error(plan, last)` reproducing today's per-surface outcomes verbatim. Semantic commit 3c [D11]: Messages route forwards `OpenAIUpstreamError` 400/404 instead of flattening to 502 (a:485–486 vs a:418–424). Rider 3d [D11/F-38]: per-surface exception handlers producing dialect-native error bodies via what will become `adapter.wire_error` — it lives at exactly this seam and never gets cheaper; contract-test the bodies with the real `openai`/`anthropic` client libraries parsing them.
*Exit*: exhaustive status-code table test (surface × {capacity, exhausted-with-error, exhausted-keyless, unknown-model}); only D11-annotated vectors change.

**Phase 4 — RequestPlan builder (D4, D10, D12, D14; stages D5).** Extract `build_request_plan()` from the five verbatim prologues (s:974–1000, 1091–1117, 1237–1269, 1704–1733, 1813–1842); mechanical commit first. Then one semantic commit each: 4b [D10] Messages stops mutating `payload["model"]` up-front (s:1722–1723, 1831–1832) — cross-alias-retry vector; 4c [D4] capability guard becomes a plan step for all surfaces incl. a **new** embeddings guard (cap:53–68, OpenAI-typed 400) — user-visible tightening, release-noted; 4d [D14] scenario classification gains a surface input; rules without a surface qualifier keep matching (compat default) so chat rules stop silently applying to embeddings only on opt-in; 4e [D12] normalize headers once, delete the a:471 duplicate. Shard context (D5) is extracted for all surfaces into `plan.shard` but not yet fed to selection.
*Exit*: 4a byte-identical vectors; 4b–4e each flip only their annotated cells.

**Phase 5 — CandidateSchedule + selection (D5, D6, D7, D8, D13).** Extract `build_candidates(plan, pool, cloud_extra)`. [D8] `tried` becomes pure failure exclusion; format eligibility moves to schedule construction (replaces the three pre-seedings: none / s:1281–1283 / s:1746–1750+1851–1855). [D6] both cloud topologies declarative (extra_candidates+prefer_cloud vs fallback tier). [D7, semantic] `max_attempts` = explicit sum over the schedule (OpenAI paths gain up to +1 attempt when the legacy row is injected; Messages fallback becomes bounded) — property test "attempts ≤ max_attempts, every candidate tried at most once, tier order respected". [D5, semantic] flip shard context on for EMB/M-NS/M-S. [D13, semantic-defensive] attach `_peer_forward_headers` (s:651–670) on the anthropic-format arm (s:1970–1977, 1999–2006) with a simulated-anthropic-peer test.
*Exit*: only D5/D6/D7/D8/D13-annotated vectors change, one commit each; capacity-storm scenario asserts bounded attempts and correct `capacity_rejections` vs offline-trip behavior (p:241–262).

**Phase 6 — Engine, non-streaming (three sub-PRs: C-NS, then EMB, then M-NS).** Extract `engine.run_with_failover` and the `SurfaceAdapter` protocol; migrate one surface per sub-PR, deleting its bespoke loop (s:1011–1087 / s:1278–1355 / s:1751–1809) and supplying its adapter. Old loops keep serving unmigrated surfaces — the strangler property: at every merge, some surfaces new, some old, all green. The anti-erosion grep-gate lands with the first sub-PR.
*Exit per sub-PR*: vectors byte-identical for the migrated surface (the heart of the design); acquire/release pairing asserted under injected mid-invoke exceptions; admission finally-release verified.

**Phase 7 — Engine, streaming (D9; risk peak; sub-PRs: C-S, then M-S, then RESP-S wiring).** `open_stream` runs select→acquire→connect→first-event inside the loop and returns a `StreamSession` only after upstream connect (the post-F-32 shape made structural). `StreamSession` (engine-first's type) owns: per-line restore, `yielded_any` no-replay (failure after first byte ⇒ dialect error frame via `adapter.mid_stream_error_frame` + terminate), usage-chunk capture → `recorder.success`, shard success, and release **including on client disconnect/GeneratorExit**. Semantic commits: [D9] M-S translated stream restores the requested model (Anthropic `message_start.message.model` frames); M-S error frame gains its terminator event (explicit annotated vector change). Adversarial pin for D15: a non-`AnthropicUpstreamError` (TypeError-class) during connect must surface as a mapped HTTP status, never 200/aborted. Delete the M-S `candidates_exhausted`/`fallback_iter` state machine (s:1861–1954) in one commit for clean revert.
*Exit*: full streaming battery (frame sequences, boundary-split fuzzing, disconnect-release, error-frame goldens both dialects, no-replay, usage-absent tolerance) + the **pre-merge soak**: 15–30 min mixed workload on FakeFarm (all paths, concurrency > per-source caps, 10% injected failures, 5% mid-stream drops, random client aborts, one backend flapping 503) asserting in-flight gauges and `_source_in_flight` return to zero, acquire/release balanced, `REQUESTS_TOTAL` = harness-observed count exactly, memory stable.

**Phase 8 — F-25 matcher unification (shadow mode, from the strangler).** 8a: implement `ModelResolver` (one precedence: alias exact → tag-prefix → casefold → pool intersection p:433–453 → catalog passthrough; `serves()` derived from the same walk, honoring blind-catalog p:522–525 and auth-gated skip p:510–521; `known_models()` for 404 hints p:464–476). Run in **shadow** beside matchers A/B across the full suite + a Hypothesis property test over generated alias/pool/catalog corpora **with the legacy matchers as oracles** (engine-first's amendment); log every disagreement. 8b: switch candidacy and invocation to the resolver; delete A and B; requires zero unexplained shadow disagreements (each triaged bug-or-intent first); reverts to shadow in one commit. 8c: `model_pools` parses into the resolver's internal groups representation (a pool = a group), so future `model_groups` (routing-hardening-plan.md:167–176) is schema-only — "fold, don't coexist" satisfied at the data-model level; collapse `netllm_core.config`→`models` and `install_detect` re-export shims (one release of deprecation stubs); ship the routing-precedence table (globals→policies→source→scenario→headers, rp:76–95) in the config reference, **backed by a test asserting the documented order against `resolve_routing`** so it cannot drift.
*Exit*: adversarial matcher-divergence vector now provably consistent; fixture-corpus replay of every alias/pool fixture in tests/; property tests green.

**Phase 9 — F-26 service split (pure move).** service.py is now ~1,500 lines of thin code. One mechanical PR to the §1 layout (engine/surfaces shape). **Patch-target decision: mechanical repoint** (test-first-contract's choice) — all 25 `patch("netllm_agent.service.scan_local_providers", …)` sites become `netllm_agent.service.backends.scan_local_providers` in the same PR, with a deny-grep (`rg 'netllm_agent\.service\.scan_local_providers' tests/` empty) and a canary test proving interception. The repoint is safe *here*, at the end, precisely because the move is over final content and the vector corpus proves mechanicalness independently of the unit tests being repointed. No namespace shim: module-global indirection must not become load-bearing.
*Exit*: vectors byte-identical; zero test edits other than the repoints + canaries (greppable proof); `git diff --color-moved` review; import-time smoke of every module; API-surface test green; `git log --follow` sanity.

**Phase 10 — Cleanup riders.** F-49 slice (docs/telemetry-api.md normative; delete dashboard.js client-side `total_tokens` re-derivation; Python contract test on the documented key set — structural JS split deferred, filed as a new register entry). Delete any remaining strangler shims. Update 07-findings-register.md closing F-24/F-25/F-26 with commit hashes; update 03-request-lifecycle.md for the annotated behavior changes (EMB guard, attempt-cap semantics, M-S restore/terminator, messages error forwarding).

## 4. Adversarial verification regime (per phase, independent of the implementer)

Each phase gate is checked by an **independent verifier session** (fresh context, no access to the implementer's reasoning) whose brief is refutation, not confirmation:

1. **Vector audit**: re-run the contract suite from a clean checkout twice; diff vectors against the phase's declared divergence IDs; any un-annotated diff = gate failure. Attempt `--record` and diff the regenerated corpus against the checked-in one (catches nondeterminism the ×20 job missed).
2. **Streaming-abort attack** (Phases 2, 6, 7 mandatory): drive each stream path and cancel the client at chunk 0, chunk 1, mid-frame, and post-usage-chunk; assert pool in-flight, `BACKEND_IN_FLIGHT`, and `_source_in_flight` all return to zero and no second backend ever received bytes after the first yielded byte (FakeFarm request log is the oracle).
3. **Counter-parity attack**: run one identical scripted scenario through every applicable path and assert pool/metrics/admission deltas are field-identical across paths (only wire dialect differs) — the standing refutation attempt against the "fix lands on one loop only" generator (09:410–414).
4. **Patch-canary attack** (Phases 1, 9): for every repointed target, patch with a sentinel-raising fake and assert the sentinel fires; grep for any test still patching an old namespace.
5. **Shadow-disagreement audit** (Phase 8b only): independently re-run the shadow comparison on the full suite + a fresh Hypothesis seed; any disagreement not in the implementer's triage log blocks the switch.
6. **Erosion grep** (Phase 6 onward): run the engine.py anti-erosion gate and additionally attempt to find any `if plan.surface`/`isinstance(adapter, …)` branching inside engine/StreamSession.

Verifier findings are reported via the findings tool/PR review; a phase does not merge with an open CONFIRMED finding.

## 5. Full-suite + lint gates (every phase, no exceptions)

`uv run pytest` (full suite, 647+ baseline), `tests/contract/` suite with vector-diff lint, `ruff check` + format check, type check, determinism job (contract suite ×2), deny-greps, `scripts/verify-before-pr.sh` (repo pre-push contract), and for Phases 1/9 the entry-point/`-m` smokes. Phases 7 and 8b additionally require the soak and shadow-audit respectively. One manual two-node LAN smoke (`test_e2e_two_agents` fixture / netllm-swarm flow) before Phase 7 merges and once after Phase 9 — the only coverage for real mDNS/peer paths FakeFarm cannot exercise.

## 6. Branch and merge strategy

- All work on **`claude/refactor-f24-f26-consolidation`**, branched from the merged F-30..F-48 remediation. Each phase is a PR **into the feature branch**; a phase PR merges only when its exit criteria, the full-suite/lint gates, and the adversarial verifier are all green. Phases are strictly ordered except Phase 1 (CLI), which may proceed in parallel any time after Phase 0.
- Every semantic flip is its own commit inside its phase PR (revertible independently); mechanical moves are move-only commits (reviewed with `--color-moved`).
- The feature branch rebases onto main only at phase boundaries; any main-side change to `packages/` between phases triggers vector re-verification before the next phase starts.
- **The single PR from `claude/refactor-f24-f26-consolidation` to main merges only when every phase gate (0–10) is green**, the findings register updates are included, and the release notes enumerate the annotated behavior changes (D2, D4-EMB, D5, D7, D9, D10, D11/3c, D13, D14-opt-in, M-S terminator). Rollback story post-merge: each phase is a contiguous, individually revertible commit range; surface migrations (6/7 sub-PRs) revert per-surface.

## 7. Risk register (top 5, merged from all three designs)

1. **Streaming lifecycle leaks** (GeneratorExit/cancel → stuck in-flight, creeping 429s): Phase 0 disconnect vectors + verifier attack #2 + Phase 7 soak as merge gates, not follow-ups.
2. **Rebase hazard vs remediation**: hard precondition §0; Phase 0's D1/D2/D3/D15 `resolved-pre-refactor` verification is the tripwire that the assumed fixes actually landed; vectors recorded only on the merged base.
3. **Patch-target silent non-interception** (25 + 12 sites): mechanical repoints with deny-greps and canaries (§4.4); no namespace shims anywhere.
4. **Matcher switch changing field routing** (Phase 8b): shadow mode + oracle property tests + fixture replay + independent shadow audit; one-commit revert to shadow.
5. **Vector brittleness / rubber-stamping**: canonicalizer with explicit volatile schema; determinism ×20; the divergence-ID lint makes an unexplained vector diff a hard CI failure rather than a reviewer judgment call.

Estimated totals: ~+3,000 test lines (Phase 0), production roughly LOC-neutral (~1,650 new engine/adapter/plan lines replacing ~1,550 loop lines), largest resulting module ≤ ~500 lines, both 2.1–2.2 kLOC monoliths dissolved. End state: exactly one implementation each of accounting, failover, capacity classification, error shaping, model resolution, and stream pumping — with a checked-in vector corpus that converts any future one-loop-only fix into a same-day parity failure.