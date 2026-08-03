# Runbook 04: code execution, sampling/roots dependents, skill factories and gateways

**Who this is for:** the four archetypes hit hardest by this release. In this estate:
`mcp-ai-tool-gateway`, `bop-chat` (`zdispense-mcp-code-mode/`), `mcp-microsoft-365`
(`server_code_mode_http.py`), `factory-suite` (`packages/mcp-factory/`),
`ai-advantage-apps-hub`, `minerva-gateway`, `mcp-ai-tools-context-gateway`.

**When to use it:** the triage script reported `gateway/proxy` or
`sampling/roots dependent`, or the repo generates tools at runtime.

**Read this before scoping any of the above.** For part (b) in particular, the honest
answer is often "redesign", not "port". Do not commit to a migration estimate for a
sampling-dependent server before reading it.

> Read [GUIDELINES.md](../GUIDELINES.md) first.

---

## Part (a): code-execution layers

Servers that expose a sandboxed exec/eval tool, or run generated code.

### Long-running work moves to the Tasks extension, which is not shipped yet

Tasks moved **out of the core protocol** into the `io.modelcontextprotocol/tasks`
extension (SEP-2663). The surface changed: `tasks/result` and `tasks/list` were
**removed**; `tasks/get` (polling) and `tasks/update` (client to server input) are
what remain, alongside `tasks/cancel`.

```
tasks/get    {taskId}                      poll for status
tasks/update {taskId, inputResponses}      answer an input_required task
tasks/cancel {taskId}
```

`CreateTaskResult` is flat (`Result & Task`, `resultType: "task"`) carrying
`taskId`, `status`, `statusMessage`, `createdAt`, `lastUpdatedAt`, `ttlMs` and
`pollIntervalMs`. Status notifications ride `subscriptions/listen` with a `taskIds`
filter, **not** an HTTP GET stream. Polling remains the default.

**Migration blocker: neither the Python SDK (`mcp` 2.0.0) nor the TypeScript SDK
implements SEP-2663 Tasks.** TypeScript removed the experimental task support
entirely and its remaining task wire types are the 2025-11-25 vocabulary. If your
long-running execution model depends on Tasks, **you cannot migrate this server
yet.** Pin to v1, track SDK support, and revisit.

### Progress reporting without bidirectional streams

The server to client request channel is gone, and servers **MUST NOT** send JSON-RPC
requests on any stream. Streaming progress out of a long-running exec is therefore no
longer a push.

Options, in order of preference:

1. **Tasks extension**, once your SDK ships it. The intended answer.
2. **Return quickly with a handle.** Mint your own job id, return it as ordinary tool
   output, and expose a `get_status(job_id)` tool the client polls. This works today
   on both SDKs with no extension support and is what most people should do now.
3. **`subscriptions/listen`**, if you need genuine push and control both ends.

Note the cancellation change: on Streamable HTTP, **closing the SSE response stream
IS the cancellation**. `notifications/cancelled` is stdio-only. If your executor
relies on a cancellation notification over HTTP, it will never arrive; watch for
client disconnect instead.

### Security: `_meta` is untrusted input

This is the most important sentence in this runbook. With the handshake gone,
per-request identity arrives in `_meta`, and **`_meta` is unauthenticated client
input**.

`io.modelcontextprotocol/clientInfo` is optional and attacker-controlled. It is a
label, not a credential. For a server that executes code, treating it as identity is
a sandbox escape:

```python
# CATASTROPHIC on a code-execution server
client = ctx.request_meta.get("io.modelcontextprotocol/clientInfo", {})
if client.get("name") == "trusted-internal-agent":
    sandbox_policy = PERMISSIVE          # any caller can claim this
```

Derive every authorization decision from the validated bearer token. Use `_meta` for
protocol facts only (`protocolVersion`, `clientCapabilities`).

The same applies doubly to `requestState`: it is opaque to the client but fully
attacker-controlled on the way back. If it influences what code runs, with what
privileges, or against which tenant's data, it **MUST** be integrity-protected with
HMAC or AEAD, and **SHOULD** bind principal, TTL and originating-request identity to
prevent replay. On Python, remember the default sealing key is ephemeral and
process-local, which breaks silently across replicas (see runbook 02, step 6).

---

## Part (b): servers that depend on sampling or roots

### The situation

Sampling and roots are **deprecated** (SEP-2577), with a 12 month floor, so nothing
breaks tomorrow. But the transport underneath them changed in the same revision, so
they are simultaneously deprecated *and* re-plumbed. That combination is why porting
is often the wrong call.

