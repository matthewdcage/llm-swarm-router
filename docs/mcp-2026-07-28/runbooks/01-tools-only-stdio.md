# Runbook 01: tools-only server over stdio

**Who this is for:** a server that registers N tools, wraps a third-party REST API,
holds credentials in a module-level client, and speaks stdio. No resources, no
prompts, no sessions, no OAuth. This is the shape of most of the estate:
`mcp-google-ads`, `mcp-zoho-books`, `mcp-se-ranking`, `mcp-campaign-monitor` and
similar.

**When to use it:** the triage script reported `tools-only stdio`.

**Expected effort:** half a day per repo for Python, one day for TypeScript. The
Python `mcp` to `MCPServer` rename is mechanical; the TypeScript package split and
zod 4 floor are where the time goes.

> Read [GUIDELINES.md](../GUIDELINES.md) first, particularly "The three traps".
> TypeScript details in this runbook come from a dimension whose adversarial
> verification did not run, so confirm signatures against the SDK before relying on
> them.

## Step 0: decide whether to migrate at all

For this archetype, deferring is a legitimate answer. stdio is the least affected
transport: framing is unchanged, and there is no header layer, no session, and no
proxy to reconfigure.

**Defer** if the pin is already bounded and the server does not use sampling, roots
or logging. Do step 1, then stop and revisit next quarter.

**Migrate now** only if the pin is unbounded, because then it is already breaking.

## Step 1: stop the bleeding (do this even if you defer)

An unbounded Python pin accepts `mcp` 2.0.0, which shipped 2026-07-28. Any clean
install, fresh CI runner or rebuilt container pulls v2 and the server stops working.

```bash
rg -n '"?(mcp|fastmcp)(\[[a-z,]+\])?\s*>=' pyproject.toml requirements*.txt 2>/dev/null
```

Bound the major:

```toml
# before, accepts 2.0.0 and breaks
dependencies = ["mcp>=1.6.0", "fastmcp>=2.13.0"]

# after
dependencies = ["mcp>=1.6.0,<2.0.0", "fastmcp>=2.13.0,<4.0.0"]
```

Note `fastmcp` (the third-party distribution) is at 3.4.5 stable, so a `>=2.x` pin
has *already* moved you to 3.x. Decide whether you want `<3.0.0` (pin back to the
2.x line you were tested against) or `<4.0.0` (accept the jump that already
happened). Prefer `<3.0.0` if the repo has not been touched recently.

TypeScript needs no equivalent action: caret ranges cannot cross a major and there
is no `@modelcontextprotocol/sdk@2` to drift into.

**Verification:**

```bash
rm -rf .venv && uv sync          # or: python -m venv .venv && pip install -e .
python -c "import mcp; print(mcp.__version__)"   # must be 1.x
```

Commit this on its own. It is a safe, isolated change and worth landing before
anything else.

## Step 2: pre-flight assessment

```bash
# tool registration sites, the bulk of the work
rg -c '@mcp\.tool|\.tool\(|registerTool' .

# things that are removed rather than changed
rg -n 'ping|logging/setLevel|resources/subscribe|list_changed|listChanged' .

# deprecated features (12 month window, but check now)
rg -n 'createMessage|create_message|listRoots|list_roots|ctx\.(info|debug|warning|error)' .

# module-level credential/client caches, see step 4
rg -n '^[A-Z_]+ *= *|^_?client *= *|@lru_cache|functools\.cache' --type py .
```

Record the counts. A server with 20 tools, no removed features and no module state
is a mechanical port. One that logs through `ctx.info()` on every call has a
behaviour change to think about (step 5).

## Step 3: the dependency bump

### Python

```toml
dependencies = [
  "mcp>=2.0.0,<3.0.0",
]
```

This pulls `mcp-types` 2.0.0, `httpx2>=2.5.0` and `pydantic>=2.12`. Two consequences:

