# AXIS G — Upstream Change Absorption: empirical measurement

Repo state: `main @ fb6ae0d` (working tree; task quoted 243e3dc). All file:line below verified by reading committed source; every "blast radius" claim marked **[verified]** was executed.

**Headline:** the OpenAI SDK bump pathway is genuinely excellent and is the *only* well-defended edge. Everything else — the Anthropic adapter, both translation bridges, all five cloud providers, all four local backends — absorbs upstream change by **silently dropping fields or emitting a 502**, with no CI signal. The project knows about exactly one of these (G5, filed and deferred at `docs/extending/PROGRAM.md:164`); the bridge-drop class and the local-backend class are, as far as I can find, undocumented anywhere.

---

## 1. VENDOR SDKs — what the bump pathway actually protects

### The pathway (all of it, precisely)

| Piece | Location | What it does |
|---|---|---|
| Floor+ceiling pins | `packages/netllm-sdk-openai/pyproject.toml` (`openai>=2.0,<3`), `packages/netllm-sdk-anthropic/pyproject.toml` (`anthropic>=0.100,<1`) | A silent major bump becomes a *resolution* failure, not a runtime one (F-16) |
| Ceiling enforcement | `tests/test_sdk_versions.py:48-69` (`test_sdk_pins_have_upper_bounds`) | Asserts both `>=` and `<` exist in each spec |
| Lock↔installed equality | `tests/test_sdk_versions.py:34,40` | `openai.__version__`/`anthropic.__version__` must equal `uv.lock` |
| Isolation | `tests/test_sdk_isolation.py` (AST walk over `netllm_core`) | Core must never import `openai`/`anthropic` |
| **OpenAI param drift** | `packages/netllm-sdk-openai/tests/test_sdk_param_drift.py:24,30,34` | 3 asserts, `inspect.signature` vs hand-mirrored frozensets |
| Bump checklist | `docs/sdk-versions.md:47-62` | One SDK per PR, changelog read, layer classification, `./scripts/ci.sh sdk` |
| `sdk` CI job | `.github/workflows/ci.yml:56-…` → `scripts/ci.sh:48-57` | Runs both package test dirs + `test_anthropic_bridge.py`, `test_anthropic_cloud_compat.py`, `test_sdk_isolation.py`, `test_sdk_versions.py`, `test_sdk_versions_payload.py` |
| Weekly canary | `.github/workflows/sdk-canary.yml` (`cron: 0 9 * * 1`) | `uv sync --frozen`, then `uv pip install --upgrade openai anthropic`, then `bash scripts/ci.sh sdk`; on failure opens/comments an `sdk-canary`-labelled issue |
| Dependabot | `.github/dependabot.yml:31-59` | Weekly PRs on both SDK package dirs, `sdk-bump` label |

The canary is the strongest single mechanism here: it runs the *whole* `sdk` target against unreleased-to-us SDK versions weekly, so it catches anything the `sdk` target catches, a week early.

### What is protected, exactly

**OpenAI chat + embeddings params: fully covered.** `_SDK_CHAT_PARAMS` (39 names, `payload.py:20-61`) and `_SDK_EMBEDDINGS_PARAMS` (6 names, `payload.py:65-73`) are both asserted set-equal to `inspect.signature(...).parameters - {"self"}` minus `_SDK_CONTROL_PARAMS`. Adding *or* removing a typed param on either method fails CI. The task premise that embeddings might be unguarded is wrong — F-35/F-36 covered it (`test_sdk_param_drift.py:29-31`).

### What is NOT protected

**(a) Anthropic adapter — zero param protection. There is no mirror set at all.**
`packages/netllm-sdk-anthropic/src/netllm_sdk_anthropic/client.py:47` and `:64`:
```python
resp = await self._client.messages.create(**payload)          # :47
stream = await self._client.messages.create(**payload, stream=True)  # :64
```
`payload` is the caller's wire body, unfiltered. `MessagesAdapter.build_invocation` passes `plan.payload` verbatim (`surfaces/messages.py:44-55`), and `_anthropic_payload_for_backend` (`surfaces/messages.py:195-215`) rewrites only `model`. There is no `_SDK_MESSAGES_PARAMS`, no `extra_body` split, no drift test. The pinned `AsyncMessages.create` currently types 22 params (`cache_control, container, extra_body, extra_headers, extra_query, inference_geo, max_tokens, messages, metadata, model, output_config, service_tier, stop_sequences, stream, system, temperature, thinking, timeout, tool_choice, tools, top_k, top_p`) — none of them mirrored, and note `extra_headers`/`extra_query`/`timeout` are **not** stripped here the way `_SDK_CONTROL_PARAMS` strips them on the OpenAI side (`payload.py:15,102`), so the F-42 header-injection hardening has no Anthropic twin.

