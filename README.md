<p align="center">

```
 ███████╗██╗    ██╗ █████╗ ██████╗ ███╗   ███╗      ██╗     ██╗     ███╗   ███╗
 ██╔════╝██║    ██║██╔══██╗██╔══██╗████╗ ████║      ██║     ██║     ████╗ ████║
 ███████╗██║ █╗ ██║███████║██████╔╝██╔████╔██║█████╗██║     ██║     ██╔████╔██║
 ╚════██║██║███╗██║██╔══██║██╔══██╗██║╚██╔╝██║╚════╝██║     ██║     ██║╚██╔╝██║
 ███████║╚███╔███╔╝██║  ██║██║  ██║██║ ╚═╝ ██║      ███████╗███████╗██║ ╚═╝ ██║
 ╚══════╝ ╚══╝╚══╝ ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝     ╚═╝      ╚══════╝╚══════╝╚═╝     ╚═╝

         ·  s w a r m   r o u t e r  ·
```

</p>

# llm-swarm-router

<p align="center">
  <a href="AGENTS.md"><img src="https://img.shields.io/badge/version-0.2.0-blue?style=for-the-badge" alt="Version 0.2.0"></a>
  <a href="docs/menubar-app.md"><img src="https://img.shields.io/badge/macOS-Menubar%20app-000000?style=for-the-badge&logo=apple&logoColor=white" alt="macOS app"></a>
  <a href="docs/editor-integration.md"><img src="https://img.shields.io/badge/Docs-Editor%20wiring-FFD700?style=for-the-badge" alt="Editor integration"></a>
  <a href="./LICENSE"><img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License: MIT"></a>
  <a href="CONTRIBUTING.md"><img src="https://img.shields.io/badge/PRs-welcome-brightgreen?style=for-the-badge" alt="PRs welcome"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.11+"></a>
  <a href="https://platform.openai.com/docs/api-reference"><img src="https://img.shields.io/badge/API-OpenAI%20%2B%20Anthropic-412991?style=for-the-badge&logo=openai&logoColor=white" alt="OpenAI + Anthropic APIs"></a>
</p>

**Put every machine's GPU to work — without babysitting URLs.**

**The mesh router for local LLM backends.** Run a lightweight agent on each computer — it finds oMLX, Ollama, and LM Studio on localhost, discovers sibling agents on your LAN, and exposes one stable endpoint for every editor and tool.

<p align="center">
  <img src="assets/screenshots/llm-swarm-router-osx-settings.png" alt="llm-swarm-router Settings — backends online, swarm peers connected, routed model catalog" width="720">
  <br>
  <em>One glance: backends, peers, and models — live on your network.</em>
</p>

### Why I built it

Local AI API servers are excellent on a single machine. oMLX on Apple Silicon, Ollama on a Mac mini, LM Studio on a laptop — each is optimized for its own hardware. But they do not naturally form a mesh across a diverse home lab. Promising projects are on the horizon; I wanted **reliable performance today** — my hardware working for local coding agents **24/7**, without me continuously managing ports, failover lists, and per-repo infrastructure.

I had compute sitting idle and no simple way to log in and use it. Every editor wanted a different `base_url`. Every machine was an island. That is why I built **llm-swarm-router**.

### Who it is for

- **Multi-Mac households and small labs** — MacBook + Mac mini + studio, each running different backends on different ports.
- **Agent-heavy developers** — Cursor, Claude Code, Codex, Honcho, or any OpenAI/Anthropic client that should hit *your* GPUs, not a cloud tab.
- **People who want throughput to scale with hardware** — add a node, install the app, and the mesh picks up more models and capacity without re-wiring every project.

### What it is

A **simple-to-install, simple-to-use LLM mesh coordinator** for macOS, Linux, and Windows. Each node runs **independently and with every other node**: auto-detects **oMLX** (macOS), **Ollama**, **LM Studio**, and **vLLM** (custom server URLs supported), advertises itself on the network, discovers remote agents and their models, and **distributes inference requests** across the swarm.

| API | Base URL |
|-----|----------|
| OpenAI-compatible | `http://<host>:11400/v1` |
| Anthropic Messages | `http://<host>:11400` (translated to local backends) |

Point **Cursor**, **Claude Code**, **Codex**, **Honcho**, or any compatible client at that URL. No cloud hop, no per-repo failover URLs, no lock-in to a single backend.

---

## Platform status

