# Adding a local inference provider (Axis B)

A *local provider* is an inference server netllm discovers on localhost —
oMLX, Ollama, LM Studio, vLLM today.

> **This is not one line.** Before Phase 3 the same provider id was keyed in
> eleven parallel maps across five files and nothing referenced the roster in
> a test. Those eleven collapsed into one registry entry, which is a large
> win — but the entry does **not** carry the whole job. It comes with **three
> declared hand-written companions**, all of them deliberate refusals
> recorded in [PROGRAM.md](PROGRAM.md) §6, and per-surface UI work stays
> manual. The registry makes omission a **build failure**, not a smaller job.

## What it costs, measured

| | |
|---|---|
| Registry entries | 1 (`LOCAL_PROVIDERS` in `packages/netllm-core/src/netllm_core/local_providers.py`) |
| Hand-written companions | **3** — see below |
| Generated blocks | 2, produced by one command |
| Test files you edit | **0** — `tests/conformance/kit_local.py` parameterizes over the registry, and `tests/test_contract.py`'s Swift roster check compares against `default_discovery_providers()` rather than a hardcoded list |
| Contract vectors you edit | 0 |

Measured by `tests/extending/test_worked_example_local.py`, which injects a
fixture entry into the live registry and drives it through discovery URLs →
config validation → schema document → projection endpoint → CLI listing →
dashboard payload → macOS discovery checkboxes. If the cost above ever
becomes wrong, that file goes red.

## Step 1 — the registry entry

Copy [`templates/local-provider.md`](templates/local-provider.md) into
`LOCAL_PROVIDERS`. Field-by-field guidance lives in the `LocalProviderSpec`
docstrings, which are the contract; a summary of the ones people get wrong:

- **`default_ports`** — every port here is probed on **both** `127.0.0.1` and
  `localhost`. Order is scan order.
- **`platforms`** — a `sys.platform` allowlist. A platform nobody enumerated
  (freebsd, aix) still gets the cross-platform providers; only a
  single-platform provider like oMLX is withheld.
