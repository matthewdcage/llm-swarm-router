# MCP 2026-07-28 impact inventory: AI-Advantage and matthewdcage

**Status:** partial. Read this section before using the tables.

## What this inventory is, and what it is not

This is a **candidate map**, not a verified per-repo assessment. It was produced from
GitHub code search across the two accounts on 2026-08-03. Code search returns file
paths and repository names, so every row below tells you *that* a repo references a
given MCP construct and *where*. It does not tell you the pinned SDK version, because
file contents in those repositories were not readable from this session.

Two access constraints produced that gap, and both are worth recording because they
will recur:

1. **Cross-owner repositories cannot be attached.** `add_repo` for any `AI-Advantage/*`
   repo fails with `cross-tier adds are not supported in v1: requested
   "ai-advantage/..." but session already has repos from owner(s) [matthewdcage]`. The
   error explicitly suggests the remedy: start a session whose *initial* source is an
   AI-Advantage repo.
2. **File reads are scope-enforced.** `get_file_contents` against an unattached repo
   returns `Access denied: repository ... is not configured for this session`, even
   though code search reaches it. So the repo and path lists below are trustworthy,
   and any claim about pinned versions would not be.

The result: exactly two repositories were fully readable, and they are marked
**verified** below. Everything else is marked **candidate** and needs one command to
confirm, which is what `scripts/mcp-v2-triage.sh` is for.

## Completing this inventory

Run the triage script over local checkouts. It reproduces the per-repo assessment,
including the pinned version that could not be read remotely:

```bash
docs/mcp-2026-07-28/scripts/mcp-v2-triage.sh --all ~/code
docs/mcp-2026-07-28/scripts/mcp-v2-triage.sh --all ~/code --tsv > mcp-impact.tsv
```

It classifies each repo into an archetype, scores risk, and names the runbook that
applies. The bands it emits (P0 to P3) are the same ones used below.

Alternatively, to reproduce this analysis with full file access for the org, start a
fresh Claude Code session with an `AI-Advantage` repository as the initial source,
then attach the rest of the org from within that session.

## Verified repositories

These two were read in full.

### matthewdcage/mcp-ai-tool-gateway, band P0-critical

The highest-risk shape in the estate: a single process that is **both an MCP server
and an MCP client**, so it is exposed to the server-side and client-side breaking
changes simultaneously.

| Signal | Evidence | Consequence under 2026-07-28 |
|---|---|---|
| `mcp>=1.2` unbounded pin | `requirements.txt` | A fresh install today resolves to **mcp 2.0.0** and the server stops working. This is the single most urgent item found. |
| `from mcp.server.fastmcp import FastMCP` | `gateway_server.py` | `FastMCP` is renamed to `MCPServer` in the official Python SDK v2. |
| 18 `@mcp.tool()` decorators | `gateway_server.py` | Tool registration surface to review against the v2 API. |
| `await s.initialize()` in the proxy path | `gateway_server.py` `call()` and `_selftest()` | The `initialize`/`initialized` handshake is **removed**. Both call sites break. |
| `ClientSession`, `stdio_client`, `streamablehttp_client` | `gateway_server.py` | Client construction changes; the gateway must also bridge v1 and v2 upstreams. |
| `SESSION = ins.new_session_id()` per process | `gateway_server.py` | Session-per-process assumption for usage logging. Tolerable on stdio, incorrect if ever exposed over HTTP. |
| Proxies arbitrary downstream servers | `servers.yaml`, `call()` | Mixed-version fleet: it will front both v1 and v2 servers during the migration window. |

Applicable runbook: `runbooks/04-code-execution-sampling-and-skill-factories.md`,
gateway section. Do this repo first, because it is the component most likely to mask
or amplify failures elsewhere.

### matthewdcage/llm-swarm-router, band N/A

No impact. This is an OpenAI-compatible LLM router, not an MCP server. No
`@modelcontextprotocol/*` or `mcp` dependency appears in any manifest, and no server
or client construction code is present. The only MCP touchpoint is a client-side
`.cursor/mcp.json` referencing an external Honcho server, which is host configuration
rather than server code.

## Candidate repositories: AI-Advantage

All rows below are **candidate** status. Bands are provisional, inferred from which
signals appear rather than from a version pin.

### Python servers, `fastmcp` present in `pyproject.toml`

`mcp-google-ads`, `mcp-google-analytics`, `mcp-google-search-console`,
`mcp-google-shopping`, `mcp-google-serp`, `mcp-google-tag-manager`,
`mcp-google-cloud`, `mcp-google-workspace`, `mcp-zoho-books`,
`mcp-campaign-monitor`, `mcp-lightspeed-xseries`, `mcp-microsoft-365`,
`barcode-qrcode-api-mcp`, `wine-experience-xero-mcp-py`,
`wineexperience-xero-mcp-http-server` (in `xero-python-mcp-server/`).

Note the naming trap here: `fastmcp` in a manifest is ambiguous. It may mean the
third-party `fastmcp` distribution (stable 3.4.5, with 4.0.0b1 in beta) or the
`mcp.server.fastmcp` module inside the official SDK. These have **different**
migration paths, and the triage script reports the actual pin so you can tell them
apart. Resolve this per repo before planning any work.

### TypeScript servers, `@modelcontextprotocol/sdk` in `package.json`

