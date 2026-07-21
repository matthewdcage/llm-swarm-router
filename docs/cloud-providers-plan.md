# Cloud Providers Plan — pre-configured cloud backends, Cloud UI, local/cloud fallback

Status: **proposed** (research complete 2026-07-22, all provider facts from official docs as of that date).
Companion to [routing-hardening-plan.md](routing-hardening-plan.md). All phases are additive and
non-breaking: absent config keys default to today's behavior, and every write path
(`config import`, `POST /netllm/v1/admin/config`) already deep-merges.

## 0. Scope and naming correction

Requested providers: Moonshot AI, "Z.ai (Kimi)", OpenAI, Anthropic, OpenRouter.

> **Note:** Kimi is Moonshot AI's product; **Z.ai is Zhipu AI (GLM models)** — two different
> vendors. This plan includes both: `moonshot` (Kimi K-series) and `zai` (GLM series).

Goals:

1. Ship five **pre-configured cloud providers** the user can enable/disable without hand-editing
   `config.toml`.
2. A fully functional **"Cloud" surface** in every UI: macOS Settings tab + menubar item, `/ui/`
   dashboard tab, and a `netllm cloud` CLI group.
3. A global **cloud enabled/disabled** master switch.
4. A **fallback direction** setting: local-primary-with-cloud-fallback, cloud-primary-with-local-
   fallback, or fallback disabled.

Non-goals: per-model pricing/steering, BYOK-through-OpenRouter management, Bedrock/Vertex/Foundry.

## 1. Provider facts (official docs, 2026-07-22)

| Provider | Base URL(s) | API format(s) | Third-party auth (official) | Models endpoint | Current flagship IDs |
|---|---|---|---|---|---|
| **Moonshot (Kimi)** | `https://api.moonshot.ai/v1` (OpenAI), `https://api.moonshot.ai/anthropic` (Anthropic); CN: `api.moonshot.cn` | OpenAI + Anthropic | Bearer API key (pay-as-you-go only; no OAuth) | `GET /v1/models` | `kimi-k3` (1M ctx), `kimi-k2.7-code`, `kimi-k2.6` — **`kimi-k2-*` previews, `kimi-latest`, `moonshot-v1-*` are discontinued/sunsetting; do not ship** |
| **Z.ai (Zhipu GLM)** | `https://api.z.ai/api/paas/v4` (OpenAI); Coding Plan: `https://api.z.ai/api/anthropic` + `/api/coding/paas/v4`; CN: `open.bigmodel.cn/api/paas/v4` + `/api/anthropic` | OpenAI + Anthropic | Bearer API key (Coding Plan = subscription-backed key). **ToS caveat:** Coding Plan keys are restricted to an approved-tools list; a generic router is not on it — surface this in the UI | **none — catalog must be hardcoded** | `glm-5.2`, `glm-5-turbo`, `glm-5.1`, `glm-4.7`, vision `glm-5v-turbo` |
| **OpenAI** | `https://api.openai.com/v1` | OpenAI only (Chat Completions supported; Responses preferred) | **API key only.** "Sign in with ChatGPT" plan OAuth is documented only for OpenAI's own clients (Codex CLI etc.); no public OAuth client for third-party tools as of today | `GET /v1/models` | `gpt-5.6`, `gpt-5.3-codex` |
| **Anthropic** | `https://api.anthropic.com` | Anthropic Messages | `x-api-key` + `anthropic-version: 2023-06-01` (official). Plan route: `claude setup-token` mints a 1-year OAuth Bearer token (`CLAUDE_CODE_OAUTH_TOKEN`) — documented **only for Claude Code CI**; support it as an explicit opt-in "unofficial" auth mode with a warning | `GET /v1/models` | `claude-opus-4-7`, `claude-sonnet-4-6` |
| **OpenRouter** | `https://openrouter.ai/api/v1` (OpenAI); `https://openrouter.ai/api/v1/messages` (Anthropic) | OpenAI + Anthropic | Bearer API key **and official OAuth PKCE for third-party apps** (`https://openrouter.ai/auth?...` → `POST /api/v1/auth/keys`; localhost callbacks on any port explicitly supported for CLIs) | `GET /v1/models` (public); `GET /v1/key` for credit status | dynamic catalog |

