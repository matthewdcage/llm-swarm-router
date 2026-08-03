# Runbook 02: remote HTTP server with OAuth

**Who this is for:** a hosted MCP server over Streamable HTTP or the legacy HTTP+SSE
transport, holding `Mcp-Session-Id`, protected by OAuth, usually behind Caddy, nginx
or a cloud ingress. In this estate: `wineexperience-xero-mcp-http-server`,
`wine-experience-xero-mcp-py`, `mcp-microsoft-365`, `mcp-pbs-server`,
`mcp-scriptstream`, `BOP-ZDispense-Ai-Server`, `mcp-wine-experience-portal`,
`ai-advantage-apps-hub`.

**When to use it:** the triage script reported `remote HTTP server`, or the repo
matched `Mcp-Session-Id`.

**This is the highest-risk archetype.** Everything that changed in 2026-07-28 lands
here at once: the session is gone, three new headers are mandatory, the transport is
POST-only, the GET stream endpoint is removed, and the OAuth client-registration
story changed. The infrastructure in front of the server is in scope, not just the
code.

**Expected effort:** two to five days per repo, plus a dual-stack period.

> Read [GUIDELINES.md](../GUIDELINES.md) first. The authorization content below comes
> from a dimension whose adversarial verification did not run: **confirm every OAuth
> claim against the SDK source before acting on it.** The transport and caching
> content was verified.

## Step 0: check your fuse length

Two different clocks apply, and mixing them up is the most likely scheduling error.

| What you have | Clock |
|---|---|
| Legacy **HTTP+SSE** transport (2024-11-05) | **Three months after SEP-2596 reaches Final** |
| Sessions, DCR, roots, sampling, logging | 12 months, so 2027-07-28 at the earliest |

If you are on HTTP+SSE you have the shortest deadline in the entire release.
Confirm the SEP-2596 Final date and schedule backwards from it. Do not assume twelve
months.

```bash
rg -n 'SSEServerTransport|sse_app|/sse|text/event-stream' .
```

## Step 1: inventory the session surface

```bash
rg -n -i 'mcp-session-id|sessionId|session_id' .
rg -n 'Last-Event-ID|resumptionToken|EventStore' .
rg -n 'app\.get\(.*/mcp|GET.*\/mcp' .          # the removed GET stream endpoint
```

Triage each hit into one of three buckets:

1. **Your code minting or reading the session.** Must be removed from the modern
   path.
2. **SDK legacy-compat code.** Leave it. The shipping SDKs still actively mint and
   read `Mcp-Session-Id` on their legacy transports (Go v1.7.0 writes it at
   `mcp/streamable.go:1661`, TypeScript still exports `EventStore`). It simply never
   engages on a 2026-07-28 request.
3. **Proxy or ingress config.** Step 4.

Do not delete bucket 2 unless you are also dropping 2025-era backward compatibility.

## Step 2: re-derive per-request identity

The session was almost certainly where you cached "who is this caller". It is gone.
Identity now arrives on every single request, from two places:

- **The `Authorization` bearer token.** This is your trust anchor. Authorization is
  now required on **every** HTTP request, because there is no session to have
  authorised earlier.
- **`_meta`, for protocol facts only.** Required keys are
  `io.modelcontextprotocol/protocolVersion` and
  `io.modelcontextprotocol/clientCapabilities`. `clientInfo` is **optional**.

**Treat `_meta` as untrusted client input.** It is not authenticated. Never make an
authorization decision from `_meta.clientInfo`. Derive the principal from the
validated token, every time.

```python
# BEFORE: identity resolved once, cached against the session
_sessions: dict[str, Principal] = {}

async def handle(request):
    sid = request.headers["mcp-session-id"]
    principal = _sessions[sid]              # gone: there is no session id
```

```python
# AFTER: identity resolved per request from the bearer token
async def resolve_principal(ctx: Context) -> Principal:
    token = ctx.headers.get("authorization", "").removeprefix("Bearer ").strip()
    claims = verify_token(token)            # signature, expiry, and AUDIENCE
    return Principal(sub=claims["sub"], tenant=claims["tenant"])
```

Note `ctx.session_id` **does not exist** on the v2 Python `Context`. Accessing it
raises `AttributeError`, it is not `None`. On a legacy stateful HTTP connection you
can still read the `mcp-session-id` request header via `ctx.headers`.

Two obligations that did not change but now matter far more: you **MUST** validate
the token audience, and you **MUST NOT** pass the token through to upstream APIs.
With no session to amortise it, both run on every request, so make the verification
path fast (cache the JWKS, not the decision).