**Blast radius, verified by execution:**
```
AnthropicUpstream.messages_create({... , 'future_param': 1})
  -> TypeError "AsyncMessages.create() got an unexpected keyword argument 'future_param'"
  -> _wrap() (client.py:74-76): status_code = getattr(exc,'status_code',None) = None
  -> app.py:533  status = exc.status_code or 502
  -> HTTP 502
```
This is precisely the failure mode the OpenAI drift test's own docstring names ("TypeError -> 502s"). It is not only a bump risk: it fires **today** for any client sending a Messages field the pinned SDK doesn't type, and because the guard is inside the failover loop it burns the whole retry budget across every candidate backend first.

The only Anthropic-side signature assertion that exists is `packages/netllm-sdk-anthropic/tests/test_messages_stream_f30.py:104` — `assert "stream" not in inspect.signature(AsyncMessages.stream).parameters` — a regression pin for one historical bug, not param-set drift. The remaining Anthropic tests (`test_client_contract.py`) are mock-based and assert only that our own kwargs round-trip.

**(b) The Responses bridge — no drift protection of any kind, and it fails *silently*.**
`netllm_core/openai_responses_bridge.py:30`:
```python
_PASSTHROUGH_KEYS = ("model", "stream", "temperature", "top_p")
```
`responses_to_chat_request` (`:33-60`) copies only those four plus hand-coded `max_output_tokens`, `reasoning.effort`, `instructions`, `input`, `tools`, `tool_choice`, `parallel_tool_calls`. Everything else is **dropped with no log, no warning, no error**. Verified:
```
r2c({'model':'m','input':'hi','truncation':'auto','max_tool_calls':3,
     'text':{'verbosity':'low'},'store':True,'previous_response_id':'r1'})
  -> {'model':'m','messages':[{'role':'user','content':'hi'}]}
```
Structurally this module *cannot* be signature-checked the way `payload.py` is: `netllm_core` is forbidden from importing `openai` (`test_sdk_isolation.py`), so there is no vendor type to diff against. That is a real design tension nobody has written down. `tests/test_codex_responses_bridge.py` and `tests/test_responses_bridge_f39.py` test only shapes we authored.

**(c) The Anthropic bridge — same silent-drop class, worse consequences.**
`netllm_core/anthropic_bridge.py:21-28` hand-lists 6 passthrough keys. Verified:
```
a2o({'model','max_tokens','messages', 'top_k':40,
     'thinking':{'type':'enabled','budget_tokens':4000}, 'metadata':{...}})
  -> {'model':'m','max_tokens':1,'messages':[]}
```
`thinking` and `top_k` are dropped. That means Claude Code's extended-thinking traffic routed at a local OpenAI-format backend silently becomes a non-thinking request — a correctness change with a 200 response. Directly relevant to Axis F: this is a harness *functional requirement* being dropped on the floor.

**(d) Doc rot in the checklist itself.** `docs/sdk-versions.md` "Change layers" table, Layer 3, points at `packages/netllm-agent/src/netllm_agent/service.py` — **the file does not exist** (verified `ls`); it was split into `service/` during the refactor. Already logged as G10-adjacent "G6" in `docs/extending/lifecycle-inventory.md:76`.

### CI-time vs production-502 ledger

| Upstream change | Caught at CI? | By what |
|---|---|---|
| OpenAI adds/removes a typed chat param | ✅ | `test_sdk_param_drift.py:24` |
| OpenAI adds/removes a typed embeddings param | ✅ | `test_sdk_param_drift.py:30` |
| Either SDK ships a new major | ✅ | `<` ceiling → resolution failure; `test_sdk_versions.py:48` |
| Installed ≠ locked | ✅ | `test_sdk_versions.py:34,40` |
| OpenAI changes response *parsing* (new required field) | ✅ incidentally | FakeFarm injects below the SDK transport (`tests/contract/farm.py:1-12`), so real parse paths run |
| **Anthropic adds/removes/renames a typed param** | ❌ | none → 502 |
| **Anthropic tightens `messages.create` kwargs** | ❌ | none → 502 |
| **OpenAI Responses API gains a field Codex starts sending** | ❌ | none → silent drop, 200 |
| **Anthropic Messages gains a field Claude Code starts sending** | ❌ (anthropic→openai arm) | none → silent drop, 200 |
| Vendor changes SSE event names | ❌ | fake emits our own names |

