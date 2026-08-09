# docs/extending

Parent: [../AGENTS.md](../AGENTS.md).

## Purpose

How to add a thing to netllm — a cloud provider, a local inference server, an
API surface, a control, a config field, a harness — and what each pathway
actually guarantees. Plus the program that built those pathways and the
evidence maps that measured them.

## Ownership

| File | Contains |
|---|---|
| `README.md` | index, the single rule, the four gates, the decision tree |
| `PROGRAM.md` | the adopted program: per-axis contracts, phases, what is *not* worth doing |
| `01`–`07` | one guide per axis; each checklist row names a test or is marked unguarded |
| `08-control-parity.md` | Axis D as built, including the generated obligations table |
| `templates/` | copy-paste stubs — registry entry **plus** every declared companion |
| `extension-cost-map.md`, `lifecycle-inventory.md`, `harness-integration-map.md`, `upstream-absorption-map.md` | measured evidence behind the program |

## Local Contracts

- **The honesty constraint governs everything here.** *"Adding a provider is
  one line" is false and must not be written.* The registry makes omission a
  **build failure**, not a smaller job.
- **Every claim is either (a) backed by a named test you can point at, or
  (b) explicitly marked unguarded.** There is no third category. A guide that
  overstates what the machinery does is worse than no guide, because someone
  will trust it.
- **Every checklist row names its guard.** A row with no test behind it says
  ***unguarded***; a row describing machinery that does not exist in this
  tree says ***not built***, and names the phase that would build it.
- **Every guide ends with its exact `-k` invocation.** If an axis has no kit,
  the guide says so rather than inventing a selector.
- **Never cite a line number.** Cite the identifier. `PROGRAM.md`'s own
  `NetllmConfigDocument.swift:28-35,…` ranges and its `TAB_RENDERERS:2499`
  are all stale, and so is the Phase-4 correction to `:2524`.
- **`PROGRAM.md` is a historical record, not a live spec.** Where the tree
  disagrees with it, the tree wins and the guide says which is stale.
  Known-stale as of Phase 8: the architecture doc slot number (`10` → `11`),
  the Swift line ranges, `TAB_RENDERERS:2499`, and §8's "zero source edits
  beyond the registry entry" claim.
- **This directory is exempt from `scripts/check-doc-paths.py`** — the
  program and the maps deliberately quote future and known-dead paths. That
  exemption is a licence to describe unbuilt work, **not** a licence to be
  wrong about built work. Check your paths by hand.

## Extension contract

- **Owns:** no registry. This is documentation of registries owned elsewhere.
- **Serves:** the contributor-facing contract; every row points into
  `tests/conformance/` or `tests/extending/`.
- **No new mirrors:** a guide must not restate a roster. Roster tables and
  the control-parity table are generated between markers by
  `scripts/generate-registry-artifacts.py`; explanatory prose stays human.

## Verification

```bash
uv run pytest tests/extending -q          # the worked examples
uv run pytest tests/conformance -q        # every kit a guide points at
./scripts/ci.sh lint                      # includes check-doc-paths
```

## Child DOX Index

None — `templates/` is part of this folder.