**On a 2026-07-28 connection there are no server-initiated requests at all.** In
Python, `ctx.elicit()`, `ctx.elicit_url()`, `ctx.session.create_message()` and
`ctx.session.list_roots()` all raise `NoBackChannelError`. In TypeScript,
`server.listRoots()`, `server.elicitInput()` and the sampling push API **throw at
runtime** on any 2026-07-28-era request. These are not warnings.

### What replaces them

Multi Round-Trip Requests. Instead of asking, you return
`resultType: "input_required"` with `inputRequests`, and the **client** re-sends the
original request carrying `inputResponses` plus your `requestState`, on a new
JSON-RPC id.

Constraints worth knowing before you design against it:

- `inputRequests` may only carry three request types: `ElicitRequest`,
  `CreateMessageRequest`, `ListRootsRequest`. Keys are server-assigned and unique.
- Only three client requests may receive an `InputRequiredResult`: `prompts/get`,
  `resources/read`, `tools/call`.
- At least one of `inputRequests` or `requestState` MUST be present.
- The client controls whether it retries at all, and how many times. The Python
  client caps at 10 rounds by default.

Python has an ergonomic path via dependency injection, which is genuinely pleasant:

```python
from typing import Annotated
from mcp.server.mcpserver.resolve import Resolve, Elicit, Sample

@mcp.tool()
async def summarise(
    doc_id: str,
    summary: Annotated[str, Resolve(lambda: Sample(messages=[...]))],
) -> str:
    return summary          # the SDK batches the request and resumes on retry
```

TypeScript uses an `inputRequired()` builder with `acceptedContent()` /
`inputResponse()` readers, and reads state from `ctx.mcpReq.inputResponses` and
`ctx.mcpReq.requestState<T>()`.

### Why this is often a redesign

**Control inverts.** In v1 your server drove the conversation: it blocked, asked,
and continued with the answer in hand. In v2 your handler *returns*, loses the
stack, and may be re-entered later, on a different instance, if the client chooses
to retry. Any logic that held state across the "ask" in local variables, an open
transaction, or a held lock does not survive.

Three honest options:

1. **Do nothing for now.** 12 month floor. If the server works and is not otherwise
   being touched, this is defensible.
2. **Invert control.** Restructure so the host drives the loop and your server
   exposes stateless steps. This is the aligned-with-the-protocol answer and usually
   the right one for agent frameworks.
3. **Take a model client directly.** If you were using sampling purely to get an LLM
   completion, stop routing it through MCP: call the provider API from your server
   with your own key. The spec's own migration guidance for sampling says exactly
   this. It is simpler, and it removes a deprecated dependency entirely.

Option 3 is underrated. A large share of sampling usage is "I need a completion", not
"I need the host's model with the host's context", and for that case MCP was never
buying you much.

**Estate note:** grep found sampling and roots only in *documentation* across this
estate (`bop-chat/mcp-docs/`, `mcp-taiga/MCP_SDK.md`), with no implementation call
sites. If that holds up locally, part (b) may cost you nothing at all. Confirm with
the triage script before budgeting for it.

---

## Part (c): skill factories and dynamic tool registration

Servers that generate tools at runtime, per tenant or per user.

### The cache leak

This is the failure that matters, and it is new.

`tools/list` now carries required `ttlMs` and `cacheScope`. A factory server's tool
list is, by construction, **not** the same for every caller. If it is emitted with
`cacheScope: "public"`, tenant B is served tenant A's tool list from cache. That is
both a correctness bug and an information disclosure: tool names and descriptions
routinely leak schema, customer names and internal structure.

```python
mcp = MCPServer(
    "factory",
    cache_hints={"tools/list": CacheHint(ttl_ms=30_000, scope="private")},
)
```

**Rule: if the tool list varies by anything at all, `cacheScope` is `private`.**

Then consider whether you want caching at all. A factory that regenerates tools
frequently should use a short `ttlMs` (or `0`, meaning immediately stale) so clients
re-fetch. `ttlMs` is a freshness hint, not a poll interval.

### `listChanged` no longer arrives

Dynamic registration historically leaned on `notifications/tools/list_changed` to
tell clients the tool set moved. **Those notifications are silently dropped on a
2026-07-28 connection.** They do not error; they go nowhere.

So the pattern "register a tool, then notify" is now "register a tool, and rely on
`ttlMs` expiry". Set `ttlMs` to the longest staleness you can tolerate. If you need
genuine push, publish on `subscriptions/listen`, remembering clients opt in per
notification type.

