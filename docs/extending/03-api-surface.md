# Adding an API surface (Axis C)

A *surface* is a wire dialect the router serves: `chat`, `embeddings`,
`messages` today. Adding one means a new `Surface` member, a new
`SurfaceSpec`, a new `SurfaceAdapter`, routes, and vectors.

> **Read this first.** There is **no `tests/conformance/kit_surface.py`.**
> Axes A, B and D each got a registry-parameterized kit; Axis C did not,
> because it was already the cleanest axis by a wide margin and Phase 5a spent
> its budget on absorbing the four escaped branches instead. The guards below
> are real and named, but they live in `tests/contract/`, and a few of them
> are parameterized over a **hand-written tuple of adapters** rather than over
> the enum — so they are not free the way a kit is. Where that is true this
> guide says so.

## The seam

| Piece | Where |
|---|---|
| `Surface` enum | `packages/netllm-agent/src/netllm_agent/taxonomy.py` |
| `SurfaceSpec` (per-surface *facts*) | same file, `SURFACE_SPECS` |
| `SurfaceAdapter` protocol + `BaseAdapter` | `packages/netllm-agent/src/netllm_agent/service/surfaces/base.py` |
| Concrete adapters | `packages/netllm-agent/src/netllm_agent/service/surfaces/` |
| Route manifest | `tests/contract/routes.json` (generated) |
| Escaped-branch ledger | `tests/conformance/ledgers/surface-seams.toml` |

`SurfaceSpec` exists because four `Surface`-keyed branches had escaped the
adapter package into `candidates.py`, `request_plan.py`, `policy.py` and
`taxonomy.py`. Phase 5a moved them onto the spec, where **adding a surface
means answering each question** instead of silently inheriting whichever
branch happened to be the `else`. The ledger starts and stays **empty**,
which is the strongest form of the invariant: any entry is a regression that
has to be argued in writing.

## Steps

1. **`Surface` member** and a **`SURFACE_SPECS` entry.** `spec_for()` raises
   `KeyError` for a member with no spec — deliberately, so a new surface
   fails loudly at first use rather than inheriting the OpenAI answers.
   Answer all six spec fields explicitly, including the two error-shape ones
   (`missing_credential_message`, `exhaustion_message`): D11 is the finding
   that Messages reads exhaustion-with-no-key as 401 where every OpenAI
   surface answers 404.
2. **An adapter** implementing every `SurfaceAdapter` protocol member.
   Subclass `OpenAIDialectAdapter` or `AnthropicDialectAdapter` if your wire
   shape matches one; otherwise `BaseAdapter`.
3. **Register it in `adapter_for`.**
4. **Routes**, then regenerate the manifest:
   `uv run python scripts/generate-routes-json.py`.
5. **At least one contract vector** for every route whose `surface` is not
   null. This is what makes "does the protocol absorb a new surface"
   CI-answerable rather than a matter of opinion.
6. **Add your adapter class to `ADAPTERS` in
   `tests/contract/test_surface_adapters.py`.** Yes — a test-file edit. This
   axis has no registry-driven kit, and pretending otherwise would be the
   exact over-claim this document set exists to avoid.

## Checklist

Rows marked ***unguarded*** have no test behind them.

| # | Step | Guard |
|---|---|---|
| 1 | `Surface` member has a `SurfaceSpec` | `spec_for()` raises `KeyError` at first use — a runtime guard, not a test |
| 2 | Adapter implements every protocol member | `tests/contract/test_surface_adapters.py::test_adapter_implements_every_protocol_member` |
| 3 | No inherited `NotImplementedError` stub survives | `…::test_adapter_inherits_no_unimplemented_base_member` |
| 4 | Adapter satisfies the runtime protocol | `…::test_adapter_satisfies_the_runtime_protocol` |
| 5 | `adapter_for` maps **every** `Surface` member | `…::test_adapter_for_maps_every_surface` |
| 6 | Error classification is per-dialect | `…::test_error_classification_is_per_dialect` |
| 7 | `wire_error` envelope is surface-native | `…::test_wire_error_is_the_surface_native_envelope` |
| 8 | Capability guard rejects in the right dialect | `…::test_guard_rejects_the_wrong_capability_in_the_right_dialect` |
| 9 | Adapter holds no per-request state | `…::test_adapters_hold_no_per_request_state` |
| 10 | `invoke` signature uniform across adapters | `…::test_invoke_signature_is_uniform` |
| 11 | Only streaming surfaces have a streaming arm | `…::test_only_the_streaming_surfaces_have_a_streaming_arm` |
| 12 | Mid-stream error frames dialect-native and terminated | `…::test_mid_stream_error_frames_are_dialect_native_and_terminated` |
| 13 | Failover engine stays surface-agnostic | `scripts/check-engine-erosion.py` (in `ci.sh lint`) and `tests/contract/test_engine_erosion.py::test_engine_is_surface_agnostic` |
| 14 | No new `Surface` branch escaped the adapter package | `scripts/check-engine-erosion.py --seams` against `tests/conformance/ledgers/surface-seams.toml` |
| 15 | No entry point reimplements the prologue or the failover walk | `tests/contract/test_api_surface.py::test_no_proxy_entry_point_reimplements_the_prologue`, `tests/contract/test_engine_erosion.py::test_no_service_entry_point_still_walks_candidates` |
| 16 | Route manifest is an exact set, regenerated | `uv run python scripts/generate-routes-json.py --check` (in `ci.sh lint`) |
| 17 | Existing 373 contract vectors byte-identical | `uv run pytest tests/contract` + `tests/contract/allowed-divergences.txt` |
| — | **Your adapter appears in `ADAPTERS`** in `test_surface_adapters.py` | **unguarded** — the tuple is hand-written; forget it and rows 2–12 simply never run for your surface. This is the one place Axis C is weaker than Axes A/B/D |
| — | **`SURFACE_MEMBERS` in `check-engine-erosion.py`** stays a superset | **unguarded in this tree** — [PROGRAM.md](PROGRAM.md) §3 Axis C specifies a test-side `set(SURFACE_MEMBERS) >= {m.name for m in Surface}` assertion and it **was not written**; the literal today contains `RESPONSES`, which is not a member, which is the drift the assertion was meant to catch |
| — | **`ScenarioRule` can be scoped to your surface** | **not possible for `responses`** — `SurfaceName` in `models.py` is `chat\|embeddings\|messages`; adding `responses` is Phase F3, unlanded, and is the only F/G item that is not vector-neutral (it takes divergence id D19) |
| — | **`app.py` route modularization** | **not done** — Phase 5b is unlanded. New routes still land in `app.py` |
| — | **Dialect semantics are actually right** | **unguarded** — vectors prove the shape you recorded, not that the shape is what the vendor's clients expect |

## Run it

There is no per-surface `-k` selector because there is no kit. Run the whole
Axis C rail:

```bash
uv run pytest tests/contract/test_surface_adapters.py tests/contract/test_engine_erosion.py -q
uv run pytest tests/contract -q          # 373 vectors, must stay byte-identical
python3 scripts/check-engine-erosion.py --seams
```
