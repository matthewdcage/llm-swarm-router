# Runbook 03: resources, prompts, subscriptions and MCP Apps

**Who this is for:** servers exposing `resources/*` or `prompts/*`, servers using
resource subscriptions, and servers that want UI via MCP Apps.

**When to use it:** the triage script reported `resources/prompts bearing`, or the
repo matches `registerResource`, `@mcp.resource`, `resources/subscribe`.

**The dominant risk here is not API churn, it is caching.** `ttlMs` and `cacheScope`
are now required fields, and choosing `cacheScope` wrongly is a cross-tenant data
leak, not a performance regression. Read the caching section even if you skip the
rest.

> Read [GUIDELINES.md](../GUIDELINES.md) first.

## Step 1: what was removed outright

These are **removed**, not deprecated. There is no 12 month window:

- `resources/subscribe`
- `resources/unsubscribe`
- `ping`
- `logging/setLevel`
- `notifications/roots/list_changed`

Additionally, **`listChanged` notifications are silently dropped** on a 2026-07-28
connection. `tools/list_changed`, `prompts/list_changed`, `resources/list_changed`
and `resources/updated` all go nowhere. They do not error; they simply do not
arrive.

```bash
rg -n 'resources/subscribe|resources/unsubscribe|list_changed|listChanged|setLevel|"ping"' .
```

If your server pushed `resources/updated` to tell clients a resource changed, that
mechanism is gone. The replacement is a combination of two things:

1. **Cache hints.** Set a short `ttlMs` on volatile resources so clients re-fetch.
2. **The subscription bus.** Publish on `subscriptions/listen` if you need genuine
   push. Note that clients opt in per notification type, so this is not a drop-in
   replacement for a broadcast.

For most resource servers, a well-chosen `ttlMs` replaces the notification entirely
and is far simpler.

## Step 2: caching, and the leak you must not ship

Six result types now carry **required** `ttlMs` and `cacheScope` fields:

- `tools/list`
- `prompts/list`
- `resources/list`
- `resources/read`
- `resources/templates/list`
- `server/discover`

(The launch blog omits the last two. They are required.)

### `ttlMs`

Integer milliseconds, must be `>= 0`. It is a **freshness hint, not a poll
interval**. `0` means immediately stale. The Python client clamps anything above
`MAX_TTL_MS` (24 hours) down to that ceiling.

### `cacheScope`

Exactly two allowed values:

| Value | Meaning |
|---|---|
| `public` | safe to share across **all** authorization contexts |
| `private` | scoped to **one authorization context** |

**`private` means per-authorization-context, not per-user-device.** Two requests
bearing the same token share a private cache entry. Two different tenants never do.

### The decision rule

Ask one question: *could two different principals receive different content from
this method?* If yes, it is `private`. If you are unsure, it is `private`.

The failure mode is not subtle. A `resources/read` that returns tenant A's invoice
with `cacheScope: "public"` will be served to tenant B from cache.

```python
from mcp.server import MCPServer
from mcp.server.caching import CacheHint

mcp = MCPServer(
    "portal",
    cache_hints={
        # static, identical for everyone
        "prompts/list":  CacheHint(ttl_ms=3_600_000, scope="public"),
        # per-tenant, MUST be private
        "resources/list": CacheHint(ttl_ms=60_000, scope="private"),
        "resources/read": CacheHint(ttl_ms=30_000, scope="private"),
    },
)
```

Server-level hints fill a field **only where the handler left it unset**, so a
handler can override per resource. In TypeScript the equivalent is `cacheHints` on
the handler factory, or `registerResource(..., { cacheHint })` per resource, with
the same field-by-field precedence. Invalid values throw a `RangeError` at
construction time rather than failing at runtime, which is the behaviour you want.

### Three normative rules that are easy to violate

1. **All pages of one list request MUST share the same `cacheScope`.** Each page is
   independently cacheable, but you cannot mix scopes across a paginated list. If
   page 1 is public and page 2 is private, you have a bug.
2. **MRTR retries MUST NOT be cached.** Any request carrying `inputResponses` or
   `requestState` is uncacheable. Do not attach cache hints to an
   `input_required` result: those results carry no caching hints at all.
3. **Caching complements `listChanged`, it does not replace it.** A received
   notification invalidates a still-fresh entry immediately.

### Cross-SDK hazard

If any part of your stack is Go, read the Go `cacheScope` trap in
[GUIDELINES.md](../GUIDELINES.md). Go v1.7.0 unconditionally stamps
`cacheScope: "public"` and **overwrites explicit `private` values** on the
`resources/read` path. A Go resource server cannot currently emit `private` through
the normal result path.

Correspondingly, never enable the Python client's `share_public` option against a Go
server. That option trusts the server's public label across every principal sharing
the cache store, and its own docstring warns that a mislabelled response leaks across
tenants. Combined with the Go defect, it is a direct cross-tenant exposure.

The Python client is safer than it first appears: `CacheConfig.__post_init__` refuses
a custom store without an explicit `partition`, and the default store is per-client
and in-memory. So omitting `partition` with the default store cannot leak. The
`partition` only matters when you supply a shared custom store, and the SDK enforces
it there at construction time.

