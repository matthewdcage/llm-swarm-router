# Copy-paste stubs

One file per extension axis. Each stub is the **registry entry plus every
declared hand-written companion** — not the registry entry alone, because the
entry alone does not work and a template that pretended otherwise would be
the exact over-claim [PROGRAM.md](../PROGRAM.md) forbids.

| Stub | Axis | Guide |
|---|---|---|
| [cloud-provider.md](cloud-provider.md) | A — cloud provider | [../01-cloud-provider.md](../01-cloud-provider.md) |
| [local-provider.md](local-provider.md) | B — local inference server | [../02-local-provider.md](../02-local-provider.md) |
| [api-surface.md](api-surface.md) | C — wire dialect | [../03-api-surface.md](../03-api-surface.md) |
| [control-descriptor.md](control-descriptor.md) | D — CLI / control plane | [../04-cli-and-control-plane.md](../04-cli-and-control-plane.md) |
| [ledger-entry.md](ledger-entry.md) | all — the escape hatch | [../PROGRAM.md](../PROGRAM.md) §7 |
| [agents-extension-contract.md](agents-extension-contract.md) | DOX | [../../architecture/11-extensibility-contracts.md](../../architecture/11-extensibility-contracts.md) |

None of these are generated. They are prose examples kept beside the guides
that explain them; if a spec field is added, the template goes stale until
somebody updates it, and nothing in CI will notice. That is a deliberate
choice — generating a template from a dataclass produces a field list, not an
example, and the commentary is the point.
