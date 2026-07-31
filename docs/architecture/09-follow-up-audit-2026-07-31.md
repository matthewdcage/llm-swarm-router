# 09 · Follow-up audit — 2026-07-31

Top-down audit of `main` @ `c9bd30a` (post-0.4.5.0, two commits after the
2026-07-29 baseline audit in [07-findings-register.md](07-findings-register.md)).
Conducted as six parallel audit dimensions (docs/code alignment, API routes &
wire fidelity, architecture cohesion, integrations, industry standards, delta vs
baseline), each followed by an independent adversarial verification pass that
re-opened every cited `file:line` and attempted to refute the finding. **All 36
raw findings survived verification (35 CONFIRMED, 1 narrowed in scope);
deduplicated to 24 findings below**, numbered **F-30…F-53** continuing the
register's append-only namespace.

**Baseline guard:** `uv run pytest -q` → **647 passed, 4 skipped** (above the
642-passing baseline). Lint clean. No regression of any RESOLVED F-01…F-29
finding was found — the specific fix sites of F-01/F-02/F-03/F-04/F-05/F-06/
F-07/F-09/F-13 were each re-verified intact at HEAD.

## Purpose and target users (from README)

llm-swarm-router (netllm) is a **mesh router/coordinator for local LLM
backends**: a lightweight per-machine agent that auto-discovers oMLX, Ollama,
LM Studio, and vLLM, meshes machines over LAN (mDNS / subnet scan / static
peers), and exposes one stable dual API surface (OpenAI-compatible `/v1` +
Anthropic Messages) to editors and agent harnesses (Cursor, Claude Code, Codex,
Honcho, Continue, Cline). Target users are **home-lab and multi-machine
local-inference operators** who want throughput that scales with hardware, 24/7
unattended operation, and optional cloud fallback — without per-tool `base_url`
juggling.

## Thesis

> The product's value depends on three invariants: **(1)** the dual API surface
> must be a faithful, industry-standard implementation of the OpenAI and
> Anthropic wire protocols so any compatible client works unmodified; **(2)**
> the six-package architecture plus three control surfaces (web dashboard,
> macOS Settings, CLI) must remain one coherent control plane with no divergent
> parallel implementations; **(3)** rapid feature velocity must not fragment the
> routing path or silently regress the 2026-07-29 hardening. Hypothesis:
> the request path is sound, but fragmentation pressure is highest in (a) the
> four duplicated proxy loops and 2 kLOC modules already open as F-24/F-26,
> (b) Swift-vs-dashboard feature parity, and (c) README/docs drift.

**Thesis verdict: substantially confirmed.**

- Invariant (2) holds best: package boundaries verified clean (zero
  `openai`/`anthropic` imports in netllm-core; import graph is a DAG; CLI talks
  to the agent over HTTP except one straggler, F-40). No divergent code was
  found *without* justification — the fragmentation that exists is exactly
  where F-24/F-21 predicted it, now with two new instances from the post-audit
  commits (F-34, F-49).
- Invariant (1) is the weak edge: happy-path wire fidelity is genuinely close
  to spec, but streaming error paths, error body shapes, and the
  anthropic-format streaming path have real gaps — including the single S1
  (F-30).
- Invariant (3) held: neither post-audit commit regressed a resolved finding,
  and both added tests. Their cost is new S3-class debt (hand-mirrored SDK
  signature, asymmetric application across proxy loops, undocumented contract).
- Prediction (c) confirmed at S2: the README's flagship quickstart no longer
  matches the code (F-31).

## Severity summary

| Severity | Count | Theme |
|----------|-------|-------|
| **S1** | 1 | anthropic-format streaming always fails |
| **S2** | 4 | README quickstart broken, streaming error handling dead, telemetry accounting divergence, Windows service cannot start via SCM |
| **S3** | 19 | proxy-loop divergence, hand-maintained mirrors, error-shape fidelity, auth-gate stragglers, docs drift, ops hygiene |

## Recommended order of work

1. **Correctness now:** F-30 (one-line SDK fix + un-mocked test), F-32/F-33
   (streaming error/accounting — same code area, fix together), F-31 (README).
2. **Consistency batch (post-audit commit cleanup):** F-34, F-35, F-36, F-37 —
   all in `payload.py`/embeddings; one focused PR closes the c9bd30a debt.
3. **Exposure/consistency:** F-40, F-41, F-44, F-47.
4. **Wire fidelity:** F-38, F-42 (error shapes + Responses events, needs a
   live-Codex check).
5. **Ops/docs sweep:** everything else; most are one-liners.

---

