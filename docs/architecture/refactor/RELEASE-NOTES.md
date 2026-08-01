# Release notes — the F-24 / F-25 / F-26 consolidation

**Audience: operators.** This is the complete list of things that behave
differently after the routing consolidation. It is written for someone running
netllm, not for someone reading the diff — the code-level evidence for every
item is in [behavior-matrix.md](behavior-matrix.md), which numbers each one
`D<n>`. The IDs are kept here so you can jump between the two.

## What changed structurally

The router used to contain **five hand-copied failover loops** — one inside each
of `proxy_chat_completion`, `proxy_chat_completion_stream`, `proxy_embeddings`,
`proxy_messages` and `proxy_messages_stream`. They drifted, because every fix
landed on whichever copy the author was reading. There is now **one** loop
(`service/engine.py`), one place that writes accounting (`AttemptRecorder`), and
one model-name matcher (`ModelResolver`); everything that genuinely varies per
API surface is a small adapter object.

Nothing about your config file changes. No config key was renamed, removed, or
given a new default. The behaviour changes below are all in the request path,
and each one is pinned by a golden-vector contract test so it cannot silently
drift again.

---

## Changes you may notice as a client

### 1. `/v1/embeddings` now rejects non-embedding models with a 400 (D4)

**Before:** `/v1/embeddings` had no capability guard at all. Sending a chat
model there dispatched it to *every* backend in turn — each one failing with a
400 or 500 — until the whole retry budget was exhausted. The client waited for
all of that and then got a generic failure.

**After:** the request is rejected immediately with
`400 Model '<name>' (capability: chat) cannot serve embeddings. Use POST
/v1/chat/completions for chat models.` Zero upstream calls, zero wasted backend
capacity.

**⚠️ This is a tightening — read this if you serve embeddings.** The capability
classifier works from the **model name**, and it returns `"chat"` for anything
it does not recognise. It recognises `embed` plus the encoder-family tokens
`bge`, `gte`, `e5`, `minilm`, `bert`, `modernbert`, `colbert`, `splade`. An
embedding model whose served name contains none of those — say
`my-company-retriever-v2` — used to route fine and **now gets a 400**.

*Fix if this hits you:* either rename the served model, or add a
`[routing.model_aliases]` entry whose **request-side** name carries an embedding
token, e.g. `"embed-retriever" = ["my-company-retriever-v2"]`, and have clients
request `embed-retriever`.

### 2. `/v1/messages` forwards upstream 400 and 404 instead of flattening to 502 (D11)

**Before:** when a Messages request was served by a translated OpenAI-format
backend (the normal case on a local mesh), any upstream error was flattened to
`502 Bad Gateway`. A client's own malformed request, or a request for a model
the backend does not have, both read as *"the router is broken."*

**After:** upstream `400` and `404` are forwarded with their real status; every
other upstream status still becomes `502`. The response body is still
Anthropic-shaped, so Anthropic-dialect clients are unaffected structurally. This
matches what the OpenAI-dialect routes have always done.

**What you may notice:** dashboards and alerts that counted `502`s from
`/v1/messages` will see some of them reclassified as `400`/`404`. That is the
point — those were never gateway failures.

### 3. Streamed `/v1/messages` restores the model name you asked for, and closes its error frames (D9)

**Before, two problems on the streamed Messages path:**

- The `message_start` frame carried the **rewritten** model name — whatever the
  backend actually serves after aliases and `model_rewrites` — instead of the
  name the client sent. Every other path (chat, chat-stream, embeddings,
  non-streamed messages) restored the requested name. Clients that key state off
  `message_start.model`, or that display it, saw an internal name.
- A mid-stream failure emitted an `event: error` frame with **no terminator**.
  The OpenAI-dialect stream has always closed with `data: [DONE]`; the Anthropic
  arm just stopped, so a strict client could hang waiting for the end of the
  message.

**After:** `message_start` carries the requested name, and a mid-stream error is
followed by `event: message_stop`.

### 4. Streamed `/v1/responses` now records telemetry for successful requests (D16)

**Before:** a *fully successful* streamed Responses request recorded **nothing**
— no `routed_requests` entry, no `netllm_requests_total{status="ok"}`, no
latency sample, no token counters, no session/all-time request count. Failures
*were* recorded. So the Responses surface showed you errors and never successes:
the more reliably it worked, the more broken it looked.

The cause was structural: the Responses translator stops reading at
`data: [DONE]`, abandoning the underlying generator before the accounting code
after its loop could run.

**After:** the stream engine reads one chunk ahead of its consumer — it settles
the attempt's accounting *before* handing on the chunk the consumer stops at —
so a consumer that quits at its own terminator can no longer strand it. Streamed
Responses now records exactly what streamed chat records.

**What you may notice:** if you use Codex CLI (the reason `/v1/responses`
exists), your request counts, token totals and per-backend `routed_requests` will
jump from near-zero to accurate. This is not new traffic; it was always there and
was never counted.

### 5. `batch_shard` chat failover retries further than it used to (D17)