## Step 3: registration API changes

### Python

Decorator names and signatures are unchanged: `@mcp.resource()`, `@mcp.prompt()`,
`@mcp.completion()` all keep their v1 forms. What changes around them:

- `Context` imports from `mcp.server.mcpserver`, not `mcp.server`.
- Resource `uri` fields changed type from pydantic `AnyUrl` to plain `str`, and v2
  **stops normalising URIs**. If you relied on `AnyUrl` normalising a trailing slash
  or lowercasing a host, that no longer happens and lookups may miss.
- Protocol types changed from `extra="allow"` to silently dropping unknown fields,
  while `Resource` subclasses went the other way to `extra="forbid"`. A `Resource`
  subclass carrying an extra attribute now raises at construction.
- `completable()` optional nesting **inverted** in TypeScript, and getting it wrong
  fails silently with empty completion lists.

```python
# BEFORE
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("portal")

@mcp.resource("invoice://{invoice_id}")
async def invoice(invoice_id: str) -> str:
    return await fetch(invoice_id)
```

```python
# AFTER
from mcp.server import MCPServer
from mcp.server.mcpserver import Context

mcp = MCPServer("portal")

@mcp.resource("invoice://{invoice_id}")       # decorator unchanged
async def invoice(invoice_id: str, ctx: Context = None) -> str:
    principal = resolve_principal(ctx)         # identity is per-request now
    return await fetch(invoice_id, tenant=principal.tenant)
```

### TypeScript

`.resource()` and `.prompt()` are **removed**, not deprecated, replaced by
`registerResource` and `registerPrompt` with a config object. The class is still
`McpServer`; only the import path moved.

```ts
// BEFORE
server.resource("invoice", "invoice://{id}", async (uri) => ({ contents: [...] }));

// AFTER
server.registerResource(
  "invoice",
  "invoice://{id}",
  { cacheHint: { ttlMs: 30_000, cacheScope: "private" } },
  async (uri, ctx) => ({ contents: [...] }),
);
```

Zod `*Schema` constants moved to `@modelcontextprotocol/core` and are **not**
re-exported by `/server` or `/client`, which keep a Zod-free public surface. Import
schemas from `core` directly.

One silent behaviour change worth a test: client-side `list*()` methods now
**auto-aggregate all pages**. Code that paginated manually will still work but may
now fetch far more than intended in a single call.

## Step 4: resource not found

The error code changed:

```python
# BEFORE
raise McpError(ErrorData(code=-32002, message="not found"))

# AFTER
raise MCPError(-32602, "not found", {"uri": uri})
```

`-32002` is permanently reserved and must never be emitted by a 2026-07-28
implementation. In TypeScript this is normalised at the encode seam, so a v2 server
**cannot** emit `-32002` even if you throw it explicitly. In Python you can still
construct it, so grep for it:

```bash
rg -n '\-32002' .
```

Put the offending URI in `error.data` as `{"uri": ...}`. That is the shape the spec
expects and clients will look for it.

## Step 5: MCP Apps

MCP Apps is the `io.modelcontextprotocol/ui` extension, negotiated with
`{"mimeTypes": ["text/html;profile=mcp-app"]}`. Tools point at a UI through
`_meta.ui.resourceUri` using the `ui://` scheme.

**Python:** ships in-core as `mcp.server.apps.Apps`, an `Extension` subclass, with a
`client_supports_apps(ctx)` helper for graceful degradation. Usable today.

**TypeScript: blocked.** `@modelcontextprotocol/ext-apps@1.7.5` peer-depends on
`@modelcontextprotocol/sdk ^1.29.0`, the v1 monolith. There is no v2-compatible MCP
Apps package published. If you need Apps in TypeScript you cannot migrate that
server to v2 yet. Track the package and defer.

Always degrade gracefully. Apps is an optional extension and clients advertise
support per request via capabilities. A tool that only works with UI attached will
fail against most clients.

## Verification checklist

- [ ] All six cacheable result types carry `ttlMs` and `cacheScope`
- [ ] Every per-tenant method is `private`, verified by a two-tenant test
- [ ] All pages of a paginated list share one `cacheScope`
- [ ] No cache hints attached to `input_required` results
- [ ] `rg '\-32002'` returns nothing
- [ ] Resource-not-found returns `-32602` with `{"uri": ...}` in `error.data`
- [ ] No reliance on `resources/subscribe` or `resources/updated`
- [ ] Resource URI lookups still resolve without `AnyUrl` normalisation
- [ ] `share_public` is **not** enabled if any upstream is a Go server
- [ ] MCP Apps degrades cleanly when the client does not advertise `ui`

### The two-tenant cache test

Worth writing once and keeping, because it catches the only failure in this runbook
that is a security incident rather than a bug:

```
1. Authenticate as tenant A. Call resources/read for a tenant-scoped URI.
2. Authenticate as tenant B. Call resources/read for the SAME URI.
3. Assert B receives B's content, never A's.
4. Repeat within the ttlMs window, which is when a wrong cacheScope actually bites.
```

Step 4 is the one people forget. A wrongly-public entry only leaks while it is still
fresh, so a test that runs after expiry passes for the wrong reason.