# S1 — production-affecting

## F-30 · Streaming Anthropic Messages to any anthropic-format backend always fails

**Severity** S1 · **Area** request path / SDK adapter · **Reproduced** · **Verdict** CONFIRMED (found independently by two dimensions)

`packages/netllm-sdk-anthropic/src/netllm_sdk_anthropic/client.py:57-59` —
`messages_stream` builds `payload = {**payload, "stream": True}` and then calls
`self._client.messages.stream(**payload)`. The Anthropic SDK's `.stream()`
helper does not accept a `stream` kwarg; reproduced by driving
`AnthropicUpstream.messages_stream` under anthropic 0.106.0 → `TypeError`
before any I/O.

Every streamed `POST /v1/messages` request that routing lands on an
anthropic-format backend (the materialized cloud-anthropic row, the legacy
caller-key row, or any `[[routing.backends]]` with `api_format = "anthropic"`)
raises immediately. Local backends are unaffected (openai-format translation
path, `service.py:2010-2017`), which is why the mesh's main path masks this.
Claude Code streams by default, so cloud-Anthropic fallback for the flagship
Anthropic client is broken.

**Fix:** pop `"stream"` before calling the SDK (`.stream()` implies streaming),
or use `messages.create(**payload, stream=True)` for raw wire events. Add one
un-mocked signature/contract test — the existing mocked tests could not catch
this class.

# S2 — real user-visible defects

## F-31 · README two-machine quickstart cannot work as written

**Severity** S2 · **Area** docs alignment · **Verdict** CONFIRMED

`README.md:69-77` and `README.md:290` claim `./netllm init --swarm` generates a
cluster token and prints a `join --token` command. Since the open/secure split,
token minting requires `--secure` (`packages/netllm-cli/src/netllm_cli/main.py:303-332`);
plain `--swarm` prints the open-LAN panel, and the README's
`netllm join URL --token <generated>` against an open machine A exits 1 with
"Token mismatch" (`main.py:529-541`). AGENTS.md:54-56 documents the correct
split, so the README contradicts both the code and AGENTS.md — in the repo's
most prominent workflow.

**Fix:** switch the quickstart to `init --swarm --secure`, or rewrite it around
the open-LAN flow (`init --swarm` on both machines, verify with `./netllm peers`).

## F-32 · Streaming routes' error handling is dead code — errors yield HTTP 200 + aborted stream

**Severity** S2 · **Area** request path · **Verdict** CONFIRMED

`packages/netllm-agent/src/netllm_agent/app.py:409-424` wraps
`StreamingResponse(service.proxy_chat_completion_stream(...))` in
`except SourceCapacityExceeded / OpenAIUpstreamError` clauses — but an async
generator executes nothing until first iteration, and `StreamingResponse` sends
`http.response.start` before iterating. So for `stream=true`, capacity errors
(should be 429) and all-backends-failed (should be 502) surface as a 200 with a
truncated/aborted body. Clients see malformed SSE instead of an actionable
status. Applies to the streaming arms of all proxy surfaces.

**Fix:** pre-flight the failable work (attribution, admission, routing
resolution, first-backend selection) before constructing the
`StreamingResponse`; only stream once a backend connection is established.

## F-33 · Success accounting diverges across the four proxy loops; the new Serving UI makes it visible

**Severity** S2 · **Area** telemetry / F-24 divergence · **Verdict** CONFIRMED