Auth conclusions:

- **API key is the only universally official mechanism.** Ship key-based auth for all five.
- **OpenRouter is the only provider with a sanctioned third-party OAuth flow** — build PKCE there
  (Phase 5).
- **OpenAI "plan" auth does not exist for third parties** — API key only; document that clearly.
- **Anthropic plan token** (`claude setup-token`) works technically (Bearer on `/v1/messages`) but
  is unofficial for this use; offer as an advanced auth mode labeled as such, never the default.
- **Z.ai Coding Plan keys** carry a usage-policy risk in a router; show an informational note when
  the user selects the Coding Plan endpoints.

## 2. Current state (codebase)

- Cloud already half-exists: `_inject_openai_cloud_backend` / `_inject_anthropic_cloud_backend`
  (`packages/netllm-agent/src/netllm_agent/service.py:984-1026`) ephemerally add
  `api.openai.com` / `api.anthropic.com` when a real key is present and
  `ResolvedRouting.allow_cloud_inject` is true; Anthropic-format cloud is a **final fallback tier**
  (`_anthropic_fallback_backends`) so it never shadows the local mesh.
- Config is pydantic v2 in `packages/netllm-core/src/netllm_core/models.py`:
  `BackendOverride` (`base_url`, `provider`, `api_format`, `api_key`, `api_key_env`, `enabled`,
  `local`), `RoutingPolicy` (`allow_cloud`), closed `ProviderId` Literal
  (`omlx|ollama|lmstudio|vllm|custom|anthropic|openai`).
- Three hand-mirrored schema copies: Python `models.py`, Swift `NetllmConfigDocument.swift`,
  JS `dashboard.js` draft. All writes deep-merge, so unmodeled fields survive.
- macOS app: Settings → Routing already embeds `CloudFailoverSettings.swift` (Anthropic/OpenAI
  SecureFields → Keychain → `PythonRuntime.injectCloudAPIKeys` → agent env at spawn; **requires
  agent restart**).
- Upstream adapters: `netllm-sdk-openai` (Bearer) and `netllm-sdk-anthropic` (`x-api-key`) — every
  new provider is served by one of these two with just `base_url` + key. The
  `anthropic_bridge` translates formats both ways.

## 3. Design

### 3.1 Provider registry (new, netllm-core)

`packages/netllm-core/src/netllm_core/cloud_providers.py` — a static, code-owned catalog:

```python
@dataclass(frozen=True)
class CloudProviderSpec:
    id: str                      # "moonshot" | "zai" | "openai" | "anthropic" | "openrouter"
    display_name: str
    endpoints: dict[str, CloudEndpoint]   # region key -> endpoint set
    # CloudEndpoint: openai_base_url | None, anthropic_base_url | None
    auth_modes: tuple[str, ...]  # ("api_key",), ("api_key","oauth_pkce"), ("api_key","plan_token")
    api_key_env: str             # MOONSHOT_API_KEY, ZAI_API_KEY, OPENAI_API_KEY,
                                 # ANTHROPIC_API_KEY, OPENROUTER_API_KEY
    models_endpoint: bool        # False for zai -> use static_models
    static_models: tuple[str, ...]
    notes: str                   # UI-surfaced caveats (Z.ai ToS, Anthropic plan-token warning)
```

Registry entries (defaults):

