# Stub — an `Extension contract` section for a package `AGENTS.md`

Every package on the DOX rail carries one. Three statements, no more:
**what registry it owns**, **what projection it serves**, and **the
no-new-mirrors prohibition**. A package that owns nothing says so — "consumes
only" is a contract, not an absence.

```markdown
## Extension contract

- **Owns:** `<REGISTRY_NAME>` in `<module path>` — the single statement of
  <fact class>. Adding an entry: [docs/extending/0N-<axis>.md](...).
- **Serves:** `<projection function>` → `<HTTP route>`; every other surface
  fetches it rather than keeping a copy.
- **Consumes only:** `<REGISTRY>` from `<package>` — never re-state an id
  here.
- **No new mirrors:** a provider, surface or harness id literal may not
  appear in a file `tests/conformance/ledgers/mirrors.toml` does not already
  name. Adding a ledger row is not a fix
  ([templates/ledger-entry.md](docs/extending/templates/ledger-entry.md)).
- **Debt:** `<hand-written mirror>` at `<Symbol name — never a line number>`.
  Removal target: <phase or condition>.
```

## The one rule about citing a mirror

**Anchor on the identifier, never on a line number.**
[PROGRAM.md](../PROGRAM.md) §8 cites the macOS typed-struct mirrors at
`NetllmConfigDocument.swift:28-35,49-88,99-119,122-150`. **Every one of those
ranges is already stale** — the struct declarations sit at 27, 36, 55, 102,
113, 130 and 141 today, and will move again.

`TAB_RENDERERS` makes the point sharper still. [PROGRAM.md](../PROGRAM.md)
§3 Axis D cites it at `:2499`. `tests/conformance/projections.py` corrected
that in Phase 4 to `:2524`. Today it is at **`:2624`** — *the correction went
stale too*. That is the whole argument: there is no line number you can write
down that stays true, only symbols you can search for.

Every projection helper in `tests/conformance/projections.py` searches for a
marker string and *reports* the line it found, which is the only direction
that survives.
