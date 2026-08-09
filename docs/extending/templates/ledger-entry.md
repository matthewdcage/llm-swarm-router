# Stub — a ledger entry (the escape hatch)

**Adding a ledger entry is not a fix.** If you are here because a gate
failed, the first question is whether the fact can be derived at runtime,
generated with `--check`, or projection-tested
([../PROGRAM.md](../PROGRAM.md) §1). Only when the answer is a defensible
*no* does a ledger row belong.

Every entry needs a **reason** and an **expiry**. A reason with no date is a
decision nobody will ever revisit; the reason-quality and staleness tests
fail on both.

## `tests/conformance/ledgers/mirrors.toml` — a provider/surface id literal

```toml
[[fact_class.allowed_mirrors]]
glob = "path/to/file"
reason = """what this literal IS -- a capability check, an offline fallback, \
a generated block -- and why it cannot be derived"""
expires = "phase-N — what closes it"      # or: "never — <the standing reason>"
```

Adding a row here also turns
`tests/extending/test_worked_example_*.py::test_the_companion_list_is_exhaustive`
red until the worked example classifies the new mirror as a hand-written
companion, a generated block, or a capability branch — and until the matching
guide says which.

## `tests/conformance/ledgers/surface-seams.toml` — an escaped `Surface` branch

```toml
[[seam]]
file = "packages/netllm-agent/src/netllm_agent/example.py"
member = "MESSAGES"
reason = "why this cannot be a SurfaceSpec field or a SurfaceAdapter member"
expires = "phase-N — what closes it"
```

This ledger is currently **empty**, which is the strongest statement of the
invariant. Any entry is a regression that has to be argued in writing.

## `tests/conformance/ledgers/control-parity.toml` — a missing control

```toml
[[control]]
surface = "macos"                # dashboard | macos | cli
unit = "mycontrol"
reason = "why this surface legitimately does not carry it"
expires = "phase-N — either the surface grows the widget, or this is made permanent with data"
```

```toml
[[row_field]]
model = "RoutingPolicy"
field = "myfield"
reason = "why a client legitimately does not carry this row field"
expires = "phase-N"
```

**Tripwires** ([../PROGRAM.md](../PROGRAM.md) §7), both asserted:

- `intentionally_absent` covering more than **20%** of control descriptors
  means the spec is wrong.
- A local-exceptions ledger reaching **5** entries means the spec is wrong.

In both cases: redesign, do not add entries.