This one changes **routing outcomes**, so it gets the most detail.

`batch_shard` assigns a request to a backend by shard key, and on failure walks
to the next backend. The ledger locates the current backend by *position* in the
candidate list. The consolidation made the ledger honour the already-failed set
(it was the one selection route that ignored it), which necessarily changes that
position lookup: the just-failed backend is no longer in the list, so the walk
restarts at the head instead of continuing past the failed position.

**Concretely**, with three chat backends `[alpha, beta, gamma]`,
`default_strategy = "batch_shard"` and a request that lands on `gamma`:

| | Attempts | Result if `gamma` fails but `alpha` works |
|---|---|---|
| Before | 1 | the upstream error is returned |
| After | up to 3 | `200`, served by `alpha` |

Only assignments to a **non-first** backend are affected; a shard that lands on
the first candidate behaves identically.

**This is a deliberate improvement, not a regression.** The old forward-only walk
gave a shard assigned to the last backend *zero* failover. The cost is that a
failing request now consumes more attempts before it gives up, which is the
normal failover trade every other strategy already made.

### 6. Shard context now applies to embeddings and messages (D5)

**Before:** shard context (`x-netllm-shard-*` headers, `user`, `metadata`) was
extracted on every surface but only *used* on the two chat paths.
`/v1/embeddings` and `/v1/messages` passed `None` to selection, so `batch_shard`
and deterministic placement silently degraded to round-robin there — including
the `shardless_fallbacks` counter never firing to tell you.

**After:** all surfaces feed shard context to selection and to the batch ledger
on the same terms chat always had.

**What you may notice:** if you run batched embedding or Messages workloads with
shard headers set and `batch_shard`/`auto` selected, placement becomes
deterministic where it was previously round-robin. Cache-affinity should improve;
per-backend load will look less evenly spread, which is the intended effect.

### 7. The retry budget now counts every candidate (D7)

**Before:** the attempt cap was `max(len(pool.backends), 1)` — a count of
*pooled* rows. Two consequences:

- A **request-scoped legacy cloud backend** (the row injected from a caller's
  `Authorization`/`x-api-key` header or from `OPENAI_API_KEY`/`ANTHROPIC_API_KEY`
  in the environment) is not a pool row, so it was never counted. On a
  single-backend pool the budget was 1: the local backend failed, and the
  injected cloud row never got its attempt.
- `/v1/messages` ran *past* its own cap, because the Anthropic fallback tier was
  an unbounded second loop bolted on after the capped one.

**After:** the budget is an explicit sum over everything the request may try —
pool candidates + injected cloud rows + every fallback tier.

**What you may notice:** requests that fall back to cloud now actually reach the
cloud row in small-pool setups where they previously died one attempt short.
Conversely, a `/v1/messages` request can no longer make an unbounded number of
attempts. If you inject cloud credentials per-request and had noticed fallback
"not working" on a one- or two-backend machine, this is why.

### 8. Messages retries now re-resolve the model per backend (D10)

**Before:** the Messages paths rewrote `payload["model"]` once, up front. Every
subsequent retry sent that same name — the *first* backend's idea of the model —
even to a backend with a different alias mapping. Chat and embeddings never did
this; they keep the payload immutable and resolve per backend at call time.

**After:** the payload is immutable on every surface, and each attempt sends the
name *its own* backend advertises. A Messages request that fails over between two
backends with different aliases for the same logical model now succeeds where it
used to fail on the second one.

### 9. A pool-matched backend is always called with a name it serves (D18)

**Before:** the router answered "which backend can serve this name?" and "what
name do I call it with?" using **two different algorithms**. The candidacy
matcher accepted a `name:tag` prefix match; the invocation matcher's
`model_pools` branch did not. Reachable consequence: a backend serving
`poolmodel:7b`, in a pool whose `models` lists `poolmodel`, was a candidate for
*any* requested name and was then invoked with **the raw requested name it does
not serve** — so the upstream returned "unknown model" for a backend the router
had just decided was correct.

**After:** one matcher (`ModelResolver`) answers both questions from one walk.
That backend is now invoked with `poolmodel:7b`.

**What you may notice:** `[routing.model_pools]` setups that appeared to select
the right host and then fail upstream with an unknown-model error now work. The
set of backends considered eligible is unchanged — this was verified against the
old matchers as oracles over generated inputs, with zero candidacy disagreements
and exactly one invoked-name change, which is this one.

---

## Changes to metrics, telemetry and the dashboard

### 10. `docs/telemetry-api.md` is now normative and CI-gated (F-49)

The telemetry payload's key set was hand-mirrored in three clients (the web
dashboard and two Swift menubar files), each re-deriving `total_tokens` from
`prompt_tokens + completion_tokens` rather than reading the field the server has
always sent.

- `docs/telemetry-api.md` now documents the exact key set, block by block, and is
  marked normative.
- The dashboard's client-side `total_tokens` re-derivations are deleted; it reads
  the server value.
- `tests/contract/test_telemetry_contract.py` fails CI if the server emits a key
  the doc does not list, *or* documents a key the server does not emit.