### Capabilities are asserted, never inferred

Servers **MUST NOT** infer capabilities from prior requests, and must return
`-32021 MissingRequiredClientCapability` when a request needs a capability the caller
did not declare on *that* request. A factory that remembered "this client supports X"
from an earlier call is now wrong. Read capabilities from each request's `_meta`.

---

## Part (d): gateways, routers and aggregators

Servers that proxy multiple upstream MCP servers. **Do these first**, because a
gateway failure masks and amplifies every failure behind it.

### You are hit from both sides

A gateway is both an MCP server (to its clients) and an MCP client (to its
upstreams), so every breaking change lands twice. `mcp-ai-tool-gateway` is the worked
example: `mcp>=1.2` unbounded, `FastMCP`, 18 `@mcp.tool()` decorators, and
`await s.initialize()` on both the proxy path and the self-test.

The `initialize()` calls are the interesting ones. The handshake is **removed**, so
every upstream connection in the proxy path needs rewriting:

```python
# BEFORE
async with stdio_client(params) as (r, w):
    async with ClientSession(r, w) as s:
        await s.initialize()               # gone in v2
        res = await s.call_tool(tool, args)
```

There is no `initialize()` to call. Identity and protocol version travel per request
in `_meta` instead.

### Mixed-version upstreams are the real work

During migration you will front both v1 and v2 servers simultaneously. The gateway
must detect each upstream's era and speak the right protocol to it.

Era detection is deterministic and the spec is specific about how:

- **Over HTTP:** inspect the **body** of a 400. Do not key the fallback to a specific
  error code.
- **Over stdio:** probe with `server/discover`.
- **The era is a property of the server, not of a request.** Detect once per
  upstream, cache it, do not re-probe per call.

Hazards worth budgeting for: auth failures and 5xx are **never** era evidence (a 401
tells you nothing about protocol version); a stdio probe spawns a throwaway child
process; and probing changes recorded transcripts, which will surprise anyone
diffing golden files.

Also note `server/discover` has **no `serverInfo` member**. Server identity moved
into the result's `_meta`. A gateway that built its registry from
`initialize().serverInfo` needs a new source.

### Header-based routing is now a first-class capability

This is the one genuine upside for gateways. `Mcp-Method` and `Mcp-Name` are required
headers carrying the method and target name, so you can route and rate-limit
**without parsing the JSON body**. For a gateway fronting dozens of upstreams that is
a real simplification, and it is why the headers exist.

Two rules:

- **MUST NOT trust the mirrored headers for security decisions.** They are a routing
  convenience. A client can set them to anything. Authorise from the token.
- **SHOULD reject requests whose `MCP-Protocol-Version` does not indicate a
  header-validating revision**, since otherwise the headers guarantee nothing.

If you validate headers against the body yourself, a mismatch is HTTP 400 with
`-32020`.

### Translating stateful upstreams to stateless downstreams

If an upstream still requires `Mcp-Session-Id`, the gateway now owns that session on
the upstream's behalf: mint and hold it per upstream, and never expose it downstream.
Your clients must see a clean stateless interface regardless of what you are talking
to behind it.

Keep that mapping in a shared store, not process memory, or you have reintroduced
session affinity at the gateway layer, which defeats the point.

### Aggregating cache hints

When merging `tools/list` from several upstreams, the merged result needs its own
`ttlMs` and `cacheScope`. Two rules:

- `ttlMs` = **the minimum** across contributors. The merged list is only as fresh as
  its stalest-tolerant member.
- `cacheScope` = `private` if **any** contributor is `private`. Scope does not
  average.

And remember all pages of one list response MUST share the same `cacheScope`, which
for an aggregating gateway means deciding scope before you paginate, not per page.

## Verification checklist

- [ ] No `.initialize()` remains on any modern-era path
- [ ] Era detection reads the 400 **body**, not a specific code, and caches per upstream
- [ ] Auth failures and 5xx are never treated as era evidence
- [ ] `_meta.clientInfo` is used for **nothing** security-relevant
- [ ] `requestState` is HMAC or AEAD protected if it affects execution or access
- [ ] `requestState` sealing key is shared across replicas
- [ ] `tools/list` from any factory or per-tenant server is `cacheScope: private`
- [ ] Merged gateway lists take min `ttlMs` and most-restrictive `cacheScope`
- [ ] No reliance on `listChanged` notifications arriving
- [ ] Capabilities read per request, never remembered
- [ ] Upstream session ids never leak downstream
- [ ] Tasks-dependent servers are pinned to v1 and tracked, not half-migrated