`packages/netllm-agent/src/netllm_agent/service.py:1904-1910` —
`proxy_messages_stream` yields chunks and returns with **no** `mark_success`,
no `REQUESTS_TOTAL` increment, no latency/token recording; streamed chat
records requests but not tokens. This is the exact failure mode F-24 predicted
(a fix/behavior applied to some loops only), and ccc1c79 built the headline
Serving telemetry UI on top of these counters — so a node serving streamed
`/v1/messages` traffic (Claude Code's default mode) shows zero activity and
skewed health/latency stats that `least_load`/`latency_weighted` also consume.

**Fix:** give `proxy_messages_stream` the `_stream_with_metrics` treatment, and
have both streaming wrappers parse the final usage-bearing SSE chunk.

## F-34 · Windows "service" is a plain console exe registered with sc.exe — SCM will kill it

**Severity** S2 · **Area** packaging (Windows) · **Verdict** CONFIRMED

`packaging/windows/install-service.ps1:22` registers `netllm.exe serve -q`
directly via `sc.exe create … start= auto` with no service account (LocalSystem
by default). No code implements `StartServiceCtrlDispatcher`, so `sc start
NetllmAgent` — exactly what the docs instruct — hits SCM error 1053 after ~30s.
The documented Windows alpha service path cannot work as shipped; running as
LocalSystem is also over-privileged for a user-level LLM router.

**Fix:** wrap with a real service host (WinSW/NSSM in the zip, or pywin32
`win32serviceutil`), or drop the SCM pretense for a per-user scheduled task
mirroring the systemd *user*-unit posture.

# S3 — maintenance, consistency, latent risk

## F-35 · c9bd30a payload adaptation applied to chat only — embeddings still 502s on unknown fields

**Verdict** CONFIRMED (two dimensions independently) · **F-24 follow-up**

`packages/netllm-sdk-openai/src/netllm_sdk_openai/client.py:59,71` adapt the
chat paths; `client.py:79-84` `embeddings.create(**payload)` splats raw
payloads. The identical failure class c9bd30a fixed for chat (SDK rejects
wire-valid extension fields → 502 per backend, each marked failed) remains live
on `/v1/embeddings`. **Fix:** an embeddings twin of the adapter, or generalize
`adapt_chat_payload_for_sdk` over an allowed-param set.

## F-36 · `_SDK_CHAT_PARAMS` is a hand-maintained mirror of the pinned SDK signature with no drift test

**Verdict** CONFIRMED (three dimensions) · **F-21-class mirror, F-16-adjacent**

`payload.py:8-51` — a 40-name frozenset hand-copied from
`openai.resources.chat.Completions.create` (verified exactly correct against
openai 2.41.0 today). A future SDK bump silently breaks it in either direction
(TypeError → 502s, or new typed params detouring through extra_body).
**Fix:** one-assert test `set(inspect.signature(...).parameters) - {'self'} ==
_SDK_CHAT_PARAMS` in `./scripts/ci.sh sdk` / the sdk-canary workflow.

## F-37 · `payload.py` non-dict `extra_body` branch is a contradictory dead store

**Verdict** CONFIRMED (two dimensions) · **F-18-class**

`payload.py:111-115` — the `else` branch assigns `out["extra_body"] = existing`
then line 115 unconditionally overwrites it; a client's non-dict `extra_body`
is silently discarded. **Fix:** delete the dead assignment, decide drop-vs-pass
explicitly, add the case to `tests/test_payload_adaptation.py`.

## F-38 · `/v1/*` error bodies are FastAPI `{"detail"}`, not OpenAI/Anthropic error shapes

**Verdict** CONFIRMED · **NEW**

No `exception_handler` registrations exist anywhere in `packages/`; every
non-2xx returns Starlette's `{"detail": "..."}`. OpenAI clients expect
`{"error": {message,type,param,code}}`; Anthropic clients expect
`{"type":"error","error":{...}}`. Error-body-driven client behavior (e.g.
`context_length_exceeded` handling) degrades to generic messages. Upstream
401/429 also collapse to 502 on OpenAI routes. **Fix:** per-surface exception
handlers keyed on path prefix; forward upstream status codes uniformly.

## F-39 · Responses streaming bridge omits `response.output_item.done` / `content_part.*` events

**Verdict** CONFIRMED · **NEW**

`packages/netllm-core/src/netllm_core/openai_responses_bridge.py:363-419` never
closes output items or content parts, and `response.completed` carries a
partial output array. The plan's Phase 3.5 live-Codex verification is
explicitly unrun. **Fix:** emit the bracketing events; add a recorded-fixture
test replaying a real Responses SSE transcript; run the live-Codex check.

## F-40 · `/netllm/v1/client-env` is the one `/netllm` route with no gate after F-13

**Verdict** CONFIRMED · **F-13 follow-up**

`app.py:317-320` takes no `Request`, calls neither `require_read_access` nor
`require_admin_access`. Payload is benign today (base URL + `netllm-local`
placeholders); this is a consistency/latent-risk gap, not a leak. **Fix:** gate
it like the other read routes, or mark it deliberately public with a test
asserting the payload never carries secrets.

## F-41 · `/metrics` stays unauthenticated and exposes fleet-reconnaissance data F-13 gated elsewhere

**Verdict** CONFIRMED · **F-13 follow-up (scope omission)**

`app.py:163-165` — ungated while every other read route is gated. `/metrics`
leaks backend ids/providers, model names, routed counts and token totals — a
large subset of what the F-13 docstring itself calls "complete fleet
reconnaissance". **Fix:** `require_read_access` on `/metrics` (Prometheus
supports `bearer_token` in scrape configs), preserving open behavior when no
token is configured.

## F-42 · Wire payloads can steer SDK-level controls: `extra_headers`, `extra_query`, `timeout`