| id | default region | default api_format preference | key env |
|---|---|---|---|
| `moonshot` | global (`api.moonshot.ai`), alt `cn` | openai (anthropic available) | `MOONSHOT_API_KEY` |
| `zai` | global (`api.z.ai`), alt `cn` (`open.bigmodel.cn`); endpoint profile `api` vs `coding_plan` | openai (anthropic available on coding_plan/cn) | `ZAI_API_KEY` |
| `openai` | global | openai | `OPENAI_API_KEY` |
| `anthropic` | global | anthropic | `ANTHROPIC_API_KEY` |
| `openrouter` | global | openai (anthropic available) | `OPENROUTER_API_KEY` |

The registry is versioned data, not config — base URLs and model lists update with releases, never
stored per-user (except overrides, below).

### 3.2 Config contract (additive `[cloud]` section)

```toml
[cloud]
# Master switch. Default TRUE = preserves today's behavior (env-key-triggered inject).
enabled = true
# Fallback direction: "cloud" = local primary, cloud fallback (today's implicit behavior);
# "local" = cloud primary, local fallback; "none" = no cross-tier fallback
# (cloud reachable only via explicit allow_cloud policies / pinned backends).
fallback = "cloud"
# Fallback master toggle kept separate so direction survives disable/re-enable.
fallback_enabled = true

[cloud.providers.moonshot]
enabled = false
region = "global"          # or "cn"
api_format = "openai"      # preferred surface; "anthropic" also valid where offered
# auth = "api_key"         # openrouter: also "oauth_pkce"; anthropic: also "plan_token"
# api_key_env = "MOONSHOT_API_KEY"   # override; default from registry
# api_key = ""             # inline (0600 file), discouraged in favor of env/Keychain
# models = []              # optional allowlist shown in /v1/models
# base_url = ""            # escape hatch override

[cloud.providers.zai]
enabled = false
endpoint_profile = "api"   # "api" | "coding_plan"
# ...same fields
```

Pydantic: new `CloudConfig` + `CloudProviderConfig` models on `NetllmConfig.cloud` with full
defaults; **absent section ⇒ behavior identical to v0.4.2**. `provider` ids for cloud rows do NOT
extend the closed `ProviderId` Literal; materialized backends use `provider="custom"` with a new
optional `cloud_provider: str = ""` tag on `Backend`/`BackendOverride` (additive field, ignored by
old readers) — this avoids the Literal-widening downgrade hazard entirely.

**Compat rules (the non-breaking contract):**

- Existing `[[routing.backends]]` cloud rows and env-key auto-inject keep working unchanged.
- `_inject_*_cloud_backend` becomes: "if `cloud.providers.{openai,anthropic}` is absent from
  config, fall back to legacy env-triggered inject" — a one-time migration on `netllm init`/first
  save can materialize legacy env keys into `[cloud.providers.*].enabled=true` rows (same
  `lan_defaults_applied` one-shot idiom, flag `cloud_defaults_applied`).
- `cloud.enabled=false` hard-disables all cloud injection AND all `local=false` backend rows tagged
  as cloud, and wins over `allow_cloud` policies. `x-netllm-local-only: 1` continues to win over
  everything.
- New fields only; nothing renamed/removed; `_drop_none_values` + defaults handle old files.

### 3.3 Routing semantics

Materialization: at config-apply time (`service.apply_config`), each enabled provider entry expands
into an in-memory `Backend` (`local=False`, `cloud_provider=<id>`, resolved base_url per
region/profile/api_format, key via `api_key`/`api_key_env`/registry env). No persistence of
derived rows.

Fallback modes (evaluated in `resolve_routing` / candidate assembly in `pool.backends_for_model`):

