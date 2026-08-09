# AXIS F — CLI-agent / harness integration: what it actually costs and guarantees today

Read at `main @ 243e3dc` via `git show HEAD:<path>`. Every claim below is file:line or a command I ran.

---

## 0. The one-line answer

There **is** a harness registry (`netllm_core/known_harnesses.py`) — but it holds only *cosmetics*: display name, PATH binary, install string, docs URL. **Not one field describes what the harness needs from the router.** Every functional fact (Claude Code → Anthropic Messages at port root; Codex → `/v1/responses` because `wire_api=chat` was removed; Cursor → OpenAI `/v1`) lives as **English prose in two unlinked places**: a hand-written `_guides()` dict in the CLI and `docs/editor-integration.md`. Adding a harness touches ~14 files across 5 languages, and — proven below — **crashes `netllm connect` with a `KeyError` if you only add it to the registry**.

---

## 1. Harness matrix

Legend: **Detected** = PATH probe; **Attributed** = can be a `routing.sources` identity; **Wired** = something writes the harness's own config; **Routed** = per-source overrides/scenarios reachable; **Capability-modelled** = router holds a machine-readable statement of what this harness requires; **Tested** = any test drives the router the way this harness drives it.

| Harness | Detected | Attributed | Wired | Routed | Capability-modelled | Tested (e2e) |
|---|---|---|---|---|---|---|
| **Claude Code** | ✅ `known_harnesses.py:31-37` (`claude`) | ✅ generic `SourceConfig`; UA needle `"claude-cli"` suggested at `config.example.toml:149` | ❌ prose only — `connect.py:34-45` prints 2 `export` lines | ✅ generic (all `SourceConfig`/`ScenarioRule` fields) | ❌ "anthropic" is a bare string at `connect.py:36`; nothing declares "needs Messages surface / streams by default" | ⚠️ partial — `tests/contract/.../guards-scenario-chat-background-ua.json` sends `user-agent: claude-code/1.0` but on **`chat_ns`**, not the Messages surface Claude Code actually uses |
| **Codex CLI** | ✅ `known_harnesses.py:38-44` (`codex`) | ✅ generic | ❌ prints a TOML block to stdout for copy-paste (`connect.py:54-60`, `connect.py:286`) | ⚠️ **broken for its own surface** — see §3 | ❌ `wire_api="responses"` is a literal inside a Python f-string (`connect.py:59`) | ⚠️ bridge unit tests (`tests/test_codex_responses_bridge.py`, `test_responses_bridge_f39.py`); **live path is a human-filled markdown checklist**, `docs/solutions/codex-responses-smoke.md:1-30` |
| **Cursor** | ⚠️ `known_harnesses.py:56-62` — comment at `:45-48` admits `cli_commands` are "best-effort guesses, not verified against a real install" | ✅ generic | ❌ prose: "Cursor Settings → Models → …" (`connect.py:71-73`) | ✅ generic | ❌ | ❌ |
| **Gemini CLI** | ⚠️ same unverified guess (`known_harnesses.py:49-55`) | ✅ generic | ❌ — and its own note says wiring **may not work at all** (`connect.py:84-87`; `docs/editor-integration.md:137-144`) | ✅ generic | ❌ | ❌ |
| **Honcho** | ⚠️ same unverified guess (`known_harnesses.py:63-69`) | ✅ generic | ❌ prose + separate `docs/honcho-integration.md` | ✅ generic | ❌ (its real requirement — `batch_shard` + shard headers — is prose in `editor-integration.md:175`) | ❌ |
| **Buzz** | ✅ `known_harnesses.py:70-76` (`buzz-agent`) | ✅ generic | ❌ prose (`connect.py:109-112`) | ✅ generic | ❌ | ❌ — `buzz` appears in `tests/test_source_routing.py` **only as a fixture id string** (lines 26,41,68,86,116,129,263,280…), never as an integration |
| **Pi Agent** | ❌ **not in registry** | ⚠️ documented `[[routing.sources]] id="pi-agent"` at `config.example.toml:152-154` | ❌ | ✅ generic | ❌ | ❌ |
| **Antigravity** | ❌ **not in registry** | ⚠️ documented at `config.example.toml:160-162` | ❌ | ✅ generic | ❌ | ❌ |
| **VS Code Copilot / Continue / Cline** | ❌ | ❌ (no id anywhere) | ❌ | — | ❌ | ❌ |