- **`httpx` is no longer installed at all.** If your API client imports `httpx`
  directly (very likely for a REST wrapper) you must either add `httpx` as your own
  explicit dependency or port to `httpx2`. Do not assume it is still there
  transitively.
- **`pydantic>=2.12`** drops the previous `<3` cap. Check your own models still
  validate.

```bash
rg -n '^\s*(import httpx|from httpx)' --type py .
```

### TypeScript

```bash
npm uninstall @modelcontextprotocol/sdk
npm install @modelcontextprotocol/server@2 @modelcontextprotocol/core@2 zod@^4.2.0
npx @modelcontextprotocol/codemod@2 v1-to-v2 .
```

The codemod runs nine ordered transforms. It does **not** touch deprecated features,
error codes, or anything protocol-level. Treat its output as a starting point, not a
finished migration.

**Verification, and do not skip this one:**

```bash
npm ls zod        # 4.2.0+, and no nested zod 3 anywhere in the tree
```

zod 3 installs and compiles cleanly, then fails silently at runtime. This is the
single most expensive trap in the migration.

## Step 4: module-level credentials and caches

Sessions are gone, but for a stdio tools-only server this matters less than the
headline suggests: one process still serves one client. The real change is that
**you may no longer assume a process-lifetime identity**.

Safe to keep: a module-level HTTP client, connection pool, or config object derived
from environment variables. These were never session state.

Must change: anything keyed on a session or client identity, and anything that
assumed the single-threaded event loop.

```python
# BEFORE: safe in v1 because sync handlers ran inline on the loop
_token_cache: dict[str, str] = {}

@mcp.tool()
def fetch(account: str) -> str:
    if account not in _token_cache:          # read-modify-write, now racy
        _token_cache[account] = _mint_token(account)
    return _call_api(_token_cache[account])
```

In v2, **sync (`def`) handlers run on an anyio worker thread**, not inline on the
event loop. A dict mutation that was previously safe by virtue of the single
threaded loop is no longer safe.

```python
# AFTER: explicit lock, or make the handler async
import threading
_token_lock = threading.Lock()
_token_cache: dict[str, str] = {}

@mcp.tool()
def fetch(account: str) -> str:
    with _token_lock:
        token = _token_cache.get(account)
        if token is None:
            token = _token_cache[account] = _mint_token(account)
    return _call_api(token)
```

Also delete any use of `ctx.session` as a dictionary key: `ServerSession` is now a
**per-message proxy**, not a per-connection object, so its identity changes between
messages and the entry never hits.

## Step 5: the worked port

### Python before (mcp 1.x)

```python
from mcp.server.fastmcp import FastMCP, Context

mcp = FastMCP("zoho-books")

@mcp.tool()
async def list_invoices(status: str = "unpaid", ctx: Context = None) -> str:
    await ctx.info(f"listing {status} invoices")
    resp = await _client.get("/invoices", params={"status": status})
    if resp.status_code == 404:
        raise McpError(ErrorData(code=-32002, message="no such view"))
    return resp.text

if __name__ == "__main__":
    mcp.run()
```

### Python after (mcp 2.0.0)

```python
from mcp.server import MCPServer
from mcp.server.mcpserver import Context      # Context is NOT importable from mcp.server
from mcp.shared.exceptions import MCPError

mcp = MCPServer("zoho-books")

@mcp.tool()                                    # decorator name and signature unchanged
async def list_invoices(status: str = "unpaid", ctx: Context = None) -> str:
    await ctx.info(f"listing {status} invoices")   # see note below, may be dropped
    resp = await _client.get("/invoices", params={"status": status})
    if resp.status_code == 404:
        raise MCPError(-32602, "no such view", {"view": status})   # was -32002
    return resp.text

if __name__ == "__main__":
    mcp.run()                                  # stdio, unchanged
```

Four things changed and three did not. Changed: the import path and class name, the
`Context` import location, the `MCPError` constructor (positional `code, message,
data`, no `ErrorData` wrapper), and the error code. Unchanged: the decorator, the
handler signature, and `mcp.run()`.