---

## 2. PROVIDER API CHANGE (remote) — nothing per-provider exists

`netllm_core/cloud_providers.py` is a 176-line frozen-dataclass registry of 5 providers. Its module docstring (`:1-9`) says facts are *"sourced from each vendor's official docs as of 2026-07-22"* — a **single global comment date, not a per-provider `validated_at` field**, so it cannot be asserted, expired, or reported.

Per provider, the change classes and what happens:

| Change | Detected? | Blast radius |
|---|---|---|
| Vendor moves `base_url` (e.g. `moonshot.endpoints["global"].openai_base_url`, `:53`) | ❌ nothing | Backend probes offline → provider silently drops out of routing (`probe_openai_compat` returns `{"status":"offline"}`, `health.py:60`), or 502s |
| Model deprecated out of `static_models` (`kimi-k3`, `glm-5.2`, `gpt-5.6`, `claude-opus-4-7`, `:65,90,110,127`) | ❌ nothing | Stale name shown in every UI; requests 404/400. **`zai` has `models_endpoint=False` (`:86`) so its 5-model catalog is the *only* source — no live `/models` to self-heal** |
| Error-shape change | ❌ | `_wrap` reads `getattr(exc,"status_code",None)`; a shape the SDK stops mapping → `None` → 502 (`app.py:464,491,505,533,544`) |
| Auth change (`api_key_env`, `x-api-key` vs `Bearer`, `anthropic-version: 2023-06-01` hardcoded at `health.py:272`) | ❌ | 401/403 is treated as **online** by both probes (`health.py:41-48`, `:264`) — an auth break is invisible to health and only surfaces per-request |
| `anthropic-version` header sunset | ❌ | probe silently degrades |

**Tests are structural only.** `tests/test_cloud_providers.py` asserts the registry has five ids, that `openrouter` supports `oauth_pkce`, that `zai` has no models endpoint — i.e. that the *data we wrote* is the data we wrote. Nothing dials any provider. There is exactly one canary workflow in `.github/workflows/` and it is `sdk-canary.yml`.

**The quote you asked for — `docs/cloud-providers-plan.md:38`:**
> `static_models` tuples are hand-maintained code constants and will drift (F-23).

(Note: the F-number is misapplied — F-23 in `docs/architecture/07-findings-register.md:667` is "Heartbeats ship full catalogs on an N×N mesh". The drift item has no register entry of its own.)

And `docs/cloud-providers-plan.md:319-321` (risk 4):
> **Model ID churn** (Moonshot discontinued its k2 preview family with ~6 months notice): keep static catalogs minimal, prefer live `GET /models` wherever offered, and treat registry model lists as display hints, not routing constraints.

`docs/extending/lifecycle-inventory.md:33` row **D6** scores this: *"◐ … | ✗ no per-provider validated-date or canary | ✗"*.

And the adopted program explicitly rules it out — `docs/extending/PROGRAM.md:163`:
> **Live cloud-provider canaries.** Real gap (`docs/cloud-providers-plan.md` flags it: no per-provider validated-date, no canary), but structural conformance cannot catch a wrong `base_url` and this program will not pretend to. Separate follow-on modelled on `sdk-canary.yml`.

So: the gap is known, correctly scoped, and deliberately unowned. Axis G is where it should land.

---

## 3. LOCAL SERVER CHANGE (oMLX / Ollama / LM Studio / vLLM)

### What pins the router's assumptions