- **`host_env`** — set it if the server publishes a `HOST`-style variable
  (Ollama's `OLLAMA_HOST`). Declaring it is what deleted `local.py`'s
  `if provider_id == "ollama"` branch. Set `default_host_port` with it.
- **`api_key_env`** — declared, never derived. The lookup has to *miss* for
  `custom` and `peer:*`, so `netllm doctor` does not tell an operator to set
  a `CUSTOM_API_KEY` that nothing reads.
- **`offline_hint`** — imperative prose (`run [cyan]ollama serve[/]`).
  Genuinely per-provider; not derivable from an id.

**Spec-field rule** ([PROGRAM.md](PROGRAM.md) §7): a field earns its place
only when **≥2 entries** set it non-default. One entrant's quirk is a hook,
not a field. Review the spec shape at every third entrant.

## Step 2 — the three hand-written companions

These are not oversights. Each is a refusal the program made on purpose, and
each is enumerated with the reason it cannot be derived.

### Companion 1 — `ProviderId` (`packages/netllm-core/src/netllm_core/models.py`)

Add your id to the `Literal`.

**Why it is hand-written:** a derived `Literal` blinds basedpyright — no
exhaustiveness checking, no editor completion. [PROGRAM.md](PROGRAM.md) §6.2
refuses to open it to validated `str` for exactly that reason.

**How it fails without you:** loudly. pydantic compiles the `Literal` into
`Backend`'s core schema, so `Backend(provider="<your id>")` raises
`ValidationError` naming every accepted value. Enforcement: **runtime**.

**Guard:** `tests/conformance/kit_local.py::test_provider_id_literal_matches_the_registry`

### Companion 2 — `localProviderBootstrap` (`apps/netllm-mac/Sources/AppView/SettingsViewModel.swift`)

Add `(id: "<your id>", label: "<short label>", port: <first scan port>),`.

**Why it is hand-written:** it carries a label and a scan port per provider,
not just an id, and [PROGRAM.md](PROGRAM.md) §6.3 refuses to generate SwiftUI.
It is projection-tested against the registry instead.

**How it fails without you:** as a named test failure, not at runtime. This is
the copy that had **already drifted** before Phase 3: vLLM was prefilled on LM
Studio's port, so a user accepting the macOS default configured a URL nothing
serves. Enforcement: **projection**.

**Guard:** `tests/conformance/kit_local.py::test_swift_bootstrap_matches_the_registry`

### Companion 3 — `providers` (same file as companion 2)

Add your id to `static let providers = [...]`.

**This is a second, separate roster in the same file**, three lines above
`localProviderBootstrap`. Editing one does not edit the other. `providers` is
the list the macOS Settings discovery section iterates to draw one checkbox
per provider; `localProviderBootstrap` is the label + scan-port table behind
the offline prefill.

**Why it is hand-written:** same refusal as companion 2 —
[PROGRAM.md](PROGRAM.md) §6.3 will not generate SwiftUI. It is pinned to
`default_discovery_providers()` (with `sys.platform` forced to darwin, since
the file only ever ships to macOS) rather than generated.

**How it fails without you:** as a named test failure, not at runtime. In the
app a provider missing from this array simply has no checkbox — no error, no
log line, just an option an operator cannot turn on.

**Guard:** `tests/test_contract.py::test_swift_default_providers_match_python`
— note the **different file**: this is the one companion on this axis whose
guard does not live in `kit_local.py`.

## Step 3 — regenerate, do not hand-edit

```bash
python3 scripts/generate-registry-artifacts.py
```

Rewrites two blocks between markers:

| Block | File |
|---|---|
| `PROVIDERS_BOOTSTRAP` | `packages/netllm-agent/src/netllm_agent/static/dashboard.js` |
| `discovery.providers` | `config.example.toml` |

`--check` runs in `./scripts/ci.sh lint`, so forgetting this is a red build
with the exact command in the error message.

## The one place a provider id may still appear as a literal

`packages/netllm-discovery/src/netllm_discovery/local.py` tests
`provider != "omlx"` for oMLX's proprietary admin/telemetry API. That is a
**capability check**, not a roster copy. `kit_cloud` pins that it stays the
only one: a second such branch means the capability belongs on
`LocalProviderSpec` instead.

Anywhere else, `scripts/check-registry-mirrors.py` fails the build. Adding a
row to `tests/conformance/ledgers/mirrors.toml` is **not** a fix — it also
turns `tests/extending/test_worked_example_local.py::test_the_companion_list_is_exhaustive`
red until this guide classifies the new mirror.

## Checklist

Every row names the test that fails if you skip it. **Rows marked
*unguarded* have no test behind them** — they are advice, and nothing in CI
will notice if you get them wrong.

| # | Step | Guard (`tests/conformance/kit_local.py::…` unless stated) |
|---|---|---|
| 1 | Entry added to `LOCAL_PROVIDERS`, key equals `spec.id` | `test_registry_key_matches_spec_id` |
| 2 | Spec internally coherent (lowercase id, ≥1 port in range, ≥1 platform, hint port really scanned) | `test_spec_is_well_formed` |
| 3 | `ProviderId` widened (companion 1) | `test_provider_id_literal_matches_the_registry` |
| 4 | Every declared port probed on both hosts | `test_every_default_port_is_probed_on_both_hosts` |
| 5 | `port_env` honoured | `test_port_env_reaches_the_candidate_list` |
| 6 | `host_env` honoured for `host:port`, `:port` and bare host | `test_host_env_reaches_the_candidate_list` |
| 7 | Candidate list deduped and `/v1`-normalized | `test_candidates_are_deduped_and_normalized` |
| 8 | `api_key_env` resolves through **both** discovery and `Backend` | `test_api_key_env_resolves_through_both_paths` |
| 9 | `default_api_key` applies on both paths | `test_default_api_key_applies_on_both_paths` |
| 10 | Non-registry ids still get no env hint | `test_non_registry_providers_get_no_env_hint` |
| 11 | Platform gating matches `platforms` | `test_platform_membership_follows_the_spec` |
| 12 | This platform's default roster agrees | `test_default_discovery_providers_matches_this_platform` |
| 13 | An unenumerated platform is not stranded | `test_an_unknown_platform_still_gets_cross_platform_providers` |
| 14 | CLI label present and equal to `short_label` | `test_every_labelled_id_is_in_a_roster`, `test_label_matches_the_spec` |
| 15 | Offline hint names the provider and quotes the scanned port | `test_offline_hint_names_this_platforms_providers_and_ports` |
| 16 | Offline hint does not repeat a port | `test_offline_hint_does_not_repeat_a_port` |
| 17 | Offline hint falls back to registry ports with no probe record | `test_offline_hint_falls_back_to_registry_ports` |
| 18 | `KNOWN_PROVIDERS` / `DEFAULT_API_KEYS` still derived, not copied | `test_discovery_roster_is_derived_not_mirrored` |
| 19 | Agent serves the provider on `GET /netllm/v1/local-providers` | `test_the_agent_serves_the_registry_to_its_clients` |
| 20 | That route is in the exact-set route manifest | `test_the_local_provider_route_is_registered` |
| 21 | Dashboard bootstrap regenerated | `test_dashboard_bootstrap_matches_the_registry` |
| 22 | macOS offline prefill `localProviderBootstrap` added (companion 2), and carries no extras | `test_swift_bootstrap_matches_the_registry`, `test_swift_bootstrap_has_no_extra_providers` |
| 23 | macOS discovery-checkbox roster `static let providers` added (companion 3) | **`tests/test_contract.py::test_swift_default_providers_match_python`** — *not* a `kit_local.py` test, and the only row on this axis whose guard lives elsewhere |
| 24 | No new id literal anywhere else | `scripts/check-registry-mirrors.py` (in `ci.sh lint`) |
| 25 | Whole path still works end to end | `tests/extending/test_worked_example_local.py` |
| — | **Probe semantics are right for your server** (does `GET /v1/models` mean what you think?) | **unguarded** — structural conformance cannot tell you a base URL or a probe verb is wrong ([PROGRAM.md](PROGRAM.md) §7) |
| — | **`offline_hint` prose is accurate and current** | **unguarded** — only its presence is asserted, never its correctness |
| — | **Dashboard and macOS discovery UI actually renders your provider well** | **unguarded** — [PROGRAM.md](PROGRAM.md) §6.3 keeps UI hand-written; the registry only makes *omission* loud |
| — | **The server is reachable at the ports you declared on a real machine** | **unguarded** — no live canary exists for local providers ([PROGRAM.md](PROGRAM.md) §6.7) |

## Run it

```bash
uv run pytest tests/conformance/kit_local.py -k <your-id>
```

That covers every row above **except row 23** — companion 3's guard lives in
`tests/test_contract.py`, so run it explicitly:

```bash
uv run pytest tests/test_contract.py::test_swift_default_providers_match_python
```

Then the whole rail, which includes both:

```bash
uv run pytest tests/conformance tests/extending tests/test_contract.py -q
./scripts/ci.sh lint
```
