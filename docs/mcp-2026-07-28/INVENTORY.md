# MCP 2026-07-28 impact inventory: AI-Advantage and matthewdcage

Compiled 2026-08-03. Dependency pins below were read from the actual manifest
contents via GitHub code search match fragments, so **the version pins are
verified**. What is not verified is runtime behaviour: no AI-Advantage repo could
be cloned or fully read from this session, so archetype and risk band are inferred
from pins plus detected code signals, not from reading the server code.

Run `scripts/mcp-v2-triage.sh` locally to confirm and to pick up anything code
search does not index.

## The headline: the two ecosystems are in completely different danger

This is the single most important thing in this document, and it is not what you
would guess.

**Python: 10 of 12 servers have unbounded pins and are already drifting.**
A pin like `mcp>=1.6.0` or `mcp[cli]>=1.15.0` accepts **mcp 2.0.0**, which shipped
2026-07-28. Any clean install, fresh CI runner, or rebuilt container pulls v2 and
the server stops working. No code change on your side is required to break these.
They may already be broken.

**TypeScript: all 22 are safe from drift, and none of them can accidentally move.**
Every one is caret-pinned on `^1.x`. A caret range cannot cross a major, and more
decisively the v2 TypeScript SDK ships under **new package names**
(`@modelcontextprotocol/core`, `/server`, `/client`). `@modelcontextprotocol/sdk`
simply has no 2.x to drift into. These repos are stable indefinitely and migrate
only when you choose to.

So the urgent work is Python and it is a dependency-pinning problem, not a protocol
problem. The protocol migration is the larger but far less urgent body of work, and
it is concentrated in TypeScript.

### A second, independent problem found along the way

Most Python repos depend on the **third-party `fastmcp` distribution**, not the
official SDK. That package is at 3.4.5 stable (4.0.0b1 is beta). Pins like
`fastmcp>=2.0.0` and `fastmcp>=2.3.1` therefore **already resolve to 3.4.5**, a
major-version jump that happened silently and has nothing to do with the
2026-07-28 spec. If any of these repos have failed a fresh install recently, this
is the more likely cause. Treat it as a separate, earlier-dated incident.

Do not conflate the two packages. `fastmcp` (third-party) and `mcp.server.fastmcp`
(a module inside the official SDK) are different things with different migration
paths, and several repos below depend on both at once.

## AI-Advantage, Python

| Repo | `fastmcp` pin | `mcp` pin | State |
|---|---|---|---|
| `wine-experience-xero-mcp-py` | `>=2.12.3` | `mcp[cli]>=1.15.0` | **P0** both unbounded, takes mcp 2.0.0 |
| `wineexperience-xero-mcp-http-server` | `>=2.12.3` | `mcp[cli]>=1.15.0` | **P0** both unbounded, takes mcp 2.0.0 |
| `mcp-zoho-books` | `>=2.13.0` | `mcp>=1.6.0` | **P0** both unbounded, takes mcp 2.0.0 |
| `mcp-google-ads` | `>=2.0.0` | `mcp>=0.0.11` | **P0** both unbounded, already on fastmcp 3.x |
| `mcp-google-search-console` | `==3.0.0b2` | `mcp>=0.0.11` | **P0** pinned to a **beta**, mcp unbounded |
| `mcp-google-cloud` | `>=3.2.0` | not seen | **P1** unbounded |
| `mcp-microsoft-365` | `[azure]>=3.2` | not seen | **P1** unbounded, multi-tenant gateway |
| `mcp-google-workspace` | `>=2.3.3` | not seen | **P1** unbounded, already on fastmcp 3.x |
| `mcp-google-analytics` | `>=2.3.1` | not seen | **P1** unbounded, already on fastmcp 3.x |
| `mcp-google-tag-manager` | `>=2.0.0` | not seen | **P1** unbounded, already on fastmcp 3.x |
| `barcode-qrcode-api-mcp` | `>=2.13.1,<3.0.0` | not seen | **P3** correctly bounded |
| `mcp-lightspeed-xseries` | `>=3.4.2,<4.0.0` | not seen | **P3** correctly bounded |
| `mcp-google-shopping` | referenced, pin not captured | | verify locally |
| `mcp-google-serp` | referenced, pin not captured | | verify locally |
| `mcp-campaign-monitor` | referenced, pin not captured | | verify locally |

