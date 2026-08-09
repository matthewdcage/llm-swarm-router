# Stub — an API surface

Guide: [../03-api-surface.md](../03-api-surface.md) ·
**There is no `kit_surface.py`.** The guards live in `tests/contract/`.

## 1. `Surface` member and its spec

`packages/netllm-agent/src/netllm_agent/taxonomy.py`:

```python
class Surface(StrEnum):
    CHAT = "chat"
    EMBEDDINGS = "embeddings"
    MESSAGES = "messages"
    MYDIALECT = "mydialect"


SURFACE_SPECS: dict[Surface, SurfaceSpec] = {
    ...,
    Surface.MYDIALECT: SurfaceSpec(
        surface=Surface.MYDIALECT,
        excluded_api_formats=frozenset(),      # rows the loop must never pick
        classifier_api_format="openai",        # what the scenario classifier sees
        reads_anthropic_credentials=False,
        missing_credential_message="",         # "" ⟹ 404 model-not-found (D11)
        exhaustion_message="",                 # "" ⟹ 404 model-not-found
    ),
}
```

Answer every field explicitly. `spec_for()` raises `KeyError` for a member
with no spec on purpose — inheriting the OpenAI answers silently is the
failure mode this replaced.

## 2. The adapter

`packages/netllm-agent/src/netllm_agent/service/surfaces/mydialect.py`:

```python
class MyDialectAdapter(OpenAIDialectAdapter):   # or AnthropicDialectAdapter/BaseAdapter
    surface = Surface.MYDIALECT

    def guard(self, requested_model: str, effective_model: str) -> None: ...
    def build_invocation(self, plan: RequestPlan, backend: Backend) -> Invocation: ...
    async def invoke(self, plan: RequestPlan, invocation: Invocation) -> Any: ...
    def wire_error(self, status_code: int, message: str) -> dict[str, object]: ...
    # …every remaining SurfaceAdapter protocol member
```

Register it in `adapter_for`.

## 3. Routes and the manifest

```bash
uv run python scripts/generate-routes-json.py
```

## 4. The test-file edit this axis still needs

`tests/contract/test_surface_adapters.py`:

```python
ADAPTERS = (ChatAdapter, EmbeddingsAdapter, MessagesAdapter, MyDialectAdapter)
```

Forget this and eleven adapter contract tests silently never run for your
surface. This axis has no registry-parameterized kit.

## 5. Vectors

At least one contract vector per route whose `surface` is not null. New
vectors are **new files only** — the existing 373 must stay byte-identical.

## 6. Verify

```bash
uv run pytest tests/contract/test_surface_adapters.py -q
uv run pytest tests/contract -q
python3 scripts/check-engine-erosion.py --seams
```