**Verdict** CONFIRMED · **NEW (c9bd30a contract)**

`payload.py:8-51` classifies three SDK *control* kwargs as client-settable: a
request body `{"extra_headers": {...}}` makes the OpenAI SDK inject arbitrary
headers into the upstream call. **Fix:** drop the three from
`_SDK_CHAT_PARAMS` (they fall into `extra_body` as harmless JSON) or strip them
in `normalize_client_payload`; add a regression test.

## F-43 · CLI `sources toggle` imports `netllm_agent` internals and FastAPI

**Verdict** CONFIRMED · **adjacent to F-02 (which remains resolved)**

`packages/netllm-cli/src/netllm_cli/main.py:2063-2064` imports
`fastapi.HTTPException` and `netllm_agent.admin.apply_config_patch`, breaking
the layering the F-02 fix established (`netllm config import` does it right via
`netllm_core.config_json`). **Fix:** call
`netllm_core.config_merge.apply_config_patch` + `config_guards` directly,
catching `ConfigGuardError`.

## F-44 · Dead async `probe_anthropic_compat` still embodies the pre-fix F-07 billable-probe bug

**Verdict** CONFIRMED · **F-07/F-18 follow-up**

`packages/netllm-core/src/netllm_core/health.py:214-237` — the async twin still
POSTs a billable 1-token request against hardcoded
`claude-3-5-haiku-20241022`; zero callers anywhere including tests. A latent
regression vector of exactly the class F-18 catalogued. **Fix:** delete it, or
mirror the fixed sync logic with a parity test.

## F-45 · `_anthropic_api_key` env fallback lacks the placeholder-key guard `_openai_api_key` has

**Verdict** CONFIRMED · **F-04 follow-up (fix itself holds)**

`service.py:1360-1364` returns `os.environ["ANTHROPIC_API_KEY"]` without
`is_netllm_placeholder_key`, unlike the OpenAI twin at `:1373`. Residual path:
an anthropic-format `[[routing.backends]]` row with no `api_key` can send a
literal `netllm-*` placeholder upstream. **Fix:** one-line mirror + one test.

## F-46 · `cloud.fallback = "local"` is still cloud-first — the acknowledged UX trap is unarmed

**Verdict** CONFIRMED · **NEW as a registered finding (noted in AGENTS.md at baseline)**

`packages/netllm-core/src/netllm_core/routing_policy.py:104-106` — the value
names the fallback *tier* while every user instinct reads it as the *priority*.
For a local-first product, one mistyped word silently sends all traffic and
spend cloud-first. **Fix:** keep the key for compat; accept
`local-first`/`cloud-first` aliases in the CLI, print an explicit one-line
explanation on every change, and note it in `doctor`.

## F-47 · Source identity is not propagated on peer hops — mesh traffic attributes as `default`

**Verdict** CONFIRMED · **NEW**

`service.py:662-670` — `_peer_forward_headers` forwards only the loop-guard and
hops headers; `x-netllm-source` is dropped. Routing correctness is unaffected
(policies apply on the gateway pre-hop) but per-source telemetry on serving
nodes shows everything as `source=default`. **Fix:** forward the resolved id as
attribution-only (peers must not grant elevated capability from an
unauthenticated forwarded header).

## F-48 · `stats.json` is rewritten in place — crash mid-write zeroes all-time counters

**Verdict** CONFIRMED · **F-09 follow-up (durability, not frequency)**

`packages/netllm-agent/src/netllm_agent/telemetry.py:122-130` —
`write_text` truncates the live file; `_load_alltime` silently discards partial
JSON on next start. **Fix:** tmp-file + `os.replace` (atomic on POSIX and
Windows); log a warning on `JSONDecodeError` instead of silent reset.

## F-49 · Telemetry contract fields hand-mirrored in three surfaces (dashboard JS, two Swift files)

**Verdict** CONFIRMED (two dimensions) · **F-21 follow-up, extended by ccc1c79**

Server always emits canonical keys (`telemetry.py:47`), yet dashboard.js
(`routerScopeBlock`) and `ServingStatsMenuBuilder.swift` /
`TelemetryPoller.swift` each re-derive `total_tokens` and carry stringly-typed
key lists. dashboard.js grew to 2,817 lines (largest first-party source file,
+95 in ccc1c79). **Fix:** treat `docs/telemetry-api.md` as normative, delete
client-side fallbacks, add a contract test on the documented key set; include
dashboard.js when the F-24/F-26 refactor is picked up.

## F-50 · c9bd30a's payload contract is documented nowhere; sdk-openai DOX not updated