- **`fallback="cloud"`** (default): candidates = local + peers first; cloud backends appended as a
  trailing tier (generalizing today's `_anthropic_fallback_backends` to both API surfaces). Cloud is
  reached when no local/peer serves the model, or when all local candidates are offline/at
  back-pressure cap.
- **`fallback="local"`**: cloud tier first (by provider order, then `failover` semantics), local
  mesh appended as trailing tier — used when the user prefers frontier cloud models but wants the
  LAN to absorb outages/quota exhaustion.
- **`fallback="none"` or `fallback_enabled=false`**: no automatic cross-tier promotion; cloud only
  via `allow_cloud` policies, `x-netllm-backend` pins, or models that exist solely in the cloud
  catalog.

Health/catalog: keyed providers with a models endpoint get probed through the normal health cache
(auth failures surface as a `needs_key`/`auth_failed` status rather than offline); `zai` uses the
registry's static catalog. Cloud models appear in `/v1/models` (namespaced only if collisions:
alias map handles `kimi-k3` vs local names naturally).

### 3.4 Secrets

- Precedence per provider: inline `api_key` → `api_key_env` → registry default env var.
- macOS: extend `KeychainStore.Account` with `moonshot_api_key`, `zai_api_key`,
  `openrouter_api_key`; extend `PythonRuntime.injectCloudAPIKeys` to inject all five env vars.
- Keys never leave the node: `config_summary` continues to expose only `api_key_set: bool`; the
  admin API accepts keys write-only (same preserved-on-omit merge as backends today).
- Restart requirement: Phase 1 keeps "restart to apply env keys" (documented in UI); Phase 2's
  admin write path accepts inline keys and hot-applies, removing the restart for dashboard/CLI
  users; the mac app moves to the admin path in Phase 4.

### 3.5 UI surfaces (all phases gated)

- **macOS menubar** (`MenubarPopoverView.swift`): a "Cloud" row showing aggregate state
  (`Cloud: off` / `Cloud: 2 providers` / `Cloud: fallback`), with a toggle for `cloud.enabled` and
  an "Open Cloud Settings…" action.
- **macOS Settings**: new **Cloud** sidebar row (Config section) replacing the Routing-embedded
  `CloudFailoverSettings`: master toggle, fallback direction picker, five provider cards
  (enable toggle, region/profile picker, auth mode, SecureField key → Keychain, key-status dot,
  "Test" button hitting the models endpoint, ToS/unofficial-warning notes from the registry).
  Old Routing section keeps a link ("Moved to Cloud") for one release.
- **/ui/ dashboard**: new **Cloud** tab mirroring the same controls (vanilla JS, existing
  `data-tab` + `configDraft` + `POST /netllm/v1/admin/config` pattern); key entry is write-only.
- **CLI**: `netllm cloud` Typer sub-app — `list` (providers, enabled, key status, health),
  `enable|disable <provider>` , `set-key <provider>` (prompt, no echo; `--env` to reference an env
  var instead), `fallback cloud|local|none|on|off`, `test <provider>`, `connect openrouter`
  (Phase 5 PKCE).

## 4. Phases (each independently shippable; gate = tests green + default-off/behavior-preserving)

### Phase 0 — Contract (netllm-core only)
`cloud_providers.py` registry; `CloudConfig`/`CloudProviderConfig` models; `cloud_provider` tag on
`Backend`/`BackendOverride`; save/load round-trip + deep-merge tests (`tests/test_config*.py`
patterns). **No behavior change** — nothing reads the new section yet.
*Gate:* old configs load byte-identically; new section round-trips; `scripts/ci.sh`.

### Phase 1 — Routing engine
Materialize enabled providers into backends; implement `cloud.enabled` master gate,
`fallback` modes, generalized cloud fallback tier for both API surfaces; legacy env-inject
preserved when section absent; `cloud_defaults_applied` migration; health/catalog integration
(static GLM catalog); `config_summary` gains a `cloud` slice.
*Gate:* `tests/test_openai_cloud_compat.py` / `test_anthropic_cloud_compat.py` untouched and green;
new per-provider compat tests (mock SDK clients) for all five; fallback-direction tests in
`tests/test_routing_hardening.py` style.

### Phase 2 — CLI + admin API
`netllm cloud` command group; `POST /netllm/v1/admin/config` handles `cloud` section (write-only
keys, preserve-on-omit); hot-apply without restart; `netllm status` shows cloud tier.
*Gate:* CLI round-trip tests; admin merge tests; `netllm doctor` check for enabled-but-keyless
providers.