| Assumption | Location | Absorbs change how |
|---|---|---|
| Ports | `netllm_discovery/local.py:16-21` — `omlx [8080,8088,8081]`, `ollama [11434]`, `lmstudio [1234,41334]`, `vllm [8000,8001]` | Env override (`OMLX_PORT`/`OLLAMA_PORT`/`LMSTUDIO_PORT`/`VLLM_PORT`, `:100-105`; `OLLAMA_HOST` parsing at `:81-97`) then config `provider_urls`. A new default port = silent non-discovery |
| Probe path | `health.py:78` — `GET {base}/models`; base normalised to end in `/v1` (`local.py:53`) | Server dropping/renaming the OpenAI-compat shim ⇒ backend reads offline |
| Catalog shape | `health.py:36-37` — `body["data"]` list of `{"id": ...}` | Anything else silently yields `model_count=0`, `models=[]`. **A `[]`-catalog backend stays a candidate for every model** (`tests/test_model_aliases.py:47-60`, "backends_with_unknown_catalog_stay_candidates"), so a shape change turns a specific backend into a catch-all that 404s everything |
| Payload field names | `payload.py:76-79` `_FIELD_ALIASES = {"repeat_penalty": "repetition_penalty"}` — **one entry** | Any other divergence rides through as `extra_body` (`_adapt_payload_for_sdk`, `:110-146`) and is the *upstream's* problem |
| SDK-untyped knobs | `_SDK_CHAT_PARAMS` split → `extra_body` | This is the real absorption mechanism and it works well: `top_k` etc. reach vLLM at top level |
| Capability inference | `capabilities.py` name tokens (see §4) | see §4 |
| oMLX admin | `local.py:367-480`, `_normalize_omlx_admin_payload` | oMLX-specific, fixtures at `tests/fixtures/omlx/*.json` — the **only** recorded-real-response fixtures in the repo |

### What test would catch an Ollama response-shape change?

**None.** Every local-backend test constructs the response dict itself:
- `tests/test_local_discovery.py` — `test_scan_finds_omlx_on_alternate_port` (:73), `test_scan_finds_vllm` (:104) etc. all hand-build `{"data":[{"id":...}]}`.
- `tests/contract/farm.py` (FakeFarm) speaks a dialect we authored. Its own docstring (`:1-12`) is honest about the boundary: it is injected *below the SDK transport* precisely so the real SDK request-build/response-parse runs — that defends §1's SDK axis, not the provider axis. It cannot notice that Ollama's real body changed.
- `tests/fixtures/` holds 5 files total: 3 oMLX admin payloads and 2 Anthropic v1 bodies. There is no recorded Ollama, LM Studio, or vLLM `/v1/models` or `/v1/chat/completions` corpus.

So a local backend can change its wire shape, port, or capability semantics and the first signal is a user reporting no models / 502s. `netllm doctor` and `./netllm test` are the only detectors and both are manual.

---

## 4. CAPABILITY DRIFT

`capabilities.py:53-68` classifies by casefold token split against three frozensets: `_EMBEDDING_TOKENS` (8: bge, gte, e5, minilm, bert, modernbert, colbert, splade — plus substring `"embed"`), `_AUDIO_TOKENS` (10), `_OTHER_TOKENS` (2), substring `"rerank"`; **unknown ⇒ `"chat"`** (`:67`, documented as deliberately conservative at `:9-11`).

That default is safe for the chat/messages guards (an unknown name still chats). It is **unsafe for the embeddings guard added in Phase 4c** — `policy.py:127-156`, whose own docstring flags it:

> **User-visible tightening, release-note it.** … an embedding model with an unrecognized name … now gets a 400 here where it used to route. Callers in that position should rename the served model, or map it with a `[routing.model_aliases]` entry whose *request* name carries an embedding token.

**Verified end-to-end** (scratch test, `/tmp/…/scratchpad/fg/test_axisg_alias_override.py`), with a served model named `voyage-3-lite` (matches no heuristic):
- direct request → `400 Model 'voyage-3-lite' (capability: chat) cannot serve embeddings.`
- with `[routing.model_aliases] "my-embed-model" = ["voyage-3-lite"]`, request as `my-embed-model` → **200**.

So the override path **does work**, and the mechanism is subtler than the docstring implies: the guard runs at `policy.py:333` on `model` = requested name after `sources[].model_rewrites` + scenario override *only* — alias resolution happens later, per-backend (`_model_for_backend`, `policy.py:66-84`). The token must therefore be in the *request* name. The docstring is right; nothing else states the ordering.