## Step 3: the three required headers

Every Streamable HTTP POST must now carry:

| Header | Required on | Value |
|---|---|---|
| `MCP-Protocol-Version` | every request | must equal the body's `_meta` protocol version |
| `Mcp-Method` | every request | exactly the JSON-RPC `method` string |
| `Mcp-Name` | `tools/call`, `resources/read`, `prompts/get` | mirrors `params.name`, **except `resources/read` where it mirrors `params.uri`** |

The point of these is that gateways and WAFs can route and authorise without parsing
the JSON body.

Server obligations when validating:

- A missing required header, a header that disagrees with the body, or a header with
  invalid characters is **HTTP 400 with JSON-RPC `-32020`** (HeaderMismatch).
- An unsupported protocol version is `-32022`.
- An unknown method is **HTTP 404 with `-32601`**, deliberately distinct from the
  400s so clients can tell a modern 404 from a legacy HTTP+SSE 404.

Two traps:

- `-32020` was **`-32001` in the pre-release drafts**, along with `-32003`→`-32021`
  and `-32004`→`-32022`. Anything built against a beta has the wrong constants.
- There is **no way to import `-32020` from the TypeScript v2 SDK.**
  `HEADER_MISMATCH_ERROR_CODE` exists only as an internal bundled constant and is
  absent from every public entry point, and the `ProtocolErrorCode` enum has no
  `HeaderMismatch` member. Hardcode the literal or mirror it from `mcp-types`.

Header values that are not plain safe ASCII use an exact Base64 sentinel:
`=?base64?<payload>?=`. The prefix and suffix are case-sensitive and must appear
exactly as shown in lowercase. Clients must also encode any plain-ASCII value that
happens to look like the sentinel. Servers must decode before comparing, and
**should** compare numerics numerically rather than as strings.

## Step 4: the infrastructure in front of the server

This step is why the archetype is expensive. Code changes alone will not make it
work.

**Header pass-through.** Any proxy, WAF, ingress or CDN that strips unknown headers
will now break every request. Explicitly allow `MCP-Protocol-Version`, `Mcp-Method`,
`Mcp-Name` and `Mcp-Param-*`.

**POST-only.** The standalone GET stream endpoint is removed. Route rules, health
checks and CDN cache rules that assumed `GET /mcp` need updating.

**SSE buffering.** Streaming responses require buffering to be disabled. The spec
names `X-Accel-Buffering: no` explicitly. For Caddy:

```caddyfile
mcp.example.com {
  reverse_proxy localhost:8080 {
    flush_interval -1                     # disable response buffering for SSE
    header_up MCP-Protocol-Version {http.request.header.MCP-Protocol-Version}
    header_up Mcp-Method {http.request.header.Mcp-Method}
    header_up Mcp-Name {http.request.header.Mcp-Name}
  }
}
```