**Verdict** CONFIRMED (two dimensions) · **NEW (post-audit commit)**

`payload.py` aliases `repeat_penalty→repetition_penalty` for every
OpenAI-format backend and flattens client `extra_body` — user-observable
payload rewriting with zero mention in any doc, and
`packages/netllm-sdk-openai/AGENTS.md` still says "Key module: client.py",
violating the root DOX rule. **Fix:** document the contract (which fields pass
typed, which via extra_body, the alias table) in the package AGENTS.md +
a user-facing note in `docs/editor-integration.md`.

## F-51 · AGENTS.md contradicts itself on the `netllm sources` CLI

**Verdict** CONFIRMED · **NEW**

Key commands (AGENTS.md:77-78) documents `sources list|toggle` (which ship,
`main.py:2025-2052`); a workspace-facts bullet (AGENTS.md:263-265) says the
sources CLI is "still not built". **Fix:** edit the bullet to
"`netllm connect` CLI still not built (`netllm sources list|toggle` shipped)".

## F-52 · `docs/telemetry-api.md` still documents the pre-F-10 psutil behavior

**Verdict** CONFIRMED · **F-10 doc-side follow-up**

`docs/telemetry-api.md:45` says `host` stays null unless psutil is manually
installed (Linux); psutil is now a hard dependency of netllm-agent
(`pyproject.toml:18`), so `host` populates on all platforms. **Fix:** update
the doc and its `"host": null` example.

## F-53 · Undocumented shipped surface: `config export|schema`, `models --local/--subnet-scan`, `[ui]` menubar/favorites keys

**Verdict** DOWNGRADED→narrowed (core confirmed) · **F-21-adjacent (docs, not mirror)**

`main.py:1660-1691` (`config export|schema`) and `main.py:644-662` (models
flags) appear in no user-facing doc; `[ui]` `model_favorites` /
`menubar_show_*` keys are absent from `config.example.toml`. **Fix:** add rows
to the AGENTS.md Key commands table and commented keys to
`config.example.toml`.

---

## Status verifications (informational, no action)

- **Open/partial baseline findings re-verified unchanged:** F-20, F-21, F-23,
  F-24, F-25, F-26, F-28, F-29 all remain accurately described at HEAD; none
  silently resolved or materially worsened (F-21 nudged by ccc1c79 → F-49).
- **F-26 sizes:** service.py 2,246 · cli main.py 2,141 · dashboard.js 2,817
  (baseline: 2,149 / 2,119 / 2,721). Growth came from the audit's own
  remediation; post-audit accretion is dashboard.js only. Stable debt, not
  growing debt.
- **Integration roll-up:** Cursor/generic-OpenAI solid · Claude Code local path
  solid, cloud-Anthropic streaming broken (F-30) · Codex bridge partial (F-39,
  live verification unrun) · Honcho solid per its doc · Gemini CLI correctly
  documented as not reliably wireable · Buzz is an attribution label with no
  dedicated integration (worth stating in docs). Source-identity precedence
  (header → virtual key → UA → default, secret gating) implemented exactly as
  documented. OpenRouter OAuth PKCE correct.
- **Standards posture:** cluster-token handling is right end to end
  (`secrets.token_urlsafe(24)`, 0o600 config, `secrets.compare_digest` at all
  gates). Exposure-phase fixes (F-06/07/11–15) held. Two hygiene notes folded
  into the work queue: basedpyright still non-blocking with no dated milestone
  (F-27 follow-up), and the deb/rpm systemd user unit ships without the four
  standard hardening directives (F-28 follow-up).
- **Architecture doc counts drifted** within 2 days (642→647 tests, LOC
  figures): expected decay of frozen snapshots; bump figures on the next
  audit-adjacent PR (per AGENTS.md verification rules) rather than per commit.

## Comparison to the established baseline

The 2026-07-29 register's assessment ("baseline health is good; these are the
remaining edges") remains accurate and its status table is trustworthy as of
`c9bd30a`. What this audit adds: the baseline audit was code-inward
(correctness of what exists); this pass was product-outward (claims vs code,
wire fidelity vs client expectations, cross-surface consistency), which is
where the S1/S2 items above were hiding — every one of them sits on a boundary
the baseline scoped out (streaming error semantics, Anthropic-format streaming,
README claims, Windows service packaging). The fragmentation thesis was
confirmed in the specific mechanism F-24 predicted: post-audit fixes landing on
one proxy loop but not its siblings (F-33, F-35) is now the dominant defect
generator, which strengthens the register's case for the scoped-out F-24
consolidation refactor.