**"Wired" is empty for every row.** Nothing in the repo writes a harness's config file. That's deliberate policy, stated in three places (`known_harnesses.py:23-26` "never executed on the user's behalf"; dashboard copy at `dashboard.js:1997-2000` "never auto-installs"; `.agents/skills/netllm-connect-editor/SKILL.md:43,57,118` "never auto-edit editor configs"). It is a defensible choice, but it is also the ceiling on "plug and play" — the maximum the product does today is *print* the right strings.

**Proof the roster diverges** (ran it):
```
$ .venv/bin/python -c "... invoke(app,['connect','pi-agent'])"
exit 1
Unknown harness 'pi-agent' — Known tools: claude-code, codex, gemini-cli, cursor, honcho, buzz
```
`docs/editor-integration.md:114-133` gives Pi Agent a full JSON wiring block and `config.example.toml:152` ships a source block for it, yet it is undetectable, unconnectable, has no icon, and never appears in `netllm sources list` or the dashboard.

---

## 2. Real cost of adding one harness, end to end

### 2a. The blocking defect: registry-only addition crashes the CLI

`connect.py:241` does `_guides(base_v1, base_root, virtual_key)[harness_id]` against a **hand-written dict at `connect.py:30-114` that is a second, independent roster**. `connect.py:225` validates against `KNOWN_HARNESSES`, so a new entry passes validation and then dies on the dict lookup. Confirmed empirically by appending a `KnownHarness(id="continue", …)` in-process:

```
FAIL KeyError 'continue'
```

There is no test tying `_guides` to `KNOWN_HARNESSES` — `grep -rn "_guides" tests/` returns nothing. Contrast `tests/test_admin_harnesses.py:46-53`, which *does* guard the icon convention with exactly this reasoning ("a new KNOWN_HARNESSES entry with no matching file would silently 404… instead of failing a test"). The same guard was never applied to the guide dict, which fails harder (crash, not 404).

### 2b. Files touched to add "Continue" properly

| # | File | What |
|---|---|---|
| 1 | `packages/netllm-core/src/netllm_core/known_harnesses.py:30-77` | new `KnownHarness` |
| 2 | `packages/netllm-cli/src/netllm_cli/commands/connect.py:30-114` | new `_HarnessGuide` (**mandatory or crash**) |
| 3 | `packages/netllm-cli/src/netllm_cli/commands/connect.py:200` | id list re-typed in the `--help` string |
| 4 | `packages/netllm-agent/src/netllm_agent/static/icons/harnesses/<id>.svg` | required by fixed convention `admin.py:266`, enforced `test_admin_harnesses.py:51-53` |
| 5 | `packages/netllm-agent/src/netllm_agent/static/icons/harnesses/README.md:14-19` | provenance row |
| 6 | `config.example.toml:130-172` | commented `[[routing.sources]]` block |
| 7 | `docs/editor-integration.md` | a new `##` section |
| 8 | `docs/cli-source-routing-plan.md` | 12 harness-id mentions |
| 9-12 | `.agents/`, `.claude/`, `.cursor/`, `.github/skills/netllm-connect-editor/SKILL.md` | **same file 4×** (`scripts/sync-agent-skills.sh`) |
| 13-16 | …`/references/editor-settings.md` | **same file 4×** |
| 17 | `tests/test_known_harnesses.py:10` | hardcoded `ids == {…}` set assertion — fails until edited |

Plus, if the harness needs anything non-generic: `models.py:73` `SurfaceName`, `scenarios.py:27,97` heuristics, `config_merge.py:_merge_sources` field tuple.

### 2c. Duplicated-fact list (the roster is stated 11× in 5 languages)

