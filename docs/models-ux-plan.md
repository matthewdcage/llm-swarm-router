# Models & pools UX plan — pickers, grouping, search, pool visibility

Status: **proposed** (not started). Follow-up to
[config-schema-rewrite-plan.md](config-schema-rewrite-plan.md) (which
built the generic pool *editor*) driven by first real user testing of
the macOS app (2026-07-22). Scope: macOS Settings first; web-dashboard
parity is a trailing phase, not interleaved.

## 1. Problems (as observed in testing)

1. **Pool editor fields are free text.** The Routing tab's Model pools
   editor renders `hosts` and `models` as bare string-list editors. Both
   have *known, enumerable* candidate sets at runtime (connected peers,
   discovered backends, served model IDs) — free text invites typos that
   fail silently: a misspelled host ref simply never matches, and the
   pool quietly routes nothing.
2. **Models tab is a flat dump.** `modelsTab` (SettingsWindowView.swift)
   renders `routedModels`/`localModels` as flat `ForEach` lists:
   - no grouping by peer/host (the data has `host`, the row never shows it)
   - no collapse/expand
   - no search/filter
   - no pool visibility: nothing shows whether a model is in a
     `routing.model_pools` entry, no way to add/remove from here, no
     indicator whether a pool is currently *effective* (host online +
     serving a pool model)
   - no activity/metrics (backend online/in-flight exist in status but
     aren't surfaced per row)

## 2. Data inventory (what exists today, no server changes needed)

| Need | Source | Gap |
|---|---|---|
| Peer identities | `/netllm/v1/status` `peers[]`: `agent_id`, `hostname`, `listen_url`, `role` | none |
| Backends + their models | `status.backends[]`: `provider`, `base_url`, `local`, `health`, `models[]`, `in_flight` | Swift `BackendStatus` doesn't parse `agent_id` (Python sends it); needed to group peer backends by machine |
| Routed catalog | `/v1/models` | none |
| Pool membership | `document.routing.model_pools` (already a live editable dict in the app) | none |
| Host ref forms a pool accepts | backend id, `peer:<agent-id>`, bare agent_id, base_url (`ModelPool` docstring; same as `x-netllm-backend` pin) | none |
| Per-model metrics (requests, last-used) | — | **not tracked server-side**; in-flight is per-backend only → phase C |

## 3. Design

### Phase A — pool editor pickers (kills the user-error risk; smallest)

New computed candidates on `SettingsViewModel`:

- `knownHostRefs: [(ref: String, label: String)]` — deduped union of:
  - local backends: `base_url` (label `"<provider> · <base_url>"`)
  - peers: bare `agent_id` (label `"<hostname> (<agent_id>)"`); peers
    deduped by `agent_id` across `status.peers` + `lanPeers`
- `knownModelIDs: [String]` — union of `status.backends[].models`,
  deduped, sorted case-insensitively.

Widget change: `SchemaFieldOverride` gains `suggestions: [String]?`
(with optional display labels). `SchemaFormView`'s `list_strings` case
and `EditableStringList` rows gain a trailing menu button (`plus.circle`
→ `Menu` of unused suggestions) beside the existing text field:

- picking inserts the canonical ref — no typing
- the text field stays editable (offline agent / not-yet-seen host are
  legitimate), so this is *assist*, not *restrict*
- soft validation: a row whose value matches no suggestion gets an
  `exclamationmark.triangle` + tooltip ("not currently known — check
  spelling or bring the host online"), never a hard block

`modelPoolEditor` threads `hosts: suggestions=knownHostRefs`,
`models: suggestions=knownModelIDs` through `itemOverrides`.

### Phase B — Models tab rework

**B1. Parse `agent_id` into `BackendStatus`** (AgentAPI status parser) —
prerequisite for honest grouping; peer backends currently only carry a
`base_url`.

**B2. Grouped, collapsible, searchable list.** Replace the two flat
sections with one machine-grouped view:

- Group key: machine. Local backends → one group titled with this Mac's
  hostname; each peer's backends → group titled `"<hostname>
  (<agent_id>)"` (matched via backend `agent_id` → `status.peers`).
- Each group is a `DisclosureGroup` (default expanded, state kept in
  `@State` per group id) whose header shows: online dot, backend
  provider summary (`omlx · ollama`), model count, aggregate in-flight.
- One search field above the list filtering on model/provider/host;
  while a filter is active, groups auto-expand and empty groups hide.
- Row: model name; provider caption; per-row trailing area used by B3/B4.

**B3. Pool membership + inline editing.**

- Badge per row listing pools containing that model (capsule per pool
  name, colored by pool `enabled`).
- Row menu (ellipsis or context menu):
  - "Add to pool ▸" — existing pool names (those not already
    containing it) + "New pool…" (creates `pool`/`pool-2`… via the
    existing `addModelPool()` naming, adds the model, focuses Routing
    tab naming later — no modal)
  - "Remove from <pool>" per containing pool
- Mutations edit `document.routing.model_pools` directly (same draft the
  Routing tab edits — single source, no sync problem) and mark the
  Save-needed state exactly like Routing edits do. Saving still goes
  through the existing toolbar **Save** (config import path).
- Pool *effectiveness* dot on the badge: a pool is "active" iff ≥1 of
  its host refs resolves to an online backend that serves ≥1 pool model
  — all computable client-side from `status`. Tooltip explains why
  inactive ("host offline", "no pool model served").

**B4. Activity (honest scope).** Per-*backend* metrics only, because
that's what the server tracks: group header gets `in_flight` and
health; a row gets a subtle "live" dot when its backend is online.
Per-*model* counters are phase C — do **not** fake them from backend
numbers.

### Phase C — per-model metrics (server, deferred)

Agent-side: count requests per resolved upstream model (and last-used
timestamp) in the proxy paths, expose as `model_stats` in
`/netllm/v1/status`. Only then can rows show "N requests · last used
2m ago" and pools show real utilization. Separate PR; needs
netllm-agent AGENTS.md contract notes + tests. Not scheduled until
A/B prove worth extending.

### Phase D — web dashboard parity (trailing)

Mirror A (suggestions in the pool editor via a `<datalist>`; candidates
from `status`) and B (grouping/search/badges in the dashboard Models
tab). The dashboard already fetches everything needed. Kept last so the
native app — where testing is happening — leads.

## 4. Non-goals / constraints

- No hard validation of host/model entries (offline candidates are
  legitimate); assist + warn only.
- No new HTTP endpoints for A/B — status + models + config cover it.
- `routing.model_pools` stays the single storage; the Models tab writes
  the same draft dict the Routing tab edits.
- Weighted/preferred pools (`model_groups`) remain future work
  (routing-hardening-plan.md phase 4) — nothing here should preclude
  folding `model_pools` into `model_groups` later.

## 5. Known issue noted during the same testing session

**Keychain prompt on every rebuild** — the app is ad-hoc signed (no
Developer ID on this machine), so each rebuild has a new code identity
and macOS re-prompts for the `netllm` Keychain items; "Always Allow"
only binds to that build. Permanent fix is a Developer ID certificate
(stable TeamIdentifier), not a code change. Tracked here so it isn't
re-diagnosed; see [macos-code-signing.md](macos-code-signing.md).

## 6. Suggested order & sizing

| Phase | Size | Risk | Ships alone? |
|---|---|---|---|
| A pickers | S | low (additive widget param) | yes |
| B1 agent_id parse | XS | low | with B2 |
| B2 group/collapse/search | M | medium (replaces modelsTab body) | yes |
| B3 pool badges/editing | M | medium (writes shared draft) | yes |
| B4 backend-level activity | S | low | with B2/B3 |
| C per-model metrics | M (server+app) | medium | later |
| D dashboard parity | M | low | later |
