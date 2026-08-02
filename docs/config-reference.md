# Config reference — routing precedence and model resolution

Two questions the router answers on every request are spread across several
config surfaces, and both used to be discoverable only by reading the code.
This page is their normative reference. Both tables are **machine-checked**:

- routing precedence → `tests/test_routing_precedence_table.py`, which parses
  the layer order out of this file and asserts it against
  `netllm_core.routing_policy.resolve_routing`;
- model resolution → `netllm_core.model_resolution.ModelResolver`, whose walk
  is pinned by `tests/test_model_resolution_property.py`.

If you edit a table here without changing the code, the test fails. That is
the point (F-25, `docs/architecture/07-findings-register.md`).

---

## 1. Routing precedence

Five layers can each set `strategy`, `local_only` and `allow_cloud`.
**Lowest precedence first; the last layer that sets a field wins it.**

<!-- ROUTING-PRECEDENCE-TABLE:BEGIN -->

| # | Layer | Config location | Sets |
|---|---|---|---|
| 1 | globals | `[routing]` — `default_strategy` | `strategy` |
| 2 | policies | `[[routing.policies]]` — first *enabled* entry matching `model_prefix` / `api_format` / `source` | `strategy`, `prefer_provider`, `allow_cloud` |
| 3 | source | `[[routing.sources]]` — the caller's resolved source | `strategy`, `prefer_provider`, `local_only`, `allow_cloud`, `cloud_providers` |
| 4 | scenario | `routing.sources[].scenarios.<name>` — the classified scenario, gated by the rule's `surfaces` | `strategy`, `local_only`, `allow_cloud` |
| 5 | headers | `x-netllm-strategy`, `x-netllm-local-only`, `x-netllm-backend` | `strategy`, `local_only`, pinned backend |

<!-- ROUTING-PRECEDENCE-TABLE:END -->

Read it as: a scenario rule's `strategy` beats its source's, which beats a
matching policy's, which beats `routing.default_strategy` — and
`x-netllm-strategy` beats all four (an unrecognised value is ignored, and
the layer below stands).

### The two asymmetries

Precedence is a clean total order for `strategy` and `prefer_provider`.
Cloud/locality has two deliberate exceptions:

1. **`x-netllm-local-only` is a ceiling, not a layer.** When the caller sets
   it, no policy, source or scenario can reopen cloud or remote routing for
   that request: `local_only` is forced true and both `allow_cloud_inject`
   and `cloud_leads` are forced false, *after* every other layer has run.
2. **`cloud.enabled = false` is a floor.** The `[cloud]` master switch gates
   every cloud opt-in above it; a policy or source with `allow_cloud = true`
   re-enables cloud only while the master switch is on. `cloud.fallback`
   then chooses the ordering: `"cloud"` (default) local-mesh-first,
   `"local"` cloud-first (`cloud_leads`), `"none"` no *automatic* fallback —
   though an explicit `allow_cloud = true` still opts a specific route in.

Within a layer, `local_only` is checked before `allow_cloud`: a source or
scenario rule that sets both is local-only.

---

## 2. Model resolution

Which backend may serve a name, and which name is sent upstream, are answered
by **one walk** in `ModelResolver` (F-25). The stages, in order:

| # | Stage | Matches | Sends upstream |
|---|---|---|---|
| 1 | `alias-exact` | requested name, then each `routing.model_aliases` entry for it, present verbatim in the backend's catalog | the matched name |
| 2 | `alias-tag-prefix` | a catalog entry `name:tag` for one of those names | the **full** served ID (a bare name only means `:latest` upstream) |
| 3 | `alias-casefold` | as 1–2, case-insensitively | the served ID's own casing (oMLX rejects re-cased names) |
| 4 | `group-exact` / `group-tag-prefix` / `group-casefold` | the same three arms over the models of every enabled `routing.model_pools` entry this backend is a host of | the matched served ID |
| 5 | `blind-catalog` | the backend has no known catalog (unprobed, or a cloud row keyed per request) | the requested name, unchanged |
| — | `auth-gated` | empty catalog on a **local** backend whose probe returned 401/403 | nothing — the backend is not a candidate |
| 6 | `passthrough` | nothing matched | the requested name, unchanged; the backend is not a candidate |

Candidacy (`serves()`) is *derived* from this walk — it is "stages 1–4
matched, or stage 5" — so a backend can never be selected by one rule and
then invoked under another. Before Phase 8b these were two separate
matchers and they disagreed; see behavior-matrix D18.

Two name layers run *before* this walk, per request, and are not part of it:
`routing.sources[].model_rewrites` (request name → canonical name) and then
`routing.sources[].scenarios[].model` (a hard override on top). Both are
applied once when the request plan is built.

`routing.model_pools` parses into the resolver's internal `ModelGroup`
representation. A pool **is** a group, so the planned
`routing.model_groups` (`docs/routing-hardening-plan.md`) is a second parser
into the same representation rather than a second mechanism.