`barcode-qrcode-api-mcp` and `mcp-lightspeed-xseries` are the only two repos in the
estate that pinned correctly. They are the model to copy: an upper bound on the
major.

### Immediate mitigation, before any migration work

Bound the majors. This is a one-line change per repo and it stops the bleeding
without committing you to the protocol migration:

```toml
# was: "mcp>=1.6.0"          accepts 2.0.0, breaks
"mcp>=1.6.0,<2.0.0"

# was: "fastmcp>=2.13.0"     already silently moved to 3.4.5
"fastmcp>=2.13.0,<4.0.0"     # or <3.0.0 to pin back to the 2.x line
```

Do this for the five P0 repos first. It buys you the full 12 month deprecation
window to plan properly.

## AI-Advantage, TypeScript

All on the `@modelcontextprotocol/sdk` v1 line. None can drift. Ordered by how far
behind they are, because migration effort scales with that distance.

| Repo | pin | Note |
|---|---|---|
| `bop-chat` (`drug-bot/`) | `^0.5.0` | **pre-1.0**, by far the largest gap |
| `wineraising-onboarding` | `^1.0.0` | also `mcp-servers/wineraising/` at `^1.0.0` |
| `wineraising-mcp-standalone` | `^1.4.0` | |
| `wineraising-admin-mcp-standalone` | `^1.4.0` | |
| `wineexperience-xero-mcp-http-server` | `^1.8.0` | also carries the Python server above |
| `hydra-digital-ops` (`agent-hydra-reporting/`) | `^1.10.2` | |
| `mcp-google-maps` | `^1.11.0` | |
| `medbill-chat` | `^1.17.0`, `^1.17.1` | LibreChat-derived |
| `wine-chat` | `^1.17.0` | LibreChat-derived |
| `bop-chat` (`api/`, `packages/api/`) | `^1.17.1` | LibreChat-derived |
| `librechat-base` | `^1.17.1` | LibreChat-derived |
| `BOP-ZDispense-Ai-Server` | `^1.17.3` | |
| `ncib.gov-mcp` | `^1.17.4` | has an SSE transport factory |
| `mcp-wine-experience-portal` | `^1.17.5` | has a streamable-http transport |
| `aus-healthhive` | `^1.18.2` | |
| `mcp-google-tag-manager` | `^1.18.1` | Cloudflare Workers OAuth provider |
| `ChatUI-Nov-2025` | `^1.21.0` | LibreChat-derived |
| `mcp-se-ranking` | `^1.23.0` | |
| `mcp-pbs-server` | `^1.26.0` | session-bearing, Caddy in front |
| `mcp-scriptstream` | `^1.26.0` | SSE + backward-compatible transports |
| `ai-advantage-apps-hub` | `^1.26.0` | |
| `factory-suite` (`packages/mcp-factory/`) | `>=1.26.0` **peer** | unbounded peer range, see below |

`factory-suite` is the one loose range: an unbounded `>=1.26.0` peer dependency. It
cannot reach v2 (no such package), so it is not a drift risk, but it will float
across the whole 1.x line in consumers. Bound it.

The LibreChat-derived repos (`librechat-base`, `bop-chat`, `wine-chat`,
`medbill-chat`, `ChatUI-Nov-2025`) are MCP **clients**, and their SDK version is
largely dictated by upstream LibreChat. Decide per repo whether you are migrating
them or tracking upstream. They should not be scheduled as your own migration work.

## Verified in full

### matthewdcage/mcp-ai-tool-gateway, P0

The only repo read end to end, and the worst shape in the estate: one process that
is **both an MCP server and an MCP client**, so both sides of the breaking change
land on it at once.

| Signal | Evidence | Consequence |
|---|---|---|
| `mcp>=1.2` unbounded | `requirements.txt` | Fresh install resolves to **mcp 2.0.0**, breaks |
| `from mcp.server.fastmcp import FastMCP` | `gateway_server.py` | `FastMCP` renamed to `MCPServer` in v2 |
| 18 `@mcp.tool()` decorators | `gateway_server.py` | Registration surface to port |
| `await s.initialize()` ×2 | `call()` and `_selftest()` | Handshake **removed**; both sites break |
| `ClientSession`, `stdio_client`, `streamablehttp_client` | `gateway_server.py` | Client construction changes |
| Proxies arbitrary downstream servers | `servers.yaml`, `call()` | Must bridge v1 and v2 upstreams |