### Phase 3 — Web dashboard
Cloud tab in `netllm_agent/static/` (index.html tab + dashboard.js section + tokens-only CSS);
`emptyConfigDraft()` extended.
*Gate:* manual e2e + existing dashboard smoke; no schema drift vs `config_summary`.

### Phase 4 — macOS app
`NetllmConfigDocument.swift` cloud section; Settings Cloud tab; menubar Cloud item/toggle;
Keychain accounts + `injectCloudAPIKeys` for the three new vars; switch key writes to the admin
API to drop the restart requirement (env injection kept as fallback).
*Gate:* `scripts/verify-before-pr.sh` (swift build) + menubar e2e; config written by old app
versions still merges cleanly (deep-merge guarantee).

### Phase 5 — Auth flows beyond keys
OpenRouter **OAuth PKCE** (`netllm cloud connect openrouter` + mac app button: localhost callback →
`POST /api/v1/auth/keys` → store as key in Keychain/env). Anthropic **plan_token** auth mode
(Bearer, opt-in, "unofficial — documented for Claude Code CI only" warning). OpenAI stays key-only
until a public OAuth client exists (registry `auth_modes` makes adding it later a data change).
*Gate:* PKCE flow test against mocked endpoints; token-mode header tests (Bearer vs x-api-key).

### Phase 6 — Docs + release
`config.example.toml` `[cloud]` section; `docs/editor-integration.md` cloud section rewrite;
AGENTS.md/README touch-ups; workspace-wide version bump (`test_version_sync.py`); release notes.

## 5. Risks / open questions

1. **Z.ai Anthropic endpoint on pay-as-you-go keys** is undocumented (documented under Coding
   Plan only) — verify at Phase 1 integration; default `zai` to the OpenAI-format `paas/v4` URL.
2. **Plan-auth legality** (Anthropic plan token in a router; any future OpenAI plan flow): ship
   behind explicit opt-in with the registry `notes` warning; never default.
3. **Schema triple-mirror drift** (Python/Swift/JS) — **resolved for cloud provider display
   metadata**, the part most likely to drift as providers get added or notes/regions change:
   `GET /netllm/v1/cloud/providers` (`admin.cloud_provider_registry_payload`) is now the single
   source of truth for `id`/`display_name`/`notes`/`regions`/`auth_modes`/`default_api_format`.
   The dashboard already consumed this shape via `config_summary`'s `cloud.providers` (no
   Python-side change needed there — its only hardcoded piece, the 5-id iteration list, is now a
   documented offline-only bootstrap, `CLOUD_PROVIDER_IDS_BOOTSTRAP`, superseded by the live
   `Object.keys(draft.providers)` once connected). The macOS app fetches the same endpoint
   (`AgentAPI.cloudProviderRegistry`) into `SettingsViewModel.cloudProviderRegistry`, falling back
   to `SettingsViewModel.cloudProvidersBootstrap` only when the agent is unreachable — so a stale
   Swift-side `notes` string can no longer diverge from the Python registry in steady state.
   **Still open, deliberately out of scope:** a generic schema for the *editable* config shape
   (routing/discovery/swarm/agent/ui sections) that would let Swift/JS render forms without any
   hand-authored structs at all — that's the larger routing-hardening-plan.md follow-up and remains
   future work; today those sections still rely on additive fields + deep-merge (§3.2) to stay
   non-breaking across the three mirrors.
4. **Model ID churn** (Moonshot discontinued its k2 preview family with ~6 months notice): keep
   static catalogs minimal, prefer live `GET /models` wherever offered, and treat registry model
   lists as display hints, not routing constraints.
5. **Key-in-config vs env**: inline `api_key` stays supported (file is 0600) but every UI steers
   to Keychain/env.