`mcp-se-ranking`, `mcp-google-maps`, `mcp-google-tag-manager`, `mcp-pbs-server`,
`mcp-scriptstream`, `mcp-wine-experience-portal`, `ncib.gov-mcp`, `aus-healthhive`,
`BOP-ZDispense-Ai-Server`, `wineraising-mcp-standalone`,
`wineraising-admin-mcp-standalone`, `wineraising-onboarding`,
`wineexperience-xero-mcp-http-server`, `ai-advantage-apps-hub`, `factory-suite`
(`packages/mcp-factory/`), `hydra-digital-ops` (`agent-hydra-reporting/`),
`bop-chat`, `wine-chat`, `medbill-chat`, `ChatUI-Nov-2025`, `chatbox`,
`librechat-base`, `librechat-fork`.

Every one of these is on the `@modelcontextprotocol/sdk` v1 line. There is no
`@modelcontextprotocol/sdk@2`: v2 is a three-package split
(`@modelcontextprotocol/core`, `/server`, `/client`), so each of these repos faces a
package rename in addition to any API changes, plus the Node 20+ and ESM-only
requirement.

### Session-bearing, provisional P1 or P0

Repos referencing `Mcp-Session-Id`, which is removed:

`mcp-pbs-server`, `mcp-scriptstream`, `ai-advantage-apps-hub`, `mcp-microsoft-365`,
`wine-experience-xero-mcp-py`, `mcp-google-workspace`, `mcp-lightspeed-xseries`,
`mcp-google-shopping`, `BOP-ZDispense-Ai-Server`, `mcp-google-maps`, `aus-healthhive`,
`mcp-wine-experience-portal`, `bop-chat`.

Several of these also carry proxy configuration (`Caddyfile`, `Caddyfile.production`,
`docker-compose.production.yml`). Reverse-proxy config is in scope for this migration,
because `Mcp-Method` and `Mcp-Name` become required request headers and proxies must
pass them through. Do not treat the proxy layer as unaffected.

### Legacy HTTP+SSE transport, deprecated with a 12 month offramp

`mcp-scriptstream` (`server/transport/sse-transport.ts` plus
`backward-compatible-http-transport.ts`), `factory-suite`
(`packages/mcp-factory/runtime/transport/sse.ts`), `ncib.gov-mcp`
(`src/transports/transport-factory.ts`), `ai-llm-ops-stack` (vendored litellm),
and documentation references in `mcp-google-shopping`, `mcp-google-ads`,
`mcp-google-content-api`, `mcp-google-analytics`.

### Code-execution layers

`bop-chat` (`zdispense-mcp-code-mode/`) and `mcp-microsoft-365`
(`src/microsoft_365_mcp/server_code_mode_http.py`). These are the servers most
affected by the loss of bidirectional streams and should move long-running work to
the Tasks extension. See runbook 04, part (a).

### Tool and skill factories

`factory-suite` (`packages/mcp-factory/`, including a Node/Express adapter and its own
transport layer) and `ai-advantage-apps-hub`
(`docs/dev-docs/mcp-factory-maintenance-guide.md`). Dynamic and per-tenant tool
generation interacts badly with the new cacheable `tools/list`: a shared `cacheScope`
on a per-tenant tool list is both a correctness bug and a data-leak. See runbook 04,
part (c).

### Sampling and roots

Only documentation hits were found (`bop-chat` under `mcp-docs/`, `mcp-taiga`
`MCP_SDK.md`), with no implementation call sites detected. Exposure to the sampling
and roots deprecations therefore looks **low** across the org, which is the one piece
of genuinely good news in this inventory. Confirm locally, since code search does not
index every file type reliably.

## Candidate repositories: matthewdcage

TypeScript: `pbs-mcp-server`, `savoir-finance-quickbooks-mcp-server`, `n8n-mcp-node`,
`vapi-mcp`, `mcp-task-scheduler`, `wineraising-mcp`, `xero-mcp`, `script-stream`,
`drug-bot`, `mcp-google-maps`, `cursor-mcp-installer`, `cursor-mcp-installer-dev`,
`librechat-private-beta-features` (including `zdispense-mcp-code-mode/`).

Python: `mcp-ai-tool-gateway` (verified above), `mcp-zoho-books`,
`google-workspace-mcp`, `minerva-ai`, `ai-browser-use-mcp`,
`agent-libreoffice-cli-mcp`, `bop-ui-automation`, `hydra-digital-reporting-ai`.

Several of these did not appear in the session's initial repository listing, which was
paginated. Treat this list as additive to, not a replacement for, a local sweep.

## Caveats

- Counts and lists include **archived, vendored and backup paths** (for example
  `production-copy-may-2026/`, `archived/`, `*-backup/`, vendored `litellm` inside
  `ai-llm-ops-stack`). These inflate apparent exposure. The triage script prunes such
  paths; this inventory does not.
- Forks (`librechat-fork`, `librechat-base`) carry upstream MCP code you probably do
  not own. Decide per repo whether you are migrating it or tracking upstream.
- No band here should drive scheduling on its own until the pinned version is known.
  A repo pinned at `mcp==1.9.0` is stable and can be scheduled; the same repo at
  `mcp>=1.2` is already broken on the next clean install. That distinction is the
  whole point of running the triage script.