**Body rewriting breaks era detection.** Backward-compatibility probing works by
inspecting the *body* of a 400 or 404. Any intermediary that rewrites error bodies
(a friendly-error page, an API gateway's standard error envelope) will break a
client's ability to detect which era your server speaks. Exempt the MCP path.

**Header size.** The spec deliberately sets no limit, so oversized headers surface
as HTTP 413 or 431 from your infrastructure rather than as a protocol error. If you
use `x-mcp-header` to mirror tool arguments into `Mcp-Param-*` headers, check your
proxy's header size cap.

**Intermediaries MUST NOT trust the mirrored headers for security decisions.** They
are a routing convenience, not an authorisation input, and a client can set them to
anything. They should also reject requests whose `MCP-Protocol-Version` does not
indicate a header-validating revision.

## Step 5: horizontal scaling

The good news: the modern leg needs **no session affinity at all**. Drop sticky
sessions, drop the shared session store, and let a plain round-robin balancer send
any request to any instance.

Exactly two things still cross nodes:

1. **`requestState` sealing keys** (see step 6). Every instance must be able to
   unseal state sealed by any other.
2. **The subscription and notification bus**, if you use `subscriptions/listen`.

Everything else genuinely becomes stateless.

## Step 6: `requestState` and MRTR

If your server ever needed something from the client mid-call (a confirmation, a
missing parameter, an LLM completion) that was a server-initiated request. Those are
gone. The replacement is Multi Round-Trip Requests: you return
`resultType: "input_required"` and the **client** re-sends the original request with
`inputResponses` plus your `requestState`, on a new JSON-RPC id.

`requestState` is opaque to the client and **fully attacker-controlled**. If it
influences authorization, resource access or business logic you **MUST**
integrity-protect it (HMAC or AEAD) and **SHOULD** bind principal, TTL and the
originating request identity to prevent replay.

**Python deployment trap:** `MCPServer` installs an **ephemeral process-local
AES-256-GCM key by default**. That works on one instance and silently breaks the
moment you run two, because instance B cannot unseal what instance A sealed, and the
failure looks like a client bug rather than a config error. Configure a shared key
via `mcp.server.request_state` (`RequestStateSecurity` / `RequestStateBoundary`)
before scaling past one replica.

## Step 7: OAuth changes

> Single-sourced. Verify against SDK source before implementing.

**DCR is deprecated, not removed.** It keeps working for at least 12 months. The
replacement is Client ID Metadata Documents, which is not new in this revision (it
landed in 2025-11-25 under SEP-991). Registration selection is a normative priority
order: pre-registration, then CIMD, then DCR, then prompt the user.

Under CIMD an HTTPS URL with a path component *is* the `client_id`, and the JSON it
serves must echo that exact URL in its own `client_id` field. The practical payoff is
portability: CIMD client IDs work across authorization servers, DCR credentials do
not.

**Neither the official Python nor the TypeScript SDK implements the
authorization-server side of CIMD.** That is a job for your IdP, not your MCP server
library. If your server acts as its own AS, note that TypeScript v2 moved the AS
helpers into a frozen, deprecated `@modelcontextprotocol/server-legacy/auth`. That is
a hard migration.

**Three hardening changes to implement:**

- **RFC 9207 (SEP-2468):** the AS should return `iss` on every authorization
  response, and advertise `authorization_response_iss_parameter_supported: true`.
  Clients must record the expected issuer *before* redirecting, alongside the PKCE
  verifier, and validate on return. Python's `callback_handler` must now return
  `AuthorizationCodeResult` (code/state/iss), not `tuple[str, str | None]`. Also
  beware: a trailing-slash mismatch between the PRM `authorization_servers` entry and
  the AS's own `issuer` now breaks the whole flow with no client-side override.
- **`application_type` (SEP-837):** clients must send it during DCR. Omitting it
  defaults to `"web"` under OIDC, which **rejects the localhost redirects that
  desktop and CLI clients need**. All three SDKs auto-derive it from redirect URIs,
  with different defaults and different failure modes, so do not assume they agree.
- **Issuer binding (SEP-2352):** credentials are bound to the issuer that minted
  them. Key persisted credentials by issuer, never reuse across authorization
  servers, and re-register when the AS changes. The stamp is an SDK-local `issuer`
  field, deliberately not a wire field.

**What breaks if you do nothing:** existing DCR registrations keep working for the
deprecation window, so nothing breaks immediately. The risk is that new desktop and
CLI clients fail to register (the `application_type` localhost problem) and that you
run out of runway on a 12 month clock you did not start.

**Do not lock out registered clients.** Keep DCR serving throughout the dual-stack
period. Migrate to CIMD additively: accept both, prefer CIMD, retire DCR only once
telemetry shows no client using it.

## Step 8: dual-stack rollout

1. Deploy a v2 server on a **new hostname or path**, leaving the existing one alone.
2. Point one low-value client at it. Watch for `-32020` (your proxy is stripping
   headers) and 404/`-32601` (method routing).
3. Move remaining clients in order of blast radius.
4. Keep the legacy endpoint serving until telemetry shows zero traffic.
5. Retire.

**Rollback triggers, any one of which should stop the rollout:**

- Any `-32020` in production traffic: headers are not surviving the proxy.
- Any `AttributeError: session_id` or `NoBackChannelError`: incomplete port.
- Any cross-tenant cache hit: `cacheScope` is wrong (see runbook 03).
- MRTR retries failing on a subset of instances: unshared `requestState` key.

## Verification checklist

- [ ] `MCP-Protocol-Version: 2026-07-28` present and echoed correctly
- [ ] `Mcp-Method` and `Mcp-Name` survive the proxy end to end
- [ ] Header/body mismatch returns HTTP 400 + `-32020`
- [ ] Unknown method returns HTTP 404 + `-32601`
- [ ] Unsupported version returns `-32022`
- [ ] Authorization validated on **every** request, audience checked
- [ ] No token pass-through to upstream APIs
- [ ] Sticky sessions removed from the load balancer
- [ ] `requestState` key shared across all replicas
- [ ] SSE responses are not buffered by the proxy
- [ ] Error bodies are not rewritten by any intermediary
- [ ] Legacy endpoint still serving for the dual-stack window