**Documented:** yes, adequately. `docs/release-notes/v0.5.0.0.md:19` —
> `/v1/embeddings` now returns **400 immediately** for models classified as chat (name-heuristic). Unknown model names default to `chat`. Use `[routing.model_aliases]` with an embedding token in the alias if your encoder name is non-standard.

Also `docs/architecture/refactor/RELEASE-NOTES.md:28` and the lifecycle diagram `docs/architecture/03-request-lifecycle.md:159-160`. **Not** in `docs/config-reference.md` — the reference an operator hitting the 400 would actually open has no mention of `capability` at all.

**Tested:** partially, and not as a stated remediation.
- `tests/contract/scenarios_naming_cloud_guards.py:376-383` vector `naming-restore-emb` uses `model_aliases {"canon-embed": ["bge-served"]}` — it happens to exercise the shape but is authored to pin *name restoration*, not the override path, and both names carry tokens so it cannot fail if the guard moved to `effective_model`.
- `:943-964` vector `guards-rewrite-capability-400-chat-s` pins the F-57 inverse (rewrite `public-embed → bge-m3` flips capability and 400s the chat surface, quoting the caller name).
- `tests/test_embeddings.py:74` uses `"not-an-embed-model"` and its comment (`:81-84`) explicitly notes the name was chosen to *survive* the guard.
- No test names an encoder that matches **no** heuristic and proves the alias rescues it. So the documented remedy is not regression-protected: a future change classifying `effective_model` instead of the request name would break the published workaround with a green suite.

**No non-alias override exists.** There is no `[routing.model_capabilities]` map, no per-backend capability declaration, no `X-Netllm-Capability` header. A vendor shipping `voyage-4`, `jina-v4`, `nv-embedqa`, `cohere-embed-v4` (that one has `embed`), `Qwen3-Reranker` (has `rerank`) — the misses are real and the only escape is renaming the served model or inventing an alias.

---

## 5. VERSION / COMPAT INTERACTION

### Pinned ranges
Only the two vendor SDKs carry ceilings. Everything else is floor-only:
```
netllm-core:      httpx>=0.28, pydantic>=2.10, tomli-w>=1.0
netllm-agent:     fastapi>=0.115, uvicorn[standard]>=0.32
netllm-cli:       typer>=0.15, rich>=13.9, httpx>=0.28
netllm-discovery: zeroconf>=0.132
```
`httpx` is the one that matters: both vendor SDKs, both health probes, FakeFarm's transport patch (`farm.py:1-12`) and `test_messages_stream_f30.py` (which reaches into `inner._mounts`) all bind to httpx internals. An httpx 1.0 is a resolution success and a runtime surprise for anyone installing from an index — the exact F-16 failure the SDK pins were tightened to prevent, still open for the transport library underneath them. `pydantic>=2.10` unbounded is the same story for the config models. The `test_sdk_pins_have_upper_bounds` assertion (`tests/test_sdk_versions.py:53-69`) iterates a hardcoded 2-tuple list and would not notice.

### Lock discipline
`uv.lock` is committed; CI and `scripts/ci.sh:17` use `uv sync --frozen`, so CI is reproducible and `test_sdk_versions.py` pins installed==locked. Good. The consequence is that **CI only ever sees one resolution** — the lock is what makes `sdk-canary.yml` (which deliberately breaks the lock with `uv pip install --upgrade`) the single mechanism that tests anything else, and it upgrades *only* `openai anthropic`. There is no canary for httpx/fastapi/pydantic/zeroconf.

### Mixed-version mesh
There is **no protocol version negotiation anywhere**. `version` is carried in mDNS TXT (`mdns.py:141`), parsed into `Peer.version` (`swarm.py:30,161`) and heartbeats (`swarm_tasks.py:67`), and re-emitted for display (`swarm.py:265`). Nothing gates on it; `grep` for `min_version|protocol_version|compat` finds no such check in any package.

What *does* exist: config forward-compat is genuinely strong — `tests/test_config_forward_compat.py` has 15 tests covering unknown-section survival, unknown-key survival through patch and merge, `test_older_agent_save_preserves_newer_agent_keys` (:186), unknown cloud-provider subtree preservation (:224-243). And `A4` mesh version-drift *detection* is scored ✅ in `docs/extending/lifecycle-inventory.md:16`.

