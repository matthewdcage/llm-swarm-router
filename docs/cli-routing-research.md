# AI CLI routing research — gateways, source identity, and where netllm fits

Status: research grounding for [cli-source-routing-plan.md](cli-source-routing-plan.md)
Date: 23/07/2026

## The problem being solved

Every major AI coding CLI (Claude Code, OpenAI Codex, Gemini CLI, Cursor, Copilot,
plus custom harnesses) speaks an OpenAI-compatible or Anthropic-compatible HTTP
API and accepts a base-URL override (`OPENAI_BASE_URL`, `ANTHROPIC_BASE_URL`, or
a config-file provider block). The dominant community pattern in 2026 is a
**local gateway** at `http://localhost:<port>/v1` that normalises providers and
routes requests.

**netllm already is that gateway.** It exposes dual surfaces (OpenAI `/v1` and
Anthropic `/v1/messages`), discovers local backends (oMLX, Ollama, LM Studio,
vLLM), meshes over the LAN, and has optional cloud failover. So the question
this research answers is *not* "which gateway should we adopt" but:

1. What do the popular gateways do that netllm doesn't, that matters for
   routing **from** common CLIs?
2. What is the best mechanism for a CLI or custom harness to register with
   netllm as a durable, configurable **known source** with its own routing
   behavior?

## Landscape cross-check (verified 23/07/2026)

Findings from the original research memo, re-verified and corrected:

| Gateway | Verified? | Notes / corrections |
|---|---|---|
| **LiteLLM** (BerriAI) | Yes | Python, 100+ providers, virtual keys, budgets, official Codex/Gemini CLI tutorials. Heavyweight (Postgres for full features). |
| **Bifrost** (maximhq) | Yes | Go, ~11µs claimed overhead, first-class `/anthropic` handler, MCP passthrough. Note: most benchmark articles ranking Bifrost first are published by Maxim AI itself — treat perf claims as vendor-sourced. |
| **CLIProxyAPI** | Yes, attribution corrected | Lives at `router-for-me/CLIProxyAPI` (not "cliproxy"). Wraps OAuth CLI subscriptions (Codex, Claude Code, Gemini CLI, Qwen Code, Antigravity) as API endpoints on `:8317`, with multi-account rotation. |
| **claude-code-router** (musistudio) | Yes | Scenario routing for Claude Code: `default` / `background` / `think` / `longContext` (>32K tokens) / web-search models, each mappable to a different provider. Launched via `ccr code`. This is the highest-signal pattern for netllm. |
| **Kong AI Gateway** | Yes | Team governance layer; supports proxying the four major CLIs. Reference for audit/virtual-key patterns, not a fit for home-lab. |
| **OpenRouter** | Yes | Managed, zero-ops; netllm already integrates it as a cloud provider (incl. OAuth PKCE). |
| **Antigravity proxies** | Partially | Community wrappers exist and Antigravity appears as a CLIProxyAPI provider; individual repos churn quickly. Treat as unstable. |
| **pi CLI / pi-openai-proxy** (`victor-software-house/pi-openai-proxy`) | **Not verified** | Could not confirm this project exists as described. Do not build against it. |

### What the gateways have that netllm lacks (relevant to this task)

1. **Source identity.** LiteLLM virtual keys and Kong consumers let the gateway
   know *who* is calling. netllm today treats the API key as a placeholder
   (`netllm-local`) and has no per-client concept — every CLI looks identical.
2. **Per-source routing policy.** claude-code-router's scenario routing
   (background → cheap model, think → strong model, longContext → big-context
   model) is per-*request-class* within one source. LiteLLM's key-level model
   allowlists/budgets are per-*source*. netllm's `[[routing.policies]]` can
   match model prefix and api_format but nothing about the caller.