**On `ctx.info()`:** logging is deprecated and is now a per-request opt-in. On a
2026-07-28 connection the message is **dropped silently** unless the request's
`_meta` carries `io.modelcontextprotocol/logLevel`. If those log lines are how you
debug production, move them to your own logger (`logging`, structlog) rather than the
MCP channel. Also note the signature changed: v1's `**extra` passthrough is gone, so
`await ctx.info("msg", account=x)` now raises `TypeError`.

**On `MCPError`:** raising it from a tool handler now surfaces as a top-level
JSON-RPC error rather than `CallToolResult(is_error=True)`. Clients raise instead of
receiving a result. If a caller relied on inspecting `is_error`, that path is gone.

### TypeScript before

```ts
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";                       // zod 3

const server = new McpServer({ name: "se-ranking", version: "1.0.0" });

server.tool("keywords", { domain: z.string() }, async ({ domain }) => ({
  content: [{ type: "text", text: await fetchKeywords(domain) }],
}));

await server.connect(new StdioServerTransport());
```

### TypeScript after

```ts
import { McpServer } from "@modelcontextprotocol/server";   // class name unchanged
import { serveStdio } from "@modelcontextprotocol/server/stdio";
import { z } from "zod";                                    // must be ^4.2.0

const server = new McpServer({ name: "se-ranking", version: "1.0.0" });

server.registerTool(                            // .tool() is REMOVED, not deprecated
  "keywords",
  { inputSchema: { domain: z.string() } },
  async ({ domain }, ctx) => ({                 // second param is now ctx, not extra
    content: [{ type: "text", text: await fetchKeywords(domain) }],
  }),
);

await serveStdio(() => server);
```

Note the class is still `McpServer`: only the import path moved. `.tool()` is
removed outright rather than deprecated, so this will not compile until changed,
which is the good case. The handler's second parameter changed from a flat
`RequestHandlerExtra` to a structured `ServerContext` with `mcpReq` and `http`
sub-objects.

## Step 6: cacheable tool lists

`tools/list` now carries required `ttlMs` and `cacheScope` fields. For a tools-only
server whose tool set is static and identical for every caller, this is free
performance:

```python
from mcp.server import MCPServer
from mcp.server.caching import CacheHint

mcp = MCPServer(
    "zoho-books",
    cache_hints={"tools/list": CacheHint(ttl_ms=3_600_000, scope="public")},
)
```

**`cacheScope: "public"` is only correct if the tool list is genuinely identical for
every authorization context.** If tools vary per tenant, per API key, or per
permission level, it must be `"private"`, which means per-authorization-context, not
per-user-device. Getting this wrong leaks one tenant's tool list to another.

If in doubt, use `"private"`. The cost is a cache miss; the cost of the other
mistake is a disclosure incident.

## Step 7: verification

```bash
# Python: the server starts and lists tools
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}}}}' \
  | python server.py

# confirm ttlMs and cacheScope are present on the result
# confirm resultType is "complete"
```

Checklist:

- [ ] Fresh install resolves the intended major (`pip list | grep '^mcp '`)
- [ ] `rg 'FastMCP|mcp\.server\.fastmcp'` returns nothing (Python)
- [ ] `npm ls zod` shows 4.2+ with no nested zod 3 (TypeScript)
- [ ] `rg '\-32002'` returns nothing
- [ ] `tools/list` result carries `ttlMs` and `cacheScope`
- [ ] `cacheScope` is `private` if the tool list varies by caller
- [ ] Any `model_dump()` call passes `by_alias=True` (Python)
- [ ] Sync handlers mutating shared state are locked, or made async
- [ ] Tool count matches pre-migration

## Rollback

This archetype is the easiest to roll back: revert the dependency pin and the
import block. There is no persisted state, no proxy config and no client
registration to unwind. Keep the two commits (step 1 pin, steps 3 to 6 port)
separate so the port can be reverted without reintroducing the unbounded pin.