Runbook: `runbooks/04-code-execution-sampling-and-skill-factories.md`, gateway
section. Do this first, because a gateway failure masks and amplifies failures in
everything behind it.

### matthewdcage/llm-swarm-router, no impact

An OpenAI-compatible LLM router. No MCP dependency in any manifest, no server or
client construction. The only touchpoint is a client-side `.cursor/mcp.json`
pointing at an external Honcho server, which is host config, not server code. This
branch adds documentation only.

## matthewdcage, remaining

Pins not read (the session could not attach these repos; `add_repo` required an
approval that a non-interactive session cannot surface).

TypeScript: `pbs-mcp-server`, `savoir-finance-quickbooks-mcp-server`, `n8n-mcp-node`,
`vapi-mcp`, `mcp-task-scheduler`, `wineraising-mcp`, `xero-mcp`, `script-stream`,
`drug-bot`, `mcp-google-maps`, `cursor-mcp-installer`, `cursor-mcp-installer-dev`,
`librechat-private-beta-features`.

Python: `mcp-zoho-books`, `google-workspace-mcp`, `minerva-ai`, `ai-browser-use-mcp`,
`agent-libreoffice-cli-mcp`, `bop-ui-automation`, `hydra-digital-reporting-ai`.

Given the AI-Advantage pattern, assume the Python repos here carry the same
unbounded-pin exposure until the triage script says otherwise.

## Structural signals worth knowing

**Session-bearing** (references `Mcp-Session-Id`, which is removed): `mcp-pbs-server`,
`mcp-scriptstream`, `ai-advantage-apps-hub`, `mcp-microsoft-365`,
`wine-experience-xero-mcp-py`, `mcp-google-workspace`, `mcp-lightspeed-xseries`,
`mcp-google-shopping`, `BOP-ZDispense-Ai-Server`, `mcp-google-maps`, `aus-healthhive`,
`mcp-wine-experience-portal`, `bop-chat`.

Several ship reverse-proxy config (`Caddyfile`, `Caddyfile.production`,
`docker-compose.production.yml`). **The proxy layer is in scope**: `Mcp-Method` and
`Mcp-Name` become required request headers and proxies must pass them through.

**Legacy HTTP+SSE** (deprecated, 12 month offramp): `mcp-scriptstream`
(`server/transport/sse-transport.ts`, `backward-compatible-http-transport.ts`),
`factory-suite` (`packages/mcp-factory/runtime/transport/sse.ts`), `ncib.gov-mcp`
(`src/transports/transport-factory.ts`), plus docs references in several Google
servers.

**Code-execution layers**: `bop-chat` (`zdispense-mcp-code-mode/`) and
`mcp-microsoft-365` (`server_code_mode_http.py`). Most affected by the loss of
bidirectional streams. Runbook 04, part (a).

**Tool and skill factories**: `factory-suite` (`packages/mcp-factory/`, with its own
transport layer and Express adapter) and `ai-advantage-apps-hub`. Dynamic per-tenant
tool generation interacts badly with the new cacheable `tools/list`: a shared
`cacheScope` on a per-tenant tool list is both a correctness bug and a data leak.
Runbook 04, part (c).

**Sampling and roots**: documentation hits only (`bop-chat/mcp-docs/`,
`mcp-taiga/MCP_SDK.md`), no implementation call sites detected. Exposure to those
two deprecations looks **low**, which is the one genuinely good result here.

## Suggested order of work

1. **Bound the majors** on the five Python P0 repos. Hours, not days. Stops active breakage.
2. **Bound the rest** of the Python repos and the `factory-suite` peer range.
3. **`mcp-ai-tool-gateway`**, because it fronts everything else.
4. **Session-bearing HTTP servers**, including their proxy config.
5. **SSE transports**, within the 12 month offramp.
6. **The TypeScript fleet**, which is stable and can be scheduled deliberately. Start with the largest gaps (`^0.5.0`, `^1.0.0`).

## Caveats

- Repo and path lists include archived, vendored and backup directories
  (`production-copy-may-2026/`, `archived/`, `*-backup/`, vendored litellm inside
  `ai-llm-ops-stack`), which inflate apparent exposure. The triage script prunes
  these; this document does not.
- `librechat-fork` and `librechat-base` are forks carrying upstream MCP code you
  likely do not own.
- "not seen" in the tables means the pin did not appear in a search match fragment,
  not that it is absent. Confirm locally.