| Platform | Status | Install |
|----------|--------|---------|
| **macOS** (Apple Silicon) | **Available** — native menubar app + CLI | [DMG from Releases](https://github.com/matthewdcage/llm-swarm-router/releases) or build below |
| **macOS / Linux** (dev) | **Available** — Python agent + CLI from source | `uv sync` + `./netllm serve` |
| **Linux** (desktop) | **Available** — systemd user service, `.deb` / `.rpm` | [docs/linux-install.md](docs/linux-install.md) |
| **Windows** | **Available** — portable zip + Windows service | [docs/windows-install.md](docs/windows-install.md) |

Linux and Windows run the **agent and CLI from source** or packaged installs (deb/rpm/zip). All platforms share the **web dashboard** at http://127.0.0.1:11400/ui/. macOS adds a native menubar app. See [docs/platform-matrix.md](docs/platform-matrix.md).

---

## macOS — install in one step

**Recommended for Mac users:** download, drag, open. No terminal setup required.

1. Download **`llm-swarm-router.dmg`** from [GitHub Releases](https://github.com/matthewdcage/llm-swarm-router/releases).
2. Open the DMG and drag **llm-swarm-router** to **Applications**.
3. Launch from Applications. The bee icon appears in the menu bar.
4. Complete the short welcome wizard (optional LAN mode, auto-start agent).

<p align="center">
  <img src="assets/screenshots/llm-swarm-router-osx-menu.png" alt="llm-swarm-router menubar menu showing agent status, routing stats, and Settings" width="320">
  <br>
  <em>Menubar — agent status, start/stop, Open Dashboard, copy client env, and Settings (⌘,).</em>
</p>

The app **starts the agent automatically**, **scans for local providers** (oMLX, Ollama, LM Studio), and **persists discovered URLs** to `~/.config/netllm/config.toml`. You do not need to run `netllm discover` manually.

If macOS blocks the first launch: right-click the app in Applications → **Open** once.

**Terminal:** After first launch, a CLI shim is available at `~/.config/netllm/bin/netllm` (`netllm status`, `netllm models`, etc.).

More detail: [docs/menubar-app.md](docs/menubar-app.md)

### Optional: Homebrew

```bash
brew tap matthewdcage/netllm https://github.com/matthewdcage/llm-swarm-router
brew install netllm
brew services start netllm
```

---

## CLI / source install (macOS & Linux)

For development, CI, or Linux hosts without the menubar app:

```bash
git clone https://github.com/matthewdcage/llm-swarm-router.git
cd llm-swarm-router
uv sync
./netllm init
./netllm serve          # agent on http://127.0.0.1:11400
```

The `./netllm` wrapper works **immediately** from the repo — no global install required.

**Verify** (second terminal, while `serve` is running):

```bash
scripts/agent-verify-setup.sh
export OPENAI_BASE_URL=http://127.0.0.1:11400/v1
export OPENAI_API_KEY=netllm-local
```

**Global CLI (optional):** `./netllm install` → `netllm` on PATH via `uv tool install`.

**Agent-assisted setup** (Cursor, Codex, Claude Code in this repo): `/netllm-setup` and `/netllm-connect` — see [AGENTS.md](AGENTS.md) and [docs/editor-integration.md](docs/editor-integration.md).

---

## Wire your editor

```bash
export OPENAI_BASE_URL=http://127.0.0.1:11400/v1
export OPENAI_API_KEY=netllm-local
```

**Claude Code / native Anthropic clients:**

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:11400
export ANTHROPIC_API_KEY=netllm-local
```

Pick a model ID from `./netllm models` (or the app **Settings → Models** tab). Full per-editor steps: [docs/editor-integration.md](docs/editor-integration.md).

---

## What you get

<table>
<tr><td><b>Native macOS app</b></td><td>Drag-to-Applications install. Menubar supervisor, welcome wizard, full Settings UI (status, backends, models, peers, routing, discovery, doctor). Embeds the Python agent — no separate <code>uv</code> setup for end users.</td></tr>
<tr><td><b>Zero-touch local discovery</b></td><td>Agent scans on every start — oMLX (<code>:8080</code>, <code>:8088</code>, <code>:8081</code>), Ollama (<code>:11434</code>), LM Studio (<code>:1234</code>). Per-machine overrides in Settings or <code>discovery.provider_urls</code>. Found URLs persist automatically.</td></tr>
<tr><td><b>Network-wide model catalog</b></td><td>See local and LAN models in one place (<code>netllm models --lan</code> or Settings → Models). Peers advertise what they can run; routing follows your strategy.</td></tr>
<tr><td><b>Throughput that grows with the mesh</b></td><td>Each node is an independent peer. More machines with the same models installed → more capacity for <code>round_robin</code>, <code>least_load</code>, <code>latency_weighted</code>, and <code>batch_shard</code> workloads.</td></tr>
<tr><td><b>Dual API surface</b></td><td>OpenAI chat/models/streaming plus Anthropic Messages API with translation to local backends — one URL for every editor.</td></tr>
<tr><td><b>LAN swarm</b></td><td>mDNS (<code>_netllm._tcp</code>), subnet scan, and static <code>swarm.peers</code> for home labs, guest Wi‑Fi, and fixed IPs.</td></tr>
<tr><td><b>Set-and-forget operation</b></td><td>Auto-start on launch, health cache, circuit breaker, <code>netllm doctor</code>, Prometheus <code>/metrics</code> — built to run 24/7 without hand-holding.</td></tr>
<tr><td><b>Honcho-ready</b></td><td>Drop-in mesh router for Honcho deriver/dialectic flows — <a href="docs/honcho-integration.md">Honcho integration</a>.</td></tr>
<tr><td><b>Agent-native docs</b></td><td><a href="AGENTS.md">AGENTS.md</a>, <a href=".agents/skills/">skills</a>, Claude Code slash commands for setup, connect, swarm, and troubleshoot.</td></tr>
</table>

---

## How the swarm fits together

```
  ┌──────────────┐   mDNS (_netllm._tcp)   ┌──────────────┐
  │  MacBook     │◄───────────────────────►│  Mac mini    │
  │  llm-swarm-  │                         │  llm-swarm-  │
  │  router app  │                         │  router app  │
  └───┬──────┬───┘                         └───┬──────┬───┘
      │      │                                 │      │
   oMLX   Ollama                           oMLX   Ollama
  :8080+  :11434                         :8088   :11434
      │      │                                 │      │
      └──────┴─────────── :11400/v1 ───────────┴──────┘
                         │
              Cursor · Claude Code · Honcho · curl
```

| Discovery | When |
|-----------|------|
| **Automatic** | Agent scans on every start (app or `netllm serve`) |
| **mDNS** | Default on home/office LAN (`serve --host 0.0.0.0`) |
| **Subnet scan** | `netllm peers --subnet-scan` when multicast is blocked |
| **Manual** | `swarm.peers` in config, Settings, or `peers --save` |

Config: `~/.config/netllm/config.toml` — see [config.example.toml](config.example.toml).

---

## CLI quick reference

```bash
./netllm serve             # foreground agent (dev/CI/Linux)
./netllm serve --host 0.0.0.0   # LAN + swarm
./netllm start|stop|restart     # background (macOS app / Homebrew)
./netllm status            # backends, health, peers
./netllm models            # routed catalog
./netllm models --lan      # include remote LAN agents
./netllm peers             # mDNS browse
./netllm discover          # manual rescan (optional; agent auto-discovers)
./netllm test              # 1-token latency probe
./netllm test --api anthropic
./netllm gateway           # promote to LAN entrypoint
./netllm doctor            # PATH, mDNS, port, backend checks
```

---

## Build the macOS app (developers)

Requirements: macOS 15+, Apple Silicon, [uv](https://docs.astral.sh/uv/), Xcode CLT or full Xcode (actool optional — iconutil fallback on CLT-only Macs).

```bash
uv sync
apps/netllm-mac/Scripts/build.sh release
packaging/scripts/create-dmg.sh    # → dist/llm-swarm-router.dmg
```

**Test like an end user** (build → Applications → launch):

```bash
./scripts/emulate-user-install-mac.sh
```

Python layer packaging (venvstacks): [packaging/README.md](packaging/README.md)

---

## Packages

| Package | Role |
|---------|------|
| **netllm-core** | Routing, health cache, config |
| **netllm-sdk-openai** | OpenAI SDK upstream adapter |
| **netllm-sdk-anthropic** | Anthropic SDK upstream adapter |
| **netllm-discovery** | Local scan, swarm registry, mDNS |
| **netllm-agent** | FastAPI — `/v1/*`, `/netllm/v1/*`, `/metrics` |
| **netllm-cli** | Typer CLI |
| **netllm-mac** | Native macOS menubar app (`apps/netllm-mac/`) |

Architecture: [docs/architecture-reference.md](docs/architecture-reference.md)

---

## Development

```bash
uv sync
uv run pytest tests/ -v
uv run ruff check packages/ tests/
```

Optional pre-commit hooks (same ruff rules as CI):

```bash
uv run pre-commit install
uv run pre-commit run --all-files
```

After editing agent skills: `scripts/sync-agent-skills.sh`

Human contributor guide: [CONTRIBUTING.md](CONTRIBUTING.md) · Agent context: [AGENTS.md](AGENTS.md)

---

## Contributing

**llm-swarm-router** is an open-source community project. We welcome bug reports, documentation improvements, platform fixes, and feature PRs.

| Action | Link |
|--------|------|
| Report a bug | [Bug report template](https://github.com/matthewdcage/llm-swarm-router/issues/new?template=bug_report.yml) |
| Suggest a feature | [Feature request template](https://github.com/matthewdcage/llm-swarm-router/issues/new?template=feature_request.yml) |
| Development setup & PR workflow | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Code of conduct | [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) |
| Security disclosure | [SECURITY.md](SECURITY.md) |
| Help & docs index | [SUPPORT.md](SUPPORT.md) |

**Quick start for contributors:**

```bash
git clone https://github.com/matthewdcage/llm-swarm-router.git
cd llm-swarm-router
uv sync
./netllm init
uv run pytest tests/ -v && uv run ruff check packages/ tests/
```

Fork → branch → PR against `main`. Use [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`). CI runs on Ubuntu and Windows.

---

## License

MIT — see [LICENSE](LICENSE). Copyright (c) netllm contributors.