3. **Model rewriting.** All of them can map a requested model name to a
   different concrete model per route (netllm has `model_aliases` and
   `model_pools`, which are close, but they're global, not per-source).
4. **Subscription wrapping** (CLIProxyAPI): turning OAuth CLI sessions into
   API backends. netllm already has a sanctioned slice of this
   (`cloud.providers.anthropic.auth = "plan_token"`, OpenRouter OAuth).
   Broad subscription-wrapping is ToS-risky; out of scope.

## Options considered

### Option A — Native "known sources" in netllm (recommended)

Add a first-class source-identity layer to the agent: a request is attributed
to a **source** (claude-code, codex, cursor, honcho, or a custom harness) via,
in priority order, an explicit header (`x-netllm-source`), a per-source API
key (`netllm-<source>[-<secret>]` replacing the single placeholder), or
User-Agent/surface heuristics as a fallback. A `[[sources]]` config section
gives each source durable routing overrides: default strategy, model rewrites,
scenario rules (claude-code-router style), local-only, cloud allowlist,
concurrency caps. Everything stays observable in `/metrics`, status, and the
dashboard.

Why: netllm already owns discovery, mesh routing, health, failover, and both
wire formats. Bolting LiteLLM in front would duplicate all of that, add a
Python/Postgres stack to a lean uv workspace, and put a second hop on every
request. The genuinely missing piece — caller identity + per-caller policy —
is small relative to what netllm already has.

### Option B — Chain an external gateway (LiteLLM or Bifrost) behind netllm

Register LiteLLM/Bifrost as a `[[routing.backends]]` row (they're
OpenAI-compatible), letting it fan out to its 100+ cloud providers while
netllm keeps owning local/mesh routing. Zero code needed today. Reasonable as
an *optional* pattern for exotic cloud providers netllm doesn't pre-configure;
document it, don't depend on it.

### Option C — LiteLLM/Bifrost in front of netllm (rejected)

CLIs point at LiteLLM; netllm becomes just another backend. Rejected: loses
netllm's scenario/mesh headers (`x-netllm-*`), splits config across two
systems, and the mesh-aware strategies (spillover, batch_shard, agent-hop)
can't be driven from the outer gateway.

## Patterns worth importing (from the cross-check)

- **Scenario classes** from claude-code-router: classify each request as
  `default` / `background` / `think` / `long_context` / `web_search` using
  observable signals (Claude Code sends distinguishable sub-agent traffic;
  token count is computable; `thinking` blocks are visible in the Messages
  body) and let each source map scenarios to models/strategies.
- **Virtual keys** from LiteLLM/Kong: per-source keys enable identity without
  any client-side changes beyond the env var the CLI already sets.
- **First-class Anthropic surface** from Bifrost: netllm already has this;
  keep parity of routing features across both surfaces (a past gap fixed in
  routing-hardening phase 1) as a standing gate for all new source features.
- **`ccr code`-style launcher** UX: a `netllm connect <tool>` command that
  prints (or applies, with consent) the exact env/config wiring per CLI, and
  registers the source in config — replaces the manual steps in
  [editor-integration.md](editor-integration.md).

## Buzz as the first custom harness

Buzz (`agent-buzz-slack`, separate Rust workspace) is the concrete "custom
harness" this work must serve. Its coding agent, `buzz-agent`, is an ACP agent
whose LLM client (`crates/buzz-agent/src/llm.rs`, `config.rs`) takes a
`provider` + `base_url` + `api_key`:

- **Anthropic provider** posts to `{base_url}/v1/messages` with `x-api-key` —
  matches netllm's Anthropic surface directly.
- **OpenAI-compatible provider** with `OpenAiApi::Auto` picks the Responses API
  only for `*.openai.com` hosts and **Chat Completions for everything else** —
  so a netllm `base_url` gets `/v1/chat/completions` with no config fights.
- It runs up to 8 concurrent sessions per process and Buzz's design goal is
  "ten agents in parallel behind Buzz" — precisely the aggregate-parallel
  workload the swarm's `local_spillover` / `batch_shard` strategies exist for.

Integration path: **zero Buzz-side code changes.** Point `buzz-agent` at
`http://<netllm-host>:11400/v1` (or the Anthropic surface) with API key
`netllm-buzz`; the virtual-key identity in the plan's Phase 1 attributes all
Buzz traffic, Phase 2 gives it per-source strategy (e.g. `local_spillover` +
concurrency cap) and model rewrites, and Phase 5 validates the parallel-fleet
case. An optional later Buzz-side patch can add `x-netllm-source` /
shard-context headers per session for finer attribution, but it is not
required.

## Decision

Proceed with **Option A** (native known sources), keep **Option B** as a
documented escape hatch for long-tail cloud providers. Full phase-gated
delivery plan: [cli-source-routing-plan.md](cli-source-routing-plan.md).