Writing that test immediately turned up one undocumented key —
`subscribers` (bool, whether any client currently holds a `watch=1`
subscription) — which is now documented. **No payload field was added, removed
or renamed**; this is a documentation-and-gate change only.

The structural split of `dashboard.js` (2,825 lines) was deliberately **not**
attempted and is filed as **F-54**.

---

## Changes that are invisible unless you are looking for them

These are real but should not alter anything you observe in normal operation.
They are listed for completeness, because each was a divergence between paths.

- **D2 — failure accounting is written in exactly one place.** The chat-stream
  path's outer error handler and the Messages-stream path each used to record a
  *different subset* of {backend failure mark, `netllm_requests_total{status="error"}`,
  latency} than the non-streaming paths did. The pre-refactor remediation
  (F-32/F-33) had already closed the observable gap — the contract vectors
  recorded before this refactor and the ones recorded after are byte-identical on
  every failure path, which is the proof — and the refactor makes it structural:
  `AttemptRecorder` is now the only code in the repo that touches `mark_success`,
  `_mark_backend_failure`, `REQUESTS_TOTAL`, latency or the token counters.
  **No metric changes value.** This entry exists because the *class* of bug is
  what F-24 was about, and it is now impossible to reintroduce on one path only.
- **D3 — streaming errors reach the client as real HTTP statuses.** Fixed before
  this refactor (F-32), but the refactor makes it structural rather than a
  convention: admission, capability guards and routing all resolve *before* the
  streaming response object is constructed, so a `429` or `400` on `stream=true`
  can no longer be delivered as a `200` with an aborted body.
- **D6 — the two "cloud last" mechanisms became one type.** OpenAI-dialect
  surfaces carried the cloud row as an extra strategy candidate; Messages carried
  Anthropic rows as an ordered final tier. Both are now fields of one
  `CandidateSchedule` walked by one loop. Ordering semantics are unchanged: the
  Anthropic cloud still never shadows the free local mesh.
- **D8 — "this backend failed" and "this backend speaks the wrong dialect" are
  now separate sets.** They used to share one variable with three different
  initialisations per surface. No routing outcome changes; it is why the attempt
  accounting in D7 could be made correct.
- **D12 — request headers are normalised exactly once**, in the plan builder. The
  Messages route used to re-normalise them a second time. Harmless, but it was a
  trap for anyone adding a header-driven feature.
- **D13 — the Anthropic-format upstream now attaches the mesh loop-guard headers**
  (`x-netllm-local-only`, incremented hop count). The OpenAI-format upstream
  always did. Nothing constructs an Anthropic-format `peer:` row today, so this
  was latent rather than broken — it is now closed defensively, so a future
  Anthropic-dialect peer cannot ping-pong requests across the mesh.
- **D14 — scenario rules can now be scoped to specific surfaces, opt-in.**
  Scenario classification runs the chat-shaped heuristic on `/v1/embeddings`
  traffic too, so a rule written for chat has *always* applied there — and a
  `think` rule that swaps in a reasoning model breaks an embeddings request
  outright. Silently narrowing existing configs would be the bigger footgun, so
  the default is unchanged (empty = every surface) and narrowing is opt-in via a
  new `surfaces` field on a scenario rule:

  ```toml
  [routing.sources.scenarios.think]
  model = "qwen3-32b-thinking"
  surfaces = ["chat", "messages"]   # NEW — omit for the old behaviour
  ```

---

## Known issue this refactor uncovered but did not change

### A capability-guard 400 echoes your internal model name back to the client

**This is pre-existing behaviour, present before and after the refactor.** It is
recorded here because separating "the name the client asked for" from "the name
after rewrites" made it visible for the first time.

The capability guard runs on the model name *after* `sources[].model_rewrites`
and any scenario override. Its 400 message quotes that post-rewrite name. So a
client that sends `my-chat-model` against a source configured with
`model_rewrites = { "my-chat-model" = "internal-bge-m3-secret" }` gets:

```
400 Model 'internal-bge-m3-secret' (capability: embedding) cannot serve chat completions.
```

— a name it never sent and was never meant to see. If your rewrite targets encode
anything you would rather not disclose (internal project names, tenant
identifiers, vendor contract names), that string is reachable by any caller who
can trigger the guard.

Other error paths do not have this problem: the exhaustion 404 is built from
`plan.requested_model` and correctly quotes the caller's own name.

**Workaround today:** avoid sensitive strings in `model_rewrites` targets. The
proper fix — quote `requested_model` in the guard, and mention the resolved name
only in the server log — is a one-line change but is a *behaviour* change to an
error body, so it is deliberately not bundled into a refactor whose gate is
byte-identical vectors. It should be filed and fixed on its own.

---

## Rollback

Every phase is a contiguous, individually revertible commit range, and the
per-surface migrations revert per surface. The golden-vector corpus in
`tests/contract/vectors` (141 vectors) is the oracle for whether a revert
actually restored the previous behaviour: an unexpected vector diff is a hard CI
failure, not a reviewer judgement call.
