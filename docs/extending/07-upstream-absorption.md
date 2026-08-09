# Absorbing an upstream change (Axis G)

*Upstream* means a vendor SDK, a cloud provider's API, or a local inference
server's wire shape changing under you.

> **Axis G's machinery does not exist yet.** [PROGRAM.md](PROGRAM.md)'s
> addendum designs the Anthropic payload mirror (G1), `FieldContract` with an
> explicit unknown-field policy (G2), recorded upstream fixtures (G3),
> `validated_at` + the provider canary (G4), capability overrides (G5) and a
> dependency-pin ledger (G6). **None of G1–G6 has landed.** This guide tells
> you what to do today and is explicit about what nothing catches.
>
> Evidence and design:
> [upstream-absorption-map.md](upstream-absorption-map.md),
> [PROGRAM.md](PROGRAM.md) §11 and §13.

## The root cause, stated once

Every translation and probe boundary in the router is a hand-maintained
allowlist of field names with **no counterpart to diff against**, and the two
most dangerous ones sit inside `netllm_core`, which is correctly forbidden
from importing the vendor types that would make a signature diff possible
(`tests/test_sdk_isolation.py`). That isolation rule is not relaxed. The
counterpart has to come from the SDK packages, where imports are legal, and
from recorded real transcripts.

## What exists today

| Boundary | Guard |
|---|---|
| OpenAI chat / embeddings typed params | `packages/netllm-sdk-openai/tests/test_sdk_param_drift.py` — a real signature diff against the pinned SDK |
| OpenAI payload split into `extra_body` | `packages/netllm-sdk-openai/src/netllm_sdk_openai/payload.py` + `test_payload_adaptation.py` |
| Weekly early warning on an SDK bump | `.github/workflows/sdk-canary.yml` |
| Pinned SDK versions are asserted | `tests/test_sdk_versions.py`, `tests/test_sdk_versions_payload.py` |
| Router never imports a vendor SDK in core | `tests/test_sdk_isolation.py` |
| Behaviour of every route, recorded | 373 vectors under `tests/contract/vectors/` |

## Bumping an SDK

1. One SDK package per PR.
2. `uv sync`, commit `uv.lock`.
3. `./scripts/ci.sh sdk`.
4. Update `docs/sdk-versions.md` with the resolved version and date, and name
   the layer you changed (adapter / bridge / agent / probes).
5. Read the upstream changelog and link it in the PR.

## Adding or changing a translated field

`packages/netllm-core/src/netllm_core/anthropic_bridge.py` and
`openai_responses_bridge.py` each hold a bare tuple of field names. Both
silently drop anything not listed and return 200.

**Today the unknown-field policy is neither chosen nor written down.** That
is G2's whole point. Until it lands: adding a field means editing the tuple,
and *removing* one is invisible to CI unless a vector happens to cover it.

## Checklist

Rows marked ***unguarded*** have no test behind them; rows marked
***not built*** describe machinery that does not exist in this tree.