1. **Harness id roster** — `known_harnesses.py:30-77` (6) ‖ `connect.py:30-114` `_guides` (6, independent dict) ‖ `connect.py:200` help text (6, hand-typed) ‖ `config.example.toml:130-172` (**5, different set** — has pi-agent/antigravity, lacks cursor/gemini-cli/honcho) ‖ `docs/editor-integration.md` (**7, third set** — adds VS Code Copilot) ‖ `static/icons/harnesses/*.svg` (6 files) ‖ `tests/test_known_harnesses.py:10` (6, hardcoded) ‖ 4× `SKILL.md`.
2. **Virtual-key format `netllm-<id>`** — `source_identity.py:16` `_KEY_PREFIX` ‖ `connect.py:178` ‖ `connect.py:240` ‖ `config.example.toml:116` ‖ `editor-integration.md:73,100,132,154` ‖ `AGENTS.md:233`.
3. **Icon URL convention** — `admin.py:266` f-string ‖ `AgentAPI.swift:292` comment ‖ the on-disk filenames.
4. **`SourceConfig` field list** — `models.py:196-218` (15 fields) vs. `config_merge.py:_merge_sources` literal 13-name tuple (+`id`, +`secret` special-cased). Currently complete; **no parity test** — a 16th field is silently unsavable through the dashboard/CLI patch path. (`docs/extending/extension-cost-map.md:263` proposes exactly this test; not written.)
5. **Claude Code's User-Agent** — three different strings, none verified: `config.example.toml:149` says `"claude-cli"`; `scenarios.py:97` hardcodes `"claude-code" in user_agent`; the contract vector sends `claude-code/1.0`. The UA-substring path (`source_identity.py:120-129`) and the background-scenario heuristic therefore key off **different needles**, and a user who copies the config.example block gets attribution but not background classification.
6. **Three control surfaces reimplement the sources UI** — `dashboard.js:1975-2083` (hand-written JS, `toggleHarness` at `:2070`), `SettingsWindowView.swift`, `commands/sources.py`. No shared descriptor. (Already noted at `extension-cost-map.md:195`.)

---

## 3. Functional requirements — is any of it modelled?

**No. There is zero declarative "harness X requires router capability Y."**

`KnownHarness` (`known_harnesses.py:17-27`) has exactly five fields: `id`, `display_name`, `cli_commands`, `install_hint`, `docs_url`. Nothing about surface, wire format, streaming, tool-use, model naming, or context needs.

The nearest thing is `_HarnessGuide.surface: str  # "openai" | "anthropic" | "codex"` at `connect.py:22-27` — a **display-only** string in a CLI presentation dataclass. It is never read by the agent, never validated against the surfaces the agent actually serves, and its third value `"codex"` isn't even a surface name (`models.py:73` defines `SurfaceName = Literal["chat","embeddings","messages"]`; the agent enum has `Surface.CHAT/EMBEDDINGS/MESSAGES` per `surfaces/*.py:79,78,165`). Note also `netllm_core/capabilities.py` is about **model** capabilities (chat/embedding/audio/rerank), not harness ones — the namespace is already taken.

Checked concretely against the four requirements named in the task:

| Requirement | Router honours it? | Modelled? |
|---|---|---|
| Claude Code → Anthropic Messages at port root, streams by default | Yes — `surfaces/messages.py` + `anthropic_bridge.py` (SSE translation incl. `tool_use` at `:105,198-235`) | ❌ the "use port root, no `/v1`" fact exists only as prose (`editor-integration.md:32`) and as `_agent_base_urls` returning `root` for the anthropic guide (`connect.py:117-121`) |
| Codex → `/v1/responses`, `wire_api=chat` removed | Yes — `surfaces/responses.py`, `openai_responses_bridge.py` | ❌ literal string in an f-string (`connect.py:59`) + a comment (`responses.py:33-39`) |
| Tool-use passthrough | Yes on all three — `anthropic_bridge.py:45-48,68-78`; `openai_responses_bridge.py:55-60,136-159,184-185` | ❌ no harness declares needing it; nothing would catch a regression per-harness |
| Long-context / scenario routing | Generic — `scenarios.py:133` threshold, `ScenarioRule` per source | ❌ no harness ships defaults; every user hand-writes `[routing.sources.<id>.scenarios.*]` |

**A concrete consequence of the gap:** `ScenarioRule.surfaces` (`models.py:211`, gate at `:196-211`, consumed `routing_policy.py:87-90`) accepts only `chat | embeddings | messages`. **Codex's surface is not nameable.** `/v1/responses` delegates straight to `proxy_chat_completion` (`responses.py:41-43`, docstring at `:3-7`: "no `Surface.RESPONSES`"), so a Codex request reports `surface="chat"`. A user who writes `surfaces = ["chat"]` to scope a rule to Cursor silently also hits Codex; a user who wants a Codex-only rule cannot express it at all. That is the D14 footgun the code explicitly reasoned about for embeddings (`models.py:198-207`), left open for the one harness the surface was built for.

---

## 4. What breaks silently