What doesn't: `lifecycle-inventory.md:41` scores **E8 (mesh upgrade, mixed versions)** as `✗ | ✗ | ✗ — G8`, and `:82` is worth quoting for the upstream-change interaction:
> Nothing documents: upgrade ordering (gateway before peers, given `swarm_tasks.py:28-52` makes the gateway authoritative for routing strategy), how many versions of skew are supported… `commands/join_swarm.py:140` tells the operator to "Verify both machines run a compatible netllm version" without ever defining *compatible*.

**The Axis-G-specific coupling nobody has stated:** upstream change and mesh skew multiply. A capability-heuristic change, an `_SDK_CHAT_PARAMS` widening, a `_FIELD_ALIASES` addition, or a cloud `base_url` correction ships in one node's binary. On a mixed mesh with agent-hop forwarding (`MAX_FORWARD_HOPS`, `policy.py:56-64`), the *terminating* peer applies its own version of all four — so an old peer will 400 on a request the new peer's guard admitted, or drop a param the new peer's alias map preserved, and the failure is attributed to the wrong machine. `tests/test_e2e_two_agents.py` runs both nodes at the same version; `docs/extending/PROGRAM.md:122` proposes `NETLLM_COMPAT_PRETEND_VERSION` for exactly this but it is Phase 6 work not yet done.

---

## 6. THE MATRIX — ranked by "silently reaches production"

Rank 1 = worst (wrong answers, 200 status, no signal anywhere). Rank descends toward loud/CI-caught.

| # | Upstream change class | Detected how? | Detected when? | By what test/mechanism | Blast radius |
|---|---|---|---|---|---|
| **1** | **Anthropic Messages gains a field Claude Code sends (`thinking`, `top_k`, `cache_control`) and the request lands on an openai-format backend** | **Not at all** | never | none | **HTTP 200, silently degraded answer.** `anthropic_bridge.py:21-28` drops it. Extended thinking silently off. Verified |
| **2** | **Responses API gains a field Codex sends (`truncation`, `max_tool_calls`, `text.verbosity`, `store`)** | **Not at all** | never | none — `netllm_core` is barred from importing `openai`, so no signature to diff | **HTTP 200, silently wrong.** `openai_responses_bridge.py:30` `_PASSTHROUGH_KEYS` is 4 names. Verified |
| **3** | **Local backend (Ollama/LM Studio/vLLM) changes `/v1/models` body shape** | Not at all | never | no recorded fixtures exist for any of the three | Backend reads `model_count=0` → becomes a **catch-all candidate for every model** (`test_model_aliases.py:47`) → 404 storms attributed to the wrong backend |
| **4** | **Vendor ships an encoder whose name matches no heuristic** | Only by user 400 | first embeddings request | `capabilities.py:67` returns `"chat"` | 400 at `/v1/embeddings`. Override works (alias, verified) and is release-noted (`v0.5.0.0.md:19`) but **absent from `config-reference.md` and not regression-tested as a remedy** |
| **5** | **Cloud provider changes `base_url` or auth** | Only in production | first request / next health TTL | no canary, no per-provider `validated_at` (only a module comment, `cloud_providers.py:5`) | Provider silently drops from routing (401/403 counts as *online*, `health.py:41-48`) or 502s. Flagged-and-deferred: `PROGRAM.md:163` |
| **6** | **Cloud provider deprecates a model in `static_models`** | Only in production | first request | none; `zai` has `models_endpoint=False` so no self-heal | Stale names in all UIs, 404s. `cloud-providers-plan.md:38` + `:319` |
| **7** | **Anthropic SDK adds/removes/renames a typed `messages.create` param** | **Not until runtime** | first request after bump | none — no `_SDK_MESSAGES_PARAMS`, no drift test | **TypeError → `status_code=None` → HTTP 502** (verified: `client.py:74` → `app.py:533`), burning the full failover budget first. This is **G5**, filed and deferred at `PROGRAM.md:164` |
| **8** | **Client sends any Messages field the pinned SDK doesn't type** (same mechanism, no bump needed) | Not until runtime | today | none | Same 502. Also: `extra_headers`/`extra_query`/`timeout` are **not** stripped on the Anthropic path — no F-42 twin |
| **9** | **httpx / pydantic / fastapi / zeroconf major bump** | Downstream installs only | at their runtime | floors with no ceilings; `uv.lock` hides it in-repo; `test_sdk_pins_have_upper_bounds` covers only the 2 vendor SDKs | Repeat of F-16 one layer down. Transport internals are load-bearing (FakeFarm patch, `_mounts`) |
| **10** | **Mesh skew after any of #1-#9 ships to one node** | Partially — `peer_warnings` | at request time | `tests/test_routing_hardening.py:195-218` (A4 detection ✅); no mixed-version request-path lane (E8/G8 ✗) | Terminating peer applies *its* guards/aliases/params; failure attributed to the wrong machine. `lifecycle-inventory.md:41,82` |
| **11** | Vendor changes SSE event names / stream framing | Not at all | production | FakeFarm emits names we authored (`farm.py`) | Stream corruption / client hang |
| **12** | OpenAI SDK changes response *parsing* (new required field) | ✅ incidentally | CI + weekly canary | FakeFarm injects below the SDK transport, so real parse paths run (`farm.py:1-12`) | Caught |
| **13** | **OpenAI adds/removes a typed chat or embeddings param** | ✅ **loudly** | CI, and 1 week early | `test_sdk_param_drift.py:24,30` | Caught |
| **14** | Either vendor SDK ships a new major | ✅ loudly | dependency resolution | `<3` / `<1` ceilings; `test_sdk_versions.py:48` | Caught |
| **15** | Installed SDK drifts from `uv.lock` | ✅ loudly | CI | `test_sdk_versions.py:34,40` | Caught |
| **16** | Core starts importing a vendor SDK | ✅ loudly | CI | `test_sdk_isolation.py` (AST) | Caught |