| # | Step | Guard |
|---|---|---|
| 1 | Pinned SDK version recorded and asserted | `tests/test_sdk_versions.py` |
| 2 | OpenAI typed-param drift caught | `packages/netllm-sdk-openai/tests/test_sdk_param_drift.py` |
| 3 | Untyped OpenAI fields reach upstream in `extra_body`, control kwargs stripped | `packages/netllm-sdk-openai/tests/test_payload_adaptation.py` |
| 4 | `netllm_core` still imports no vendor SDK | `tests/test_sdk_isolation.py` |
| 5 | Anthropic client contract unchanged | `packages/netllm-sdk-anthropic/tests/test_client_contract.py` |
| 6 | Streaming still frames correctly | `packages/netllm-sdk-anthropic/tests/test_messages_stream_f30.py`, `tests/contract/scenarios_streaming_errors.py` |
| 7 | No route removed, no envelope changed | `tests/contract/routes.json` (exact set) + 373 vectors + `tests/contract/allowed-divergences.txt` |
| 8 | Any behaviour change carries a declared divergence id | `tests/contract/test_divergence_lint.py` |
| — | **An untyped Messages field does not 502** | **not built (G1)** — `client.py` splats `**payload`; any Messages field the pinned SDK does not type raises `TypeError`, which becomes a **502 after the whole failover budget burns across every candidate backend**. There is no `netllm-sdk-anthropic/payload.py` and no `_SDK_MESSAGES_PARAMS` drift test. This one fires **today**, with no upstream change required |
| — | **Anthropic typed-param drift** | **not built (G1)** — the OpenAI drift test has no Anthropic twin, so `ci.sh sdk` and `sdk-canary.yml` cover one SDK of two |
| — | **Which layer eats a dropped field, and why** | **not built (G2)** — no `wire_contracts.py`, no `FieldContract`, no `unknown_policy`, no drop counter, no `x-netllm-dropped-fields` header, no `scripts/check-wire-allowlists.py` |
| — | **A fifth hand-rolled field allowlist landing somewhere** | **unguarded** — nothing scans for it |
| — | **Ollama / vLLM / LM Studio wire-shape regressions** | **not built (G3)** — `tests/fixtures/` holds only Anthropic bodies and oMLX admin payloads. Every local test hand-builds `{"data":[{"id":…}]}`. A `/v1/models` shape change makes `model_count=0`, which turns a backend into a **catch-all candidate for every model** and produces 404 storms attributed to the wrong backend |
| — | **A cloud provider moving its `base_url` or changing auth** | **not built (G4)** — no `provider-canary.yml`. Worse: 401/403 currently count as **online** in both probes, so a dead credential is invisible to health |
| — | **A `static_models` id disappearing upstream** | **not built (G4)** — nothing checks `static_models ⊆ live catalog`. `zai` has `models_endpoint=False`, so its five-model tuple can never self-heal at all |
| — | **How old the registry's facts are** | **not built (G4)** — no `validated_at`, no `catalog_source`. The whole cloud registry is dated by one module comment that nothing can assert or expire |
| — | **An encoder matching no capability heuristic** | **not built (G5)** — `capabilities.py` defaults unknown names to `chat`, and no test names a model matching *no* heuristic. The documented remedy is a `[routing.model_aliases]` entry; `[routing.model_capabilities]` does not exist |
| — | **httpx / pydantic majors** | **unguarded ceiling** — `httpx>=0.28` is floor-only yet load-bearing in both SDKs, both probes, `FakeFarm`'s transport patch and a `_mounts` reach-in. `tests/test_sdk_versions.py` iterates a hardcoded 2-tuple; the dependency-pin ledger is G6 |
| — | **A vendor changing SSE event names on a live provider** | **permanently undetectable** by anything designed here ([PROGRAM.md](PROGRAM.md) §13 item 14) — `FakeFarm` emits names we authored, and that determinism is load-bearing. Recorded transcripts would catch regressions against the recording, not the vendor changing after it |
| — | **A semantically wrong `base_url`** (right host, wrong region) | **permanently undetectable** — answers 200 to every probe (§13 item 15) |
| — | **A provider silently re-quantizing or shadow-routing a model** | **permanently undetectable** — no wire signature exists (§13 item 16) |
| — | **Mixed-version mesh × upstream change** | **not built** — an old peer applies its own allowlists and fails asymmetrically, attributed to the wrong machine. Needs Phase 6's compat lane, also unlanded |

## Run it

```bash
./scripts/ci.sh sdk
uv run pytest tests/contract -q     # 373 vectors, byte-identical
```

If you are here because something upstream broke and you want the guarantee
back, [PROGRAM.md](PROGRAM.md) §16 is explicit about the order: **G1 first**
— it is the only work in either axis that fixes a defect firing today, and it
lands in `ci.sh sdk` and `sdk-canary.yml` with zero workflow edits.
