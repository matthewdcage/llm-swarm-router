# MCP 2026-07-28 migration pack

Assessment of the MCP **2026-07-28** specification and the v2 SDKs, with migration
guidance for the AI-Advantage and matthewdcage MCP estate.

Compiled 2026-08-03 against primary sources (spec repo, SDK source and wheels,
package registries), with an adversarial verification pass over most of it.

## Start here

| Document | What it is |
|---|---|
| **[GUIDELINES.md](GUIDELINES.md)** | Entry point. Version table, the three traps, breaking-change register, deprecation clocks, sequencing. |
| [INVENTORY.md](INVENTORY.md) | Per-repo impact across both accounts, with verified dependency pins. |
| [scripts/mcp-v2-triage.sh](scripts/mcp-v2-triage.sh) | Classifies any repo, scores risk P0 to P3, names the runbook. |

## Runbooks

| # | Archetype |
|---|---|
| [01](runbooks/01-tools-only-stdio.md) | Tools-only over stdio. Most of the estate. |
| [02](runbooks/02-remote-http-oauth.md) | Remote HTTP with OAuth, session-bearing. Highest risk. |
| [03](runbooks/03-resources-prompts-and-apps.md) | Resources, prompts, subscriptions, MCP Apps. |
| [04](runbooks/04-code-execution-sampling-and-skill-factories.md) | Code execution, sampling/roots dependents, skill factories, gateways. |

## If you read nothing else

1. **Python pins are the emergency.** Ten of twelve Python servers accept `mcp`
   2.0.0 through unbounded pins and break on the next clean install. TypeScript
   cannot drift at all. Fix the pins first, in hours, then plan calmly.
2. **The TypeScript v2 SDK still speaks 2025-11-25 by default.** Upgrading the
   package does not change the wire. Verify the wire, not the lockfile.
3. **zod 3 fails silently at runtime** with the v2 TypeScript packages. No install
   error, no type error.
4. **HTTP+SSE has a three month fuse**, not twelve like everything else.
5. **Go v1.7.0 overwrites `cacheScope` to `public`**, clobbering explicit `private`
   values. Cross-tenant disclosure risk.

## Triage a repo

```bash
scripts/mcp-v2-triage.sh /path/to/repo
scripts/mcp-v2-triage.sh --all ~/code --tsv > mcp-impact.tsv
```

## Confidence

Five of seven research dimensions were adversarially verified, and the pass
corrected real errors in every one, including four in this project's own starting
assumptions.

**Two dimensions were not verified** because the research workflow hit a spend limit:
`authorization` (CIMD, RFC 9207, `application_type`, issuer binding, EMA) and
`typescript-sdk` (package split, `registerTool`, zod 4, codemod). Content from those
areas is single-sourced and flagged inline. Confirm anything load-bearing against the
SDK source before acting on it.
