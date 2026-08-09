# Integrating a CLI agent / harness (Axis F)

A *harness* is an external AI coding CLI or editor pointed at netllm — Claude
Code, Codex CLI, Gemini CLI, Cursor, Honcho, Buzz today.

> **Axis F's machinery does not exist yet, and this guide will not pretend it
> does.** [PROGRAM.md](PROGRAM.md)'s addendum designs `HarnessSpec`,
> `WireRequirement`, `kit_harness`, per-harness golden vectors and
> `connect --verify`. **Phase F1 has not landed.** What exists today is
> `KnownHarness` — five cosmetic fields, no machine-readable wire
> requirement — plus a second, shadow roster inside `connect.py`.
>
> Evidence and the full design:
> [harness-integration-map.md](harness-integration-map.md),
> [PROGRAM.md](PROGRAM.md) §10.

## What exists today

| Piece | Where | Shape |
|---|---|---|
| `KNOWN_HARNESSES` | `packages/netllm-core/src/netllm_core/known_harnesses.py` | `id`, `display_name`, `cli_commands`, `install_hint`, `docs_url` |
| PATH detection | `packages/netllm-core/src/netllm_core/harness_detection.py` | `shutil.which` over `cli_commands` |
| Wiring guides | `packages/netllm-cli/src/netllm_cli/commands/connect.py` `_guides()` | **a second hand-written roster**, keyed by the same ids |
| Served registry | `netllm_agent.admin.harness_registry_payload` → `GET /netllm/v1/harnesses` | id, name, detected, configured, enabled |
| Icons | `packages/netllm-agent/src/netllm_agent/static/` | one file per id |
| Attribution | `packages/netllm-core/src/netllm_core/source_identity.py` | virtual key `netllm-<id>`, UA needles |

## Adding a harness today

1. **`KNOWN_HARNESSES` entry.** Binary names are `shutil.which` candidates,
   checked in order. Three of the six current entries are explicitly
   best-effort guesses the code itself flags; if yours is unverified, say so
   in a comment beside it — there is no `binary_verified` field yet
   (that is F2).
2. **A `_guides()` entry in `connect.py`.** This is the shadow roster. It is
   a *second* hand-written dict keyed by the same ids, and until F1 deletes
   it, omitting your entry means `netllm connect <id>` passes validation and
   then raises `KeyError` in the primary onboarding command.
   Phase 8 added the parity assert that catches this.
3. **An icon file** matching the naming convention.
4. **Attribution**, if the harness should be routable as a source: a
   `routing.sources` entry with `match.user_agent_contains`, or the virtual
   key `netllm-<id>`. `netllm connect --toggle` writes the source row but
   **not** the match block, so a user who never changes their API key falls
   silently to `default`. That gap is F4; it is real today.

## Checklist

Rows marked ***unguarded*** have no test behind them; rows marked
***not built*** describe machinery that does not exist in this tree.

| # | Step | Guard |
|---|---|---|
| 1 | Entry added to `KNOWN_HARNESSES`, id in the declared roster | `tests/test_known_harnesses.py::test_registry_ids_match_phase1_deferred_set` |
| 2 | Ids unique | `tests/test_known_harnesses.py::test_registry_ids_unique` |
| 3 | Display name and at least one `cli_commands` candidate | `tests/test_known_harnesses.py::test_every_entry_has_display_name_and_cli_commands` |
| 4 | Lookup by id works and unknown ids return `None` | `tests/test_known_harnesses.py::test_get_known_harness_found`, `…::test_get_known_harness_unknown_returns_none` |
| 5 | **A `connect.py` `_guides()` entry exists** | `tests/test_known_harnesses.py::test_every_known_harness_has_a_connect_guide` (added in Phase 8 — before it, this was the hard-crash path) |
| 6 | Icon file present on disk | `tests/test_admin_harnesses.py::test_every_known_harness_has_an_icon_file_on_disk` |
| 7 | Served on `GET /netllm/v1/harnesses` with detected/configured/enabled independent | `tests/test_admin_harnesses.py::test_harnesses_endpoint_serves_registry`, `…::test_detected_is_independent_of_configured_and_enabled` |
| 8 | Adding the endpoint did not disturb config/status payloads | `tests/test_admin_harnesses.py::test_config_and_status_payloads_unaffected_by_this_endpoint` |
| 9 | No harness id literal in a file the mirror ledger does not name | `scripts/check-registry-mirrors.py` — **but see below**: there is **no `harness-id` fact class** in `tests/conformance/ledgers/mirrors.toml`, so this row is currently **unguarded**. Adding the fact class is a Phase-0 addendum item that did not land |
| — | **The binary name is right** | **unguarded** — three of six are guesses; `binary_verified` / `verified_at` are F2 fields that do not exist. `detected` is load-bearing in the CLI, dashboard and macOS badge, so a wrong guess reads as "not installed" |
| — | **The wiring instructions actually work** | **unguarded** — `connect --verify` (F2) does not exist. Nothing replays the printed recipe against a live agent |
| — | **The harness's wire requirement is declared and enforced** | **not built** — `WireRequirement` (surface, base-URL shape, wire api, streaming, tool use, `requires_fields`) is F1/F2. Today "Claude Code needs the Messages surface", "Codex needs `/v1/responses`" are English prose in two unlinked places |
| — | **A route your harness depends on cannot be deleted** | **partly guarded** — `tests/contract/routes.json` is exact-set, so a deleted route fails *some* test; it does not fail *your harness's* test by name, because `kit_harness` does not exist |
| — | **A field your harness needs survives both bridges** | **not built** — the `requires_fields ⊆ recorded upstream body` assertion is F2. Drop `thinking` from the Anthropic bridge today and no test names Claude Code |
| — | **A rule can be scoped to your harness alone** | **not possible for Codex** — `ScenarioRule.surfaces` is `chat\|embeddings\|messages`; `/v1/responses` reports `surface="chat"`, so a Cursor-scoped rule silently also hits Codex. That is F3 (divergence D19), unlanded |
| — | **`netllm connect --toggle` writes the match block** | **not built** — F4. It writes `{id, enabled, known_id}` and no `match`, so attribution falls to `default` unless the user also changes their API key |
| — | **Editor/shell config is written for the user** | **deliberately never** — [PROGRAM.md](PROGRAM.md) §14. `env` steps stay printed |

## Run it

There is no `tests/conformance/kit_harness.py`. Run what does exist:

```bash
uv run pytest tests/test_known_harnesses.py tests/test_admin_harnesses.py \
             tests/test_harness_detection.py tests/test_cli_connect.py -q
```

If you are adding a harness and want the guarantees the rows above say are
missing, the shortest honest path is [PROGRAM.md](PROGRAM.md) §16's cut line:
the `_guides` parity assert (now landed), `HarnessSpec` with `wire` and
`user_agent_needles`, and two golden vectors.