| Change | Blast radius | Caught by |
|---|---|---|
| Add a `KnownHarness` without a `_guides` entry | `netllm connect <id>` **hard crash** (`KeyError`, `connect.py:241`) | ❌ **nothing** |
| Add a `KnownHarness` without an SVG | dashboard/macOS 404 | ✅ `test_admin_harnesses.py:46-53`, `:95-102` |
| Add a `KnownHarness` at all | roster assertion | ✅ (as a tripwire) `test_known_harnesses.py:8-10` |
| Harness renames its binary (e.g. `cursor`→`cursor-agent`) | `detected` silently false forever; `install_hint` shown to someone who has it installed | ❌ nothing — and `known_harnesses.py:45-48` **already admits 3 of 6 are unverified guesses** |
| Harness changes its User-Agent | attribution silently reverts to `default` (`source_identity.py:131`); `source_requests` quietly wrong; background scenario stops firing | ❌ nothing (`source_identity.py:64-78` is explicit that it "never denies a request") |
| Harness changes wire protocol (the Codex `wire_api=chat` removal, `editor-integration.md:79-85`) | every user's config breaks | ❌ nothing automated; the only gate is a human filling in `docs/solutions/codex-responses-smoke.md` |
| New `SourceConfig` field not added to `config_merge.py` tuple | field unsavable via dashboard/`sources toggle`/`config import` | ❌ no parity test |

**Conformance:** there is **no** test that drives the router the way any harness drives it. The 250-file contract-vector corpus (`tests/contract/vectors/**`) covers surfaces, streaming, failover, scenarios — organised by *router mechanism*, never by *client*. The single vector carrying a harness fingerprint (`guards-scenario-chat-background-ua.json`) sends a Claude Code UA down the **chat** path, which Claude Code does not use. `netllm connect claude-code --json` emits an exact, machine-readable wiring recipe (`connect.py:180-195`) — nothing consumes it as a test fixture.

---

## 5. Real integration vs. attribution label + docs

Ranked by how much dedicated machinery exists:

1. **Codex — a real integration.** It is the only harness that caused router code to be written: `surfaces/responses.py`, `netllm_core/openai_responses_bridge.py` (~200 lines), 15+ unit tests, contract vectors, a live smoke checklist. Router-side reason documented at `responses.py:33-39`.
2. **Claude Code — a real integration, but pre-existing.** `surfaces/messages.py` + `anthropic_bridge.py` serve the Anthropic surface generally; Claude Code is the beneficiary, not the driver. Its *one* harness-specific line of code is the background heuristic at `scenarios.py:93-97` — which keys off `"claude-code"` while the shipped config example says `"claude-cli"`.
3. **Honcho — docs + a generic feature.** `batch_shard` and shard headers exist; `docs/honcho-integration.md` explains them. No Honcho-specific code path; the registry entry (`pip install honcho`, binary `honcho`) is almost certainly wrong anyway — `honcho` on PATH is the unrelated Foreman-clone process manager, so `detected` is a **false positive waiting to happen**.
4. **Cursor / Gemini CLI — a label, an unverified binary guess, and a paragraph.** Gemini CLI's own note (`editor-integration.md:137-144`) says there is *no confirmed working wiring* — it ships as a registry entry, an icon, and a `connect` guide for something that doesn't work.
5. **Buzz — the register's verdict stands, and understates it.** An id, an in-repo-generated SVG (`icons/harnesses/README.md:14` — "no published Buzz mark exists"), a `_guides` entry, a commented config block. Every `buzz` occurrence in `tests/` is a placeholder source id in `test_source_routing.py`. There is no Buzz-shaped request anywhere, and `install_hint="See the agent-buzz-slack workspace README"` points at a repo that is not this one.
6. **Pi Agent / Antigravity — docs only, and worse: documented-but-unreachable.** Full wiring instructions with no registry entry, so the CLI actively rejects them (proven §1).

---

## 6. Top gaps blocking plug-and-play

