# Agent & developer guide

## DOX rail

Full protocol: [`.cursor/agents/AGENTS.md`](.cursor/agents/AGENTS.md).

- Before editing: walk root → target path; read every `AGENTS.md` on the route (nearest doc controls local detail)
- After meaningful edits: update the closest owning `AGENTS.md`; refresh Child DOX Index when boundaries change
- Child docs must not weaken parent DOX

## Project overview

**swarm-llm (netllm)** is a mesh router for local LLM backends. Each host runs a lightweight agent that discovers oMLX (macOS), Ollama, LM Studio, and vLLM on localhost, finds sibling agents on the LAN via mDNS, and exposes dual API surfaces: OpenAI-compatible `http://<host>:11400/v1` and Anthropic Messages API `http://<host>:11400/v1/messages` (with translation to local backends).

Tech stack: Python 3.11+, [uv](https://docs.astral.sh/uv/) workspace monorepo, FastAPI agent, Typer CLI.

## Architecture

| Package | Path | Role |
|---------|------|------|
| netllm-core | `packages/netllm-core/` | Routing, health cache, config |
| netllm-sdk-openai | `packages/netllm-sdk-openai/` | OpenAI SDK upstream adapter |
| netllm-sdk-anthropic | `packages/netllm-sdk-anthropic/` | Anthropic SDK upstream adapter |
| netllm-discovery | `packages/netllm-discovery/` | Local scan, swarm registry, mDNS |
| netllm-agent | `packages/netllm-agent/` | FastAPI: `/v1/*`, `/netllm/v1/*`, `/metrics` |
| netllm-cli | `packages/netllm-cli/` | Typer CLI |

Honcho integration: [docs/honcho-integration.md](docs/honcho-integration.md).

## Repository layout

| Path | Purpose | DOX |
|------|---------|-----|
| `packages/` | Python source of truth (uv workspace) | [packages/AGENTS.md](packages/AGENTS.md) |
| `apps/` | Native apps: macOS menubar today (`apps/netllm-mac/`) | [apps/AGENTS.md](apps/AGENTS.md) |
| `packaging/` | Release builds per OS: [packaging/README.md](packaging/README.md) | [packaging/AGENTS.md](packaging/AGENTS.md) |
| `docs/` | User install/troubleshoot/editor guides: [docs/README.md](docs/README.md) | [docs/AGENTS.md](docs/AGENTS.md) |
| `tests/` | Cross-package integration tests | [tests/AGENTS.md](tests/AGENTS.md) |
| `scripts/` | CI, skill sync, install emulation | (root rail) |
| `Formula/` | Homebrew formula | (root rail) |
| `archived/` | Local deprecated/moved files (gitignored; not on remote) | (root rail) |
| `.agents/skills/` | Canonical agent skills → sync via `scripts/sync-agent-skills.sh` | [.agents/AGENTS.md](.agents/AGENTS.md) |
| `.cursor/agents/` | DOX protocol + tracked Cursor coordinator subagents | [.cursor/agents/AGENTS.md](.cursor/agents/AGENTS.md) |

Edit skills only under `.agents/`; run `scripts/sync-agent-skills.sh` after changes.

## Key commands

Prefer `./netllm` from the repo root, works without global PATH (`uv run` wrapper in [netllm](netllm)).

| Command | Purpose |
|---------|---------|
| `uv sync` | Install workspace dependencies |
| `./netllm init` | Write config, scan local providers, optional global CLI (TTY asks single vs swarm) |
| `./netllm init --swarm` | LAN swarm: bind `0.0.0.0`, `local_spillover`, `subnet_scan` (open trusted LAN; upgrades existing config) |
| `./netllm init --swarm --secure` | Same + generate `swarm.cluster_token` and print join command |
| `./netllm join URL --token T` | Secured swarm: validate token, write LAN bind + peer (not needed on open home LAN) |
| `./netllm swarm-token` | Show token; open LAN exits 0 with guidance; `--create` / `--rotate` for secured pairing |
| `./netllm install` | Global `netllm` via `uv tool install` + shell PATH |
| `./netllm serve` | Start agent (foreground, default `127.0.0.1:11400`) |
| `./netllm start` / `stop` / `restart` | Background agent (macOS app, Homebrew, Linux systemd, Windows service) |
| `./netllm serve --host 0.0.0.0` | LAN + swarm: other machines can reach this agent |
| `./netllm status` | Agent, backends, swarm peers |
| `./netllm models` | Routed model catalog |
| `./netllm models --lan` | Models on remote LAN agents |
| `./netllm peers` | mDNS browse for swarm agents |
| `./netllm discover` | Probe oMLX / Ollama / LM Studio / vLLM on localhost |
| `./netllm test` | 1-token latency diagnose (OpenAI backends) |
| `./netllm test --api anthropic` | 1-token Messages API probe via agent |
| `./netllm gateway` | Promote agent role to gateway |
| `./netllm doctor` | PATH, mDNS, backend misconfig checks |
| `./netllm config-edit` | Open `config.toml` in `$EDITOR` |
| `./scripts/ci.sh` | Lint + test (same as CI) |
| `./scripts/ci.sh lint` | Ruff check + format --check |
| `./scripts/ci.sh test` | Run tests |
| `./scripts/ci.sh packaging` | Build deb/rpm (Linux) or zip (Windows) smoke artifacts |
| `scripts/verify-before-pr.sh` | Pre-push gate: lint + test + macOS `swift build -c release` |
| `scripts/verify-before-pr.sh --full` | Above + menubar e2e when Stage `.app` exists |
| `scripts/agent-verify-setup.sh` | Health + models check after setup |
| `scripts/sync-agent-skills.sh` | Sync `.agents/skills/` to other tool paths |

## Environment

Config: `~/.config/netllm/config.toml` (created by `./netllm init`). Example: [config.example.toml](config.example.toml).

Wire any OpenAI-compatible client:

```bash
export OPENAI_BASE_URL=http://127.0.0.1:11400/v1
export OPENAI_API_KEY=netllm-local
```

Native Anthropic Messages API (Claude Code, etc.):

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:11400
export ANTHROPIC_API_KEY=netllm-local
```

Use a real `ANTHROPIC_API_KEY` only for cloud failover; local mesh uses `netllm-local`.

Default provider ports: oMLX `:8080`, Ollama `:11434`, LM Studio `:1234`, vLLM `:8000`.

## Linux and Windows

| Platform | Install | Troubleshooting | Background agent | UI |
|----------|---------|-----------------|------------------|-----|
| Linux | [docs/linux-install.md](docs/linux-install.md) | [docs/linux-troubleshooting.md](docs/linux-troubleshooting.md) | `systemctl --user enable --now netllm` (deb/rpm) | http://127.0.0.1:11400/ui/ |
| Windows | [docs/windows-install.md](docs/windows-install.md) | [docs/windows-troubleshooting.md](docs/windows-troubleshooting.md) | `NetllmAgent` service via packaged zip | http://127.0.0.1:11400/ui/ |

Cross-platform matrix: [docs/platform-matrix.md](docs/platform-matrix.md). Agent graph wiki: `graphify-out/wiki/index.md` (after `graphify update .`).

## macOS menubar app

Native app (oMLX-style): [docs/macos-install.md](docs/macos-install.md) · Troubleshooting: [docs/macos-troubleshooting.md](docs/macos-troubleshooting.md).

| Channel | Install |
|---------|---------|
| **Source build (recommended macOS 26+)** | Clone tag → `build.sh release` → `packaging/scripts/macos-app-install.sh --source …` — [macos-install.md](docs/macos-install.md) |
| Homebrew | `brew install netllm` + `brew services start netllm` |
| CLI / dev | `./netllm serve` from repo root |
| GitHub DMG | When notarized — until then ad-hoc DMGs fail Gatekeeper on macOS 26+ |

Build: `apps/netllm-mac/Scripts/build.sh release` (requires `venvstacks` + `uv sync`).

**CI / release:** [docs/ci-and-release.md](docs/ci-and-release.md) — PR jobs, macOS Swift constraints, release tag workflow.

macOS menubar PRs must pass `menubar-lifecycle` on GitHub (`macos-14`, Swift 5.10): keep `Package.swift` at **swift-tools 5.9**, mark menubar SwiftUI views `@MainActor`, gate Tahoe `glassEffect` behind `LIQUID_GLASS_SDK` in `build.sh`. Run `scripts/verify-before-pr.sh` before push.

## SDK maintenance

Vendor SDKs are isolated in `netllm-sdk-openai` and `netllm-sdk-anthropic` only, `netllm-core` never imports `openai` or `anthropic`. Tracked versions: [docs/sdk-versions.md](docs/sdk-versions.md).

**Bump checklist** (one package per PR):

1. Edit `anthropic>=…` or `openai>=…` in the matching `packages/netllm-sdk-*/pyproject.toml`
2. `uv sync` and commit `uv.lock`
3. Update [docs/sdk-versions.md](docs/sdk-versions.md) (resolved version + date)
4. `./scripts/ci.sh sdk` then `./scripts/ci.sh`
5. Read upstream SDK changelog; adjust adapter (`client.py`), bridge (`anthropic_bridge.py`), or agent layer per [docs/sdk-versions.md](docs/sdk-versions.md#change-layers)

## Agent skills

Load the matching skill when the user asks to install, connect an editor, set up a swarm, or troubleshoot netllm. In Claude Code, use slash commands (e.g. `/netllm-setup`).

| Skill | Triggers | Canonical path |
|-------|----------|------------------|
| `netllm-setup` | install swarm-llm, set up netllm, `/netllm-setup` | `.agents/skills/netllm-setup/SKILL.md` |
| `netllm-connect-editor` | connect Cursor, wire Claude Code, Codex local model, `/netllm-connect` | `.agents/skills/netllm-connect-editor/SKILL.md` |
| `netllm-swarm` | LAN swarm, multi-machine, `/netllm-swarm` | `.agents/skills/netllm-swarm/SKILL.md` |
| `netllm-doctor` | netllm broken, no models, agent unreachable, `/netllm-doctor` | `.agents/skills/netllm-doctor/SKILL.md` |

Tool-specific copies: `.claude/skills/`, `.cursor/skills/`, `.github/skills/`. Keep in sync via `scripts/sync-agent-skills.sh`.

Editor wiring reference: [docs/editor-integration.md](docs/editor-integration.md).

## Code style

- Python 3.11+, line length 88 (ruff)
- Type checking: basedpyright, mode `standard`
- Imports: ruff isort (`E`, `F`, `I`, `UP` rules)
- Match existing package layout and Typer/Rich CLI patterns in `netllm-cli`

## Testing

- Runner: pytest (`tests/`, asyncio mode auto)
- CI: `./scripts/ci.sh lint` (Ubuntu) then `./scripts/ci.sh test` + `./scripts/ci.sh packaging` (Ubuntu + Windows); macOS `menubar-lifecycle` on PRs that touch `apps/netllm-mac/` or packaging
- Pre-push: `scripts/verify-before-pr.sh` (see [docs/ci-and-release.md](docs/ci-and-release.md))
- Add tests only for real behavior; avoid trivial assertions

## Git workflow

Human contributors: see [CONTRIBUTING.md](CONTRIBUTING.md) for fork/PR workflow, issue templates, and review expectations.

- Conventional commit messages; focus on why
- Do not commit `.cursor/plans/`, `.cursor/outreach/`, `.cursor/hooks/`, `.cursor/mcp.json`, `.cursor/rules/graphify.mdc`, `archived/`, `.env`, or secrets
- Do not commit unless the user explicitly asks (agents); human contributors open PRs per CONTRIBUTING.md

## Do not

- Edit user `.env` files or replace keys/values unless explicitly directed
- Delete files: move to local `archived/` and log in `archived/ARCHIVE_LOG.txt` (gitignored; never commit)
- Commit secrets, API keys, or real credentials
- Assume `netllm` is on PATH: prefer `./netllm` from repo root in instructions
- Skip `./netllm doctor` before declaring setup complete
- Auto-edit user editor `settings.json` without explicit consent
- macOS menubar in-app install only works from `/Applications/llm-swarm-router.app` or `netllm-mac.app`; web dashboard proxies update checks via `GET /netllm/v1/update/check`

## Learned User Preferences

- Validate macOS updater/install fixes locally (`tests/test_bundled_install_scripts.sh`, `scripts/test-menubar-e2e.sh`) before release commits or tags
- Run `./scripts/verify-before-pr.sh` (or `--full` with menubar e2e) before pushing macOS menubar PRs; after editing `design-tokens.json`, run `scripts/generate-dashboard-tokens.py` (CI enforces via `--check`)
- macOS in-app update must stop the agent and free `:11400` as part of install — not require manual **Stop** first
- Commit macOS update/install fixes as focused slices separate from unrelated feature work when possible
- Run local agent smoke (`./netllm test`, menubar e2e) before PR, merge, and release
- Never auto-post GitHub comments, discussion replies, or community posts — draft only unless the user explicitly says post/ship/submit; coordinator agents (harvest → monitor → draft) run as **Cursor-native subagents**, no external LLM APIs
- User submits outreach manually (headed browser with logged-in profile; Reddit as u/dreamsofsoaring via `reddit` CDP `:9224`; GitHub/forums as matthew@hydradigital.com.au / @matthewdcage via `work` CDP `:9223`; awesome-list PRs via `gh pr create` with OAuth, not `GITHUB_TOKEN` PAT)
- Coordinator daily goal: nurture guided community engagement — reply-first on live threads, never leave replies on our posts/questions unanswered, triage urgent items to inbox/escalation; defer Show HN until reply traction (not same week as DevHunt)
- Never publish personal machine paths in public docs, release notes, or release assets — use placeholders (e.g. `/path/to/llm-swarm-router`)
- No em dashes in coordinator drafts, briefs, and community replies (`.cursor/coordinator/voice.md`)
- Qualify ~7× throughput as aggregate parallel load across machines, not single-chat speed (same framing as oMLX #1762)
- No star asks or star emoji in community posts after reply-first credibility is earned

## Learned Workspace Facts

- Local web dashboard at http://127.0.0.1:11400/ui/ on all platforms; macOS menubar has **Open Dashboard**; same-host `http://<LAN-IP>:11400/ui/` has full admin; remote LAN browsers are read-only unless `swarm.cluster_token` is set
- Linux/Windows **alpha** use `/ui/` + CLI; macOS stable adds menubar app: same agent core
- Published GitHub Releases attach DMG (macOS), `.deb`/`.rpm` (Linux), Windows zip, and `netllm.yaml` via `.github/workflows/release.yml`: see [docs/platform-matrix.md](docs/platform-matrix.md)
- `./netllm` wrapper runs `uv run --directory $ROOT netllm`: no global install needed; `scripts/agent-verify-setup.sh` prefers global `netllm` when on PATH — use `./netllm` for repo-local smoke
- mDNS (swarm discovery) requires zeroconf from `uv sync`; `serve` on loopback blocks LAN peers — **default trusted-LAN path:** menubar **Listen on LAN** or `init --swarm` (open mesh, no token); peers pair via mDNS / subnet scan; **secured path:** `init --swarm --secure`, Settings **Require cluster token**, or `join` with shared token; `ensure_lan_mesh_defaults()` on LAN bind sets `local_spillover` + `subnet_scan` without minting tokens; loopback agents advertise `reachable=false` and `netllm peers` explains the rebind; LAN-bound agents auto-run one subnet scan when mDNS finds no peers in 10s; `doctor` notes (not errors) when LAN is open without token; Settings polls live agent status while open (see [apps/netllm-mac/AGENTS.md](apps/netllm-mac/AGENTS.md))
- **Agent-hop swarm routing:** gateways merge peer **agent** backends (`http://<peer-LAN>:11400/v1`) via `peer_agent_backends()`, not peer loopback oMLX URLs; hops carry `x-netllm-local-only` (loop guard) and peers advertise only `local=true` rows; multi-machine same-model load spread uses `local_spillover` (swarm default; heartbeat-fed load + own-hops ledger), `round_robin`, or `batch_shard` ([docs/honcho-integration.md](docs/honcho-integration.md)); mixed-provider naming merges via `[routing.model_aliases]`; unknown models 404 with the live catalog; `swarm.peers` save/scan rejects this host's own listen URL
- Do not run the macOS menubar app and `./netllm serve` together; both bind `:11400`. Before quitting the app, use **Stop** so the agent subprocess exits; otherwise an orphan can hold `:11400` and block the next launch.
- oMLX discovery probes `:8080` by default; backends on other ports need `[discovery].custom_endpoints` or `[[routing.backends]]` in `~/.config/netllm/config.toml`.
- macOS menubar install/update: **recommended on macOS 26+:** clone release tag → `apps/netllm-mac/Scripts/build.sh release` → `packaging/scripts/macos-app-install.sh --source apps/netllm-mac/build/Stage/llm-swarm-router.app` ([docs/macos-install.md](docs/macos-install.md)); GitHub DMG + menubar **Updates** when notarized; bundled `macos-app-install.sh` under `Contents/Resources/Scripts/`; `scripts/upgrade-mac-app.sh` is repo-only; in-app update stops agent via `--in-app-update`, logs under `~/Library/Application Support/netllm/logs/`; **v0.3.0.2** fixes menubar **Agent: starting…** when `listen = "0.0.0.0:11400"` — [docs/release-notes/v0.3.0.2.md](docs/release-notes/v0.3.0.2.md)
- **macOS Gatekeeper (26+):** ad-hoc GitHub DMGs fail launch (`no usable signature`); user docs point to **source build + install script** until notarized Developer ID releases ship — [docs/macos-code-signing.md](docs/macos-code-signing.md), [docs/macos-install.md](docs/macos-install.md). Personal Apple Developer account (matthewdcage@gmail.com) is active as of Jun 2026: Developer ID signing needs the `MACOS_CERTIFICATE_P12` GitHub secret + notarization app-specific password; cert exports and that password stay in 1Password / local gitignored `.env`, never in the repo
- Release tag must match root `pyproject.toml` version; bump all workspace packages + `uv lock` before `gh release create`
- `.cursor/coordinator/` is gitignored local PR overseer (state, drafts, scripts); orchestration via seven tracked **Cursor subagents** in `.cursor/agents/coordinator-*.md` (incl. **prospector**, **telegram-runner**) — [`.cursor/coordinator/AGENTS.md`](.cursor/coordinator/AGENTS.md); run `run-coordinator-pass.sh` + `test-coordinator-loop.sh` before paste work; `test-discovery.sh` for offline discover-all smoke
- `.cursor/outreach/` is gitignored local outreach research/drafts; paste-ready convention: **Target:** / **Title:** / body after `---` (plain markdown, no YAML); neither tree belongs in the remote repo
- Prefer posted voice examples in `.cursor/coordinator/examples/` (`examples/index.json`) over generic `voice.md` when drafting community replies
- Stagehand fork for browser automation lives outside this repo (maintainer-local path in `.cursor/coordinator/state/browser-profiles.json`; example checkout: `/path/to/agent-stagehand-browser-agent`); coordinator resolves it via `browse-env.sh`; `browse` CLI via `npm link` in that repo's `packages/cli`
- Reddit automation login persists under `~/.cursor/chrome-automation/reddit/Default/` (Chrome user-data-dir layout); seed with `browser-seed-profile.sh reddit --force`, verify with `verify-reddit-session.sh --apply` — daily Profile 5 login alone does not wire through unless seeded
- Work automation login (matthew@hydradigital.com.au / GitHub @matthewdcage) persists under `~/.cursor/chrome-automation/work/Default/`; seed with `browser-seed-profile.sh work --force`, login with `browser-login-work.sh`, verify with `verify-work-session.sh --apply` — daily Default profile login alone does not wire through unless seeded
- Headed outreach: **browse CLI** primary; `browser-ensure-up.sh` → `browse-session-lifecycle.sh ensure` (Chrome preserved, `state/browse-session-registry.json`); after manual Post run `clear-pending`; `test-coordinator-loop.sh` **config-only by default** (`BROWSE_E2E_LIVE=1` for live paste prep; `BROWSE_E2E_SMOKE=1` optional example.com); coordinator subagents can run **in parallel** when profiles differ (reddit `:9224` vs work `:9223`); **`stagehand-local` MCP fallback only** when browse refs fail
- Outreach prospector: weekly Monday pass via `coordinator-prospector` + `discover-all.sh` + `qualify-prospector-candidates.sh` (live HTTP before approval; channels include reddit, HN, **github_issue** via `discover-github-issues.sh`); **Recommend approve** only when `state/prospector-qualification.json` shows `ok` + `live_checked`; promote with `promote-candidates.sh --apply-approved` only after Matthew approves summary rows; then draft replies from `examples/index.json` before browser paste prep
- HN reply-first: `HN_MAX_AGE_DAYS` default **14** (Algolia discovery window matches); bare **Sorry.** on item page = comment closed; dead ids in `.cursor/coordinator/state/hn-dead-threads.json`. Reddit: `REDDIT_MAX_AGE_DAYS` default 365 for new discovery; deleted threads in `reddit-dead-threads.json`
- YouTube creator outreach: registry `.cursor/coordinator/index/youtube-creators.json` (700+ channels from Matthew's paid account matt@activ8.com.au) built by `build-youtube-creators.sh` from sources CSV + curated overlay; engagement ladder (comment → heads_up → collab_demo → interview) in coordinator `voice.md`; per-creator `engagement_ceiling` is binding; video discovery needs `yt-dlp` (Homebrew)
- Coordinator Telegram: outbound `notify-telegram.sh` → DreamsofsoaringAiBot DM (token in hermes `.env` outside repo); inbound bot agent via `telegram-agent-poll.sh` when `TELEGRAM_AGENT=1` (session JSONL by day in `state/telegram-sessions/`); Cursor `composer-2.5` local first, OMLX `gemma-4-26B-A4B-it-assistant` at `:8080` fallback; post/paste requests send **screenshot + Approve/Deny** before any submit; `telegram-local` MCP stays optional for interactive MTProto in Cursor

## Child DOX Index

| Path | Contract |
|------|----------|
| [`packages/AGENTS.md`](packages/AGENTS.md) | Python uv workspace (6 packages) |
| [`apps/AGENTS.md`](apps/AGENTS.md) | Native platform apps |
| [`docs/AGENTS.md`](docs/AGENTS.md) | User-facing guides and release notes |
| [`packaging/AGENTS.md`](packaging/AGENTS.md) | Cross-platform release builds |
| [`tests/AGENTS.md`](tests/AGENTS.md) | Cross-package integration tests |
| [`.agents/AGENTS.md`](.agents/AGENTS.md) | Canonical agent skills (sync to tool paths) |
| [`.cursor/agents/AGENTS.md`](.cursor/agents/AGENTS.md) | DOX protocol + coordinator subagent index |
| [`.cursor/coordinator/AGENTS.md`](.cursor/coordinator/AGENTS.md) | Local PR coordinator (gitignored): scripts, state, browse-first stack |

Updated: 2026-06-11 (v0.4.0.1 open trusted-LAN swarm default; optional secured token via menubar/CLI; Telegram bot DM bridge)
