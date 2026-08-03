# MCP 2026-07-28 migration: guidelines and recommendations

Entry point for the migration. Read this before opening any runbook.

Compiled 2026-08-03 against primary sources: the spec repo, SDK source and wheels,
and the package registries. Where a claim survived an adversarial refutation pass it
is marked **verified**. Two dimensions did not get that pass (see
[Confidence and known gaps](#confidence-and-known-gaps)) and are marked accordingly.

## Executive summary

The 2026-07-28 revision turns MCP from a stateful, bidirectional protocol into a
stateless request/response protocol. The `initialize` handshake, the
`Mcp-Session-Id` header, and the entire server to client request channel are gone.
That last one is the part most people underestimate: a server can no longer *ask*
the client for anything mid-call. Sampling, elicitation and roots now travel in-band
as Multi Round-Trip Requests, which the client replays.

For an estate of roughly 50 MCP repositories, the practical shape is:

- **The urgent work is dependency pinning, not protocol.** Unbounded Python pins
  silently resolve to `mcp` 2.0.0 and break on the next clean install. Fix that
  first, in hours, then plan the rest calmly. See [INVENTORY.md](INVENTORY.md).
- **The protocol migration is large but not urgent.** Deprecated features have a
  12 month floor, with one sharp exception below.
- **Three traps will cost you days if you meet them cold**: zod 3 fails silently at
  runtime, the TypeScript v2 SDK still speaks the *old* protocol by default, and the
  Go SDK stamps `cacheScope: "public"` on results you explicitly marked private.

## Version and package reality

Verified against the registries, not the release blogs. Several widely-circulated
posts were written against betas and are now wrong.

| Ecosystem | v1 line (maintenance) | v2 line (2026-07-28) |
|---|---|---|
| Python | `mcp` 1.29.0 | **`mcp` 2.0.0**, plus new `mcp-types` 2.0.0, `httpx2>=2.5.0`, `pydantic>=2.12`, Python >=3.10 |
| TypeScript | `@modelcontextprotocol/sdk` 1.30.0 | **package split**, all 2.0.0: `core`, `server`, `client`, `node`, `express`, `hono`, `fastify`, `server-legacy` |
| Go | | `github.com/modelcontextprotocol/go-sdk` **v1.7.0** |
| C# | | NuGet `ModelContextProtocol` **2.0.0** |
| Rust | | beta |
| Codemod | | `@modelcontextprotocol/codemod` 2.0.0 |

### Corrections to the widely-published narrative

These were all wrong in circulating write-ups, and two of them were wrong in this
project's own first draft:

1. **There is no `@modelcontextprotocol/sdk@2`.** The v2 TypeScript SDK ships under
   new package names. `@modelcontextprotocol/sdk` stays on the 1.x maintenance line.
   A caret range like `^1.26.0` therefore cannot drift into v2.
2. **The v2 TypeScript packages are NOT ESM-only.** All ship dual builds: `"main":
   "./dist/index.cjs"` plus an exports map with both `import` and `require`
   conditions. A CommonJS project can `require()` them. What is true is
   `"type": "module"` and `"engines": {"node": ">=20"}`. **Do not budget an ESM
   conversion.**
3. **It is not a three-package split.** At least eight packages are published at
   2.0.0. `node`, `express`, `hono`, `fastify` carry the transports and framework
   adapters; `server-legacy` holds the frozen Authorization Server helpers.
4. **The TypeScript class was not renamed.** It is still `McpServer`. Only the
   import path moves. The `FastMCP` to `MCPServer` rename is **Python only**.
5. **The 12 month policy is SEP-2596**, not SEP-2577. SEP-2577 is only the
   roots/sampling/logging deprecation.
6. **Per-request `_meta` requires `protocolVersion` and `clientCapabilities`.**
   `clientInfo` is optional, not the required key.

## The three traps

### 1. zod 3 fails silently at runtime (TypeScript)

`zod ^4.2.0` is a hard floor for the v2 packages. With zod 3 installed the code
**compiles and installs cleanly, then misbehaves at runtime**. There is no install
error and no type error to catch it.

```bash
npm ls zod          # must resolve to 4.2.0 or later, with no duplicate zod 3 nested
```

Check for a nested duplicate as well as the top-level version. A transitive zod 3
under another dependency is enough to cause it.

### 2. The v2 TypeScript SDK defaults to the legacy 2025-11-25 era

Installing `@modelcontextprotocol/server@2.0.0` does **not** put 2026-07-28 on the
wire. The protocol-version constants exported from the v2 packages still read
`2025-11-25`:

- `LATEST_PROTOCOL_VERSION`, `SUPPORTED_PROTOCOL_VERSIONS` and
  `DEFAULT_NEGOTIATED_PROTOCOL_VERSION` are re-exported from `core` by `server`, so
  importing them from either package gives you the old value.
- **Do not import protocol-version constants from any v2 TypeScript package.**
  Hardcode `"2026-07-28"` or take it from `mcp-types`.

Upgrading the package is necessary but not sufficient. Verify the wire, not the
lockfile.

### 3. The Go SDK clobbers `cacheScope` to `public`

Go v1.7.0 `mcp/protocol.go` sets `c.CacheScope = "public"` unconditionally, with no
emptiness guard, at six call sites in `mcp/server.go`. On the `resources/read` path
it runs **after** your handler returns, so a handler that explicitly sets
`CacheScope: "private"` has it silently overwritten to `"public"` on the way out.

Treat this as a **cross-tenant disclosure risk**, not a code-review item. "Always set
cacheScope explicitly" does not work in Go v1.7.0, because explicit values are not
honoured. Until it is fixed upstream you need a transport-level override or an
upstream patch.

Compounding it: the Python client's `share_public` option trusts the server's
"public" label across every principal sharing a cache store. Its own docstring warns
that a mislabelled response leaks across tenants. **Never enable `share_public`
against a Go server.**

## Breaking change register

| Change | SEP | Detection signal |
|---|---|---|
| `initialize` / `notifications/initialized` removed entirely | 2575 | `rg '\.initialize\('` |
| `Mcp-Session-Id` removed | 2567, 2575 | `rg -i 'mcp-session-id'` |
| Server to client JSON-RPC requests removed; use MRTR | 2322 | `rg 'createMessage|listRoots|elicit'` |
| Streamable HTTP is POST-only; GET stream endpoint removed | 2575 | reverse-proxy config, `app.get('/mcp'` |
| SSE resumability removed (`Last-Event-ID`, event IDs) | 2575 | `rg 'Last-Event-ID|resumptionToken|EventStore'` |
| `Mcp-Method` required on every POST | 2243 | proxy/WAF/ingress config |
| `Mcp-Name` required on `tools/call`, `resources/read`, `prompts/get` | 2243 | proxy config; mirrors `params.name`, but `params.uri` for `resources/read` |
| `MCP-Protocol-Version` header required, must match body `_meta` | 2575 | client request construction |
| Header/body mismatch is HTTP 400 + **-32020** | 2243 | error handling |
| Error codes renumbered from the betas: -32001→-32020, -32003→-32021, -32004→-32022 | | anything built against a beta |
| Resource-not-found: -32002 → **-32602** | 2164 | `rg '\-32002'` |
| `resultType` now required on every result | 2322 | result construction |
| `ping`, `logging/setLevel`, `resources/subscribe`, `resources/unsubscribe`, `notifications/roots/list_changed` **removed** | 2575, 2577 | `rg 'setLevel|resources/subscribe|ping'` |
| `listChanged` notifications silently dropped on 2026-07-28 connections | 2575 | `rg 'list_changed|listChanged'` |
| `tools/list`, `prompts/list`, `resources/list`, `resources/read`, `resources/templates/list`, `server/discover` must carry `ttlMs` + `cacheScope` | 2549 | list handlers |
| Servers **MUST** implement `server/discover` | 2567 | new endpoint required |
| Capabilities asserted per request, never inferred; missing capability is -32021 | 2575, 2133 | capability checks |
| **Python:** `mcp.server.fastmcp` deleted, no shim; `FastMCP` → `MCPServer` | | `rg 'FastMCP|mcp\.server\.fastmcp'` |
| **Python:** protocol fields snake_case for attribute access; **wire stays camelCase** | | `rg 'model_dump\('` |
| **Python:** `httpx` replaced by `httpx2`; `httpx` not installed at all | | `rg '^import httpx|from httpx'` |
| **TypeScript:** `.tool()` / `.prompt()` / `.resource()` removed | | `rg '\.tool\(|\.prompt\(|\.resource\('` |
| **TypeScript:** transports renamed and relocated; SSE and WebSocket transports removed | | `rg 'StreamableHTTPServerTransport|SSEServerTransport'` |

### Python-specific traps worth calling out

- `model_dump()` now emits **snake_case keys that no conforming peer accepts**. Pass
  `by_alias=True` anywhere you hand-build or serialise a payload.
- `ctx.session_id` **does not exist**. Accessing it raises `AttributeError`. On
  legacy stateful HTTP, read the `mcp-session-id` request header via `ctx.headers`.
- `MCPServer.get_context()` is removed. Request context only arrives via a
  `ctx: Context` parameter.
- `ServerSession` is now a **per-message proxy**, not a per-connection object. Any
  state keyed on `ctx.session` identity breaks silently.
- Sync (`def`) handlers now run on a worker thread instead of inline on the event
  loop. Shared mutable state that was previously safe by virtue of the single
  threaded loop is no longer safe.
- `MCPError` raised from a tool handler now surfaces as a **top-level JSON-RPC
  error** rather than `CallToolResult(is_error=True)`. Clients raise instead of
  returning a result.
- Deprecation warnings use `MCPDeprecationWarning`, a `UserWarning` subclass, so
  they show **in production by default** and are fatal under
  `filterwarnings = ["error"]`.
- `requestState` sealing uses an **ephemeral process-local AES-256-GCM key by
  default**, which silently breaks any multi-instance deployment. Configure a shared
  key before running more than one replica.

## Deprecation register

| Feature | Status | Earliest removal |
|---|---|---|
| Roots (SEP-2577) | Deprecated | 2027-07-28 or later |
| Sampling (SEP-2577) | Deprecated | 2027-07-28 or later |
| Logging (SEP-2577) | Deprecated, and `logging/setLevel` **already removed** | 2027-07-28 or later |
| Dynamic Client Registration (PR #2858, no SEP) | Deprecated in favour of CIMD | 2027-07-28 or later |
| **Legacy HTTP+SSE transport (2024-11-05)** | Deprecated | **three months after SEP-2596 reaches Final** |

The HTTP+SSE transport is the **shortest fuse in the entire release** and the one
most likely to be mis-scheduled: it is three months, not twelve. Confirm the
SEP-2596 Final date and work backwards from it. Repos affected are listed in
[INVENTORY.md](INVENTORY.md).

Note the awkward shape of the logging deprecation: the feature is deprecated but its
control surface was removed in the same revision, so the thing you are left with is
not the thing you wrote against. Logging is now a per-request `_meta` opt-in keyed on
`io.modelcontextprotocol/logLevel`; absence means silence.

## Migrate now, pin, or rewrite

**Pin and defer** if the server is tools-only over stdio, has no session state, and
is not in the HTTP+SSE set. Bound the major, revisit next quarter. This is most of
the estate and it is a legitimate answer.

**Migrate now** if any of: it is a gateway or proxy (it fronts everything else); it
carries `Mcp-Session-Id`; it uses the legacy HTTP+SSE transport (short fuse); or its
Python pin is unbounded (already breaking).

**Rewrite rather than migrate** if the server leans on the server to client request
channel for its core loop. MRTR is not a drop-in for push sampling or elicitation:
control inverts, and the client now drives the retry. A server built around "ask the
host mid-call" needs redesign, not porting. Budget accordingly.

**Blocked, do not start yet:**

- **Tasks extension (SEP-2663)**: implemented in *neither* the Python nor the
  TypeScript SDK. TypeScript still ships the 2025-11-25 core task types. If your
  long-running work depends on Tasks, you cannot migrate it yet.
- **MCP Apps**: `@modelcontextprotocol/ext-apps@1.7.5` peer-depends on
  `@modelcontextprotocol/sdk ^1.29.0`, the v1 monolith. There is no v2-compatible
  MCP Apps package on npm. Python ships Apps in-core as `mcp.server.apps.Apps`; only
  TypeScript is blocked.

## Triage

Run the script rather than eyeballing manifests. It classifies archetype, scores
risk, and names the runbook:

```bash
docs/mcp-2026-07-28/scripts/mcp-v2-triage.sh --all ~/code
docs/mcp-2026-07-28/scripts/mcp-v2-triage.sh --all ~/code --tsv > mcp-impact.tsv
```

| Archetype | Runbook |
|---|---|
| Tools-only over stdio | [01-tools-only-stdio.md](runbooks/01-tools-only-stdio.md) |
| Remote HTTP with OAuth, session-bearing | [02-remote-http-oauth.md](runbooks/02-remote-http-oauth.md) |
| Resources, prompts, subscriptions, Apps | [03-resources-prompts-and-apps.md](runbooks/03-resources-prompts-and-apps.md) |
| Code execution, sampling/roots dependent, skill factories, gateways | [04-code-execution-sampling-and-skill-factories.md](runbooks/04-code-execution-sampling-and-skill-factories.md) |

One caveat on the detection signals: a hit for `mcp-session-id` is **not** proof of
dead code. The shipping SDKs still actively mint and read that header on their
legacy transport paths (Go v1.7.0 writes it at `mcp/streamable.go:1661`). Triage each
hit as legacy-compat versus modern-path rather than deleting on sight. The same
applies to `EventStore` and `resumptionToken`, both still exported by
`@modelcontextprotocol/server@2.0.0`: delete them only if you are also dropping
2025-era backward compatibility.

## Sequencing

1. **Bound every unbounded Python pin.** Hours. Stops active breakage.
2. **Gateways and proxies.** They front everything else and mask failures behind them.
3. **HTTP+SSE transports.** Short fuse, three months after SEP-2596 Final.
4. **Session-bearing HTTP servers**, including their reverse-proxy config.
5. **Everything else**, scheduled deliberately.

Run a dual-stack period wherever you can. Era detection is deterministic: over HTTP
inspect the body of a 400; over stdio probe with `server/discover`. Do **not** key
the fallback to a specific error code, and treat the era as a property of the server,
not of an individual request.

## Testing and rollback

Verify the wire, not the lockfile. The package version tells you nothing about which
protocol revision is in play, which is the whole point of trap 2.

- Assert `MCP-Protocol-Version: 2026-07-28` on an actual request.
- Assert `Mcp-Method` and `Mcp-Name` are present and survive your proxy.
- Assert a header/body mismatch returns HTTP 400 with **-32020**.
- Assert an unknown method returns HTTP 404 with -32601 (this is the deliberate
  discriminator from a legacy HTTP+SSE 404).
- Assert `ttlMs` and `cacheScope` are present on all six cacheable result types, and
  that `cacheScope` is `private` for anything per-tenant.
- **Python:** assert serialised payloads are camelCase (`by_alias=True`).
- **TypeScript:** `npm ls zod` shows 4.2+ with no nested zod 3.

Rollback triggers: any `-32020` in production traffic (your proxy is stripping
headers), any cross-tenant cache hit, any `NoBackChannelError` or
`AttributeError: session_id`.

## Confidence and known gaps

The adversarial verification pass ran on five of seven research dimensions. It
found and corrected real errors in every one, including four in the project's own
starting assumptions, so the pass was doing useful work.

**Two dimensions were NOT adversarially verified**, because the workflow hit a spend
limit before their refuters ran:

- **`authorization`** (30 findings): CIMD, RFC 9207, `application_type`, issuer
  binding, EMA, step-up authorization.
- **`typescript-sdk`** (19 findings): the package split, `registerTool`, zod 4,
  transport renames, codemod behaviour.

Findings in those two areas are single-sourced. They were gathered under the same
primary-source rules and several were self-flagged as corrections to the brief,
which is a good sign, but they have not been independently challenged. **Confirm
anything load-bearing from those two areas against the SDK source before acting on
it.** The zod 4 and package-split claims in particular deserve a five-minute check
against `package.json`, because a large amount of downstream work keys off them.

The five runbook documents were also not generated: the same spend limit killed all
five writer agents, and a bug in the workflow script passed them an undefined output
path. They are being written by hand from this evidence base.
