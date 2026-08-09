# Adding a cloud provider (Axis A)

A *cloud provider* is a hosted OpenAI- or Anthropic-compatible API netllm can
route to — Moonshot, Z.ai, OpenAI, Anthropic, OpenRouter, DashScope today.

> **This is not one line.** `08946b6` (DashScope) touched 13 files. After
> Phase 4 the same provider is one registry entry plus **three declared
> hand-written companions** and two generated blocks. That is a real
> reduction and it is not "one line", and this guide will not say it is.

## What it costs, measured

| | |
|---|---|
| Registry entries | 1 (`CLOUD_PROVIDERS` in `packages/netllm-core/src/netllm_core/cloud_providers.py`) |
| Hand-written companions | **3** — see below |
| Generated blocks | 2, produced by one command |
| Test files you edit | **0** — `tests/conformance/kit_cloud.py` parameterizes over the registry |
| Contract vectors you edit | 0 |

Measured by `tests/extending/test_worked_example_cloud.py`, which injects a
fixture entry and drives it through endpoint resolution → config validation →
backend materialization → projection endpoint → CLI listing → dashboard
payload.

## Step 1 — the registry entry

Copy [`templates/cloud-provider.md`](templates/cloud-provider.md) into
`CLOUD_PROVIDERS`. The invariants that bite:

- **`endpoints` is ordered** — the first key is the default region. A stale
  default that no longer names a real endpoint would send every unqualified
  request to the wrong base URL.
- **An unknown region degrades to the default**, it does not raise. Same
  forward-compatibility contract Phase 2 gave unknown config keys.
- **`models_endpoint=False` ⟹ `static_models` non-empty.** The Z.ai
  invariant: a provider that cannot be probed for its catalog and has no
  static list materializes zero models and is silently unusable.
- **`api_key_env` must be `<ID>_API_KEY`.** The macOS app derives that name
  in degraded mode (`KeychainStore.CloudKeyEnv.defaultEnvVar`); a deviation
  would inject the key under a variable nothing reads.
- **`keychain_account` stays empty** unless you genuinely need to deviate —
  the convention `<id>_api_key` is what let Phase 4 delete six identical
  switch cases from `KeychainStore.swift`.

## Step 2 — the three hand-written companions

### Companion 1 — `CloudProviderId` (same file as the registry)

Add your id to the `Literal` directly above `CLOUD_PROVIDERS`.

**Why it is hand-written:** a derived `Literal` blinds basedpyright
([PROGRAM.md](PROGRAM.md) §6.2).

**How it fails without you — and this is the part to read.** *It does not.*
`CloudProviderSpec` is a frozen dataclass and `CloudConfig.providers` is keyed
by `str`, so the `Literal` is only ever an annotation. Nothing raises, no
request misroutes, and until Phase 8 **no test noticed either** — the mirror
ledger claimed this was "asserted by `get_args` equality in `kit_cloud`" and
that assertion existed only in `kit_local`, for the *local* axis. Enforcement
is **static-only**: basedpyright, editor completion, and one assertion.