**Asymmetry to name in one line:** rows 13-16 are all *one* mechanism family (OpenAI adapter + pins) and they are the only rows in the "caught" half. Rows 1-3, the three worst, all share a single root cause the project has never articulated — **every translation and probe boundary in the router is a hand-maintained allowlist of field names with no counterpart to diff against**, and the two most dangerous ones sit inside `netllm_core` where the isolation rule (correctly) forbids importing the vendor types that would make a drift test possible. Fixing G5 (row 7) is the cheap, already-filed win; rows 1-3 need a different instrument — recorded-fixture corpora and an explicit "unknown field" policy (drop-and-log vs 400 vs passthrough), which currently is neither chosen nor written down.

### Files that matter most
- `/home/user/llm-swarm-router/packages/netllm-sdk-anthropic/src/netllm_sdk_anthropic/client.py:47,64,74`
- `/home/user/llm-swarm-router/packages/netllm-core/src/netllm_core/openai_responses_bridge.py:30`
- `/home/user/llm-swarm-router/packages/netllm-core/src/netllm_core/anthropic_bridge.py:21-28`
- `/home/user/llm-swarm-router/packages/netllm-sdk-openai/src/netllm_sdk_openai/payload.py:15,20,65,76`
- `/home/user/llm-swarm-router/packages/netllm-sdk-openai/tests/test_sdk_param_drift.py:24,30`
- `/home/user/llm-swarm-router/packages/netllm-core/src/netllm_core/capabilities.py:53-68`
- `/home/user/llm-swarm-router/packages/netllm-agent/src/netllm_agent/service/policy.py:127-156,333`
- `/home/user/llm-swarm-router/packages/netllm-core/src/netllm_core/cloud_providers.py:1-9`
- `/home/user/llm-swarm-router/packages/netllm-core/src/netllm_core/health.py:36,41-48,272`
- `/home/user/llm-swarm-router/packages/netllm-discovery/src/netllm_discovery/local.py:16-21,100-105`
- `/home/user/llm-swarm-router/packages/netllm-agent/src/netllm_agent/app.py:533`
- `/home/user/llm-swarm-router/.github/workflows/sdk-canary.yml`, `/home/user/llm-swarm-router/scripts/ci.sh:48-57`
- `/home/user/llm-swarm-router/docs/sdk-versions.md` (Layer-3 path is dead)
- `/home/user/llm-swarm-router/docs/extending/lifecycle-inventory.md:28-33,41,56-92` (rows D1-D6, E8; gaps G5, G6, G8)
- Scratch verification: `/tmp/claude-0/-home-user-llm-swarm-router/a47262f7-b1a5-5a19-8f10-2ce807d9a52e/scratchpad/fg/test_axisg_alias_override.py`

No tracked file was modified; no git state-changing command was run.