1. **`_guides` is a shadow registry that crashes on divergence.** `connect.py:241`. One line of test (`assert set(_guides(...)) == {h.id for h in KNOWN_HARNESSES}`) closes it. Cheapest, highest-severity fix in this axis.
2. **`KnownHarness` models zero functional requirements.** Every "harness X needs Y" fact is prose. The registry-first program's own rule (`PROGRAM.md:15` "single rule") applies here and Axis D doesn't cover it. Minimum viable spec: `surface`, `base_url_shape` (`/v1` vs root), `wire_api`, `requires_streaming`, `requires_tool_use`, `user_agent_needles`, `env_vars`, `extra_config` (the Codex TOML). Then `_guides`, `config.example.toml`, `editor-integration.md`, the dashboard, and the SKILL references become generated, and §2c items 1-2 collapse.
3. **No default `user_agent_needles` per harness.** `SourceMatch.user_agent_contains` defaults empty (`models.py:180`), so UA attribution — one of the three documented resolution paths (`source_identity.py:64`) — works only if the user already knows the string. And `netllm connect --toggle` writes `{"id","enabled","known_id"}` only (`connect.py:152`), never a match block. Result: a user who runs the one-click flow but forgets to change `ANTHROPIC_API_KEY` to `netllm-claude-code` gets attribution silently falling to `default`, with no warning anywhere.
4. **`SurfaceName` cannot name `/v1/responses`** (`models.py:73`), so the harness that motivated that surface cannot be scenario-scoped. Either add `"responses"` to the literal and thread it (it is genuinely a distinct client contract even if internally it's chat), or ledger the exclusion explicitly.
5. **Zero harness conformance tests.** The corpus is organised by mechanism, not by client. The fix has a natural shape given what already exists: make `netllm connect <id> --json` the fixture source, and add one contract vector per harness that replays that harness's *actual* opening request (Codex `/v1/responses` `{"input":…}`, Claude Code `POST /` Messages streaming with `tools`) and asserts surface, `source_counts`, and `scenario_counts`. That turns `docs/solutions/codex-responses-smoke.md` from a human checklist into a red/green gate, and it is the only thing that would have caught the `wire_api=chat` removal.
6. **Three unverified binary names** (`known_harnesses.py:45-48`), one of which (`honcho`) collides with an unrelated widely-installed tool. `detected` is a load-bearing signal in the CLI, the dashboard, and the macOS Settings badge; today three of six rows are guesses the code itself flags as unconfirmed.
7. **Roster divergence between docs and code** — `pi-agent`/`antigravity` documented in two places, absent from the registry; `cursor`/`gemini-cli`/`honcho` in the registry, absent from `config.example.toml`. Generation from a single registry (gap 2) fixes this as a side effect.

**Scoping note for the program:** none of gaps 1-7 fall under Axis D as written (`PROGRAM.md:84-89`, `extension-cost-map.md:179-211`), which scopes "CLI command / control-plane setting" and treats the harness work as an *instance* of that (`extension-cost-map.md:181` cites commit `bf67238` as its example). Axis D would give harnesses a `ControlDescriptor` so the toggle renders identically on three surfaces — it would not give the router a single machine-readable statement of what Claude Code or Codex needs, would not fix the `_guides` crash, and would not produce a conformance test. Axis F is a genuinely separate axis.

## Key paths
- `/home/user/llm-swarm-router/packages/netllm-core/src/netllm_core/known_harnesses.py`
- `/home/user/llm-swarm-router/packages/netllm-core/src/netllm_core/harness_detection.py`
- `/home/user/llm-swarm-router/packages/netllm-core/src/netllm_core/source_identity.py`
- `/home/user/llm-swarm-router/packages/netllm-core/src/netllm_core/scenarios.py`
- `/home/user/llm-swarm-router/packages/netllm-core/src/netllm_core/models.py` (`SourceConfig` 196-218, `ScenarioRule` 153-211, `SurfaceName` 73)
- `/home/user/llm-swarm-router/packages/netllm-cli/src/netllm_cli/commands/connect.py` (`_guides` 30-114, crash site 241)
- `/home/user/llm-swarm-router/packages/netllm-cli/src/netllm_cli/commands/sources.py`
- `/home/user/llm-swarm-router/packages/netllm-agent/src/netllm_agent/admin.py` (`harness_registry_payload` 238-269)
- `/home/user/llm-swarm-router/packages/netllm-agent/src/netllm_agent/service/surfaces/responses.py`
- `/home/user/llm-swarm-router/packages/netllm-agent/src/netllm_agent/service/policy.py` (191-235)
- `/home/user/llm-swarm-router/config.example.toml` (113-172)
- `/home/user/llm-swarm-router/docs/editor-integration.md`
- `/home/user/llm-swarm-router/docs/solutions/codex-responses-smoke.md`
- `/home/user/llm-swarm-router/tests/test_admin_harnesses.py`, `test_known_harnesses.py`, `test_cli_connect.py`, `test_source_identity.py`, `test_source_routing.py`

No files were written; nothing in the tree was modified.