**Guard:** `tests/conformance/kit_cloud.py::test_cloud_provider_id_literal_matches_the_registry`
(added in Phase 8, precisely because the ledger's claim was false).

### Companion 2 — `cloudProvidersBootstrap` (`apps/netllm-mac/Sources/AppView/SettingsViewModel.swift`)

Add a `CloudProviderInfo(...)` row.

**Why it is hand-written:** it carries display name, notes, regions and auth
modes — prose, not a roster — and [PROGRAM.md](PROGRAM.md) §6.3 refuses to
generate SwiftUI. Generating the prose would be worse than checking it.

**How it fails without you:** the provider does not render in macOS Settings
until the app has reached an agent, which on a first run reads as "the
provider does not exist". Enforcement: **projection**.

**Guard:** `tests/conformance/kit_cloud.py::test_settings_bootstrap_covers_every_provider`

### Companion 3 — the `[cloud.providers.<id>]` stanza in `config.example.toml`

Add a commented stanza next to the others.

**Why it is hand-written:** each stanza carries per-provider commentary —
which auth modes are real, which regions exist, which model ids are
sunsetting. That is documentation, not a roster copy.

**How it fails without you:** as a named test failure. Only *presence* is
asserted; the prose is never checked. Enforcement: **projection**.

**Guard:** `tests/conformance/kit_cloud.py::test_config_example_documents_every_provider`

> This companion previously carried a `phase-8` expiry in
> `tests/conformance/ledgers/mirrors.toml` reading "stanzas fold into the
> worked example". Phase 8 discharged it by guarding presence without
> generating prose, and the ledger row now says so.

## Step 3 — regenerate

```bash
python3 scripts/generate-registry-artifacts.py
```

| Block | File |
|---|---|
| `CLOUD_PROVIDER_IDS_BOOTSTRAP` | `packages/netllm-agent/src/netllm_agent/static/dashboard.js` |
| `bootstrapProviderIDs` | `apps/netllm-mac/Sources/Config/KeychainStore.swift` |

`--check` is in `./scripts/ci.sh lint`.

## What you do **not** touch

- `apps/netllm-mac/Sources/Server/PythonRuntime.swift` — the key-injection
  list is derived from the served registry. A literal `_API_KEY` name coming
  back there fails `test_no_literal_api_key_table_survives_in_pythonruntime`.
  This was the repo's only silent, load-bearing hardcode: a provider added
  everywhere else still stored a key that was never exported, so it 401'd
  against a credential the UI showed as saved.
- `packages/netllm-sdk-openai/src/netllm_sdk_openai/payload.py` and
  `packages/netllm-core/src/netllm_core/capabilities.py` — both are
  provider-agnostic. A new cloud provider costs zero lines in either.

## Checklist

Rows marked ***unguarded*** have no test behind them.

| # | Step | Guard (`tests/conformance/kit_cloud.py::…` unless stated) |
|---|---|---|
| 1 | Entry added, key equals `spec.id` | `test_registry_key_matches_spec_id` |
| 2 | Spec coherent (id, display name, ≥1 endpoint, auth modes, env var, api format) | `test_spec_is_well_formed` |
| 3 | `CloudProviderId` widened (companion 1) | `test_cloud_provider_id_literal_matches_the_registry` |
| 4 | Default region names a real endpoint | `test_default_region_resolves_to_a_real_endpoint` |
| 5 | Unknown region degrades instead of raising | `test_an_unknown_region_falls_back_rather_than_raising` |
| 6 | `models_endpoint=False` ⟹ `static_models` non-empty | `test_static_models_exist_when_there_is_no_live_catalog` |
| 7 | `api_key_env` follows the derivable convention | `test_api_key_env_follows_the_derivable_convention` |
| 8 | Keychain account resolves to `<id>_api_key` | `test_keychain_account_follows_the_convention` |
| 9 | Served on `GET /netllm/v1/cloud/providers` with the right metadata | `test_registry_payload_carries_every_spec` |
| 10 | A `[cloud.providers.<id>]` subtree survives load → save → load | `test_config_round_trip_preserves_the_provider` |
| 11 | macOS Keychain bootstrap regenerated | `test_swift_bootstrap_covers_every_provider` |
| 12 | Dashboard bootstrap regenerated | `test_dashboard_bootstrap_covers_every_provider` |
| 13 | macOS Settings roster updated (companion 2) | `test_settings_bootstrap_covers_every_provider` |
| 14 | `config.example.toml` stanza added (companion 3) | `test_config_example_documents_every_provider` |
| 15 | No literal API-key table reintroduced in Swift | `test_no_literal_api_key_table_survives_in_pythonruntime` |
| 16 | No new id literal anywhere else | `scripts/check-registry-mirrors.py` (in `ci.sh lint`) |
| 17 | Whole path still works end to end | `tests/extending/test_worked_example_cloud.py` |
| — | **`base_url` is semantically right** (right host, right region, right account) | **unguarded** — a wrong-but-live base URL answers 200 to every probe ([PROGRAM.md](PROGRAM.md) §6.7, §13 item 15) |
| — | **`static_models` ids still exist upstream** | **unguarded here** — the `static_models ⊆ live catalog` canary is Phase G4 and **has not landed**; nothing in this tree checks it |
| — | **The provider's auth actually works** | **unguarded** — 401/403 currently count as *online* in both probes, so a bad key is invisible to health |
| — | **`notes` prose is current** | **unguarded** — never asserted |
| — | **A dated `validated_at` for the facts you just typed** | **not available** — `validated_at` / `catalog_source` are Phase G4 additions to `CloudProviderSpec` and are **not in this tree**; the whole registry is still dated by one module comment that nothing can assert |

## Run it

```bash
uv run pytest tests/conformance/kit_cloud.py -k <your-id>
```
