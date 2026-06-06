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

# swarm-llm

<p align="center">
  <a href="docs/honcho-integration.md"><img src="https://img.shields.io/badge/Docs-Honcho%20integration-FFD700?style=for-the-badge" alt="Documentation"></a>
  <a href="./LICENSE"><img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License: MIT"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.11+"></a>
  <a href="https://github.com/astral-sh/uv"><img src="https://img.shields.io/badge/Built%20with-uv-DE5FE9?style=for-the-badge&logo=astral&logoColor=white" alt="uv"></a>
  <a href="https://platform.openai.com/docs/api-reference"><img src="https://img.shields.io/badge/API-OpenAI%20compatible-412991?style=for-the-badge&logo=openai&logoColor=white" alt="OpenAI compatible"></a>
</p>

**The mesh router for local LLM backends.** Run a lightweight agent on each machine — it discovers oMLX, Ollama, and LM Studio on localhost, finds sibling agents on your LAN via mDNS, and exposes a single **`http://<host>:11400/v1`** endpoint. Point Honcho, Cursor, Open WebUI, or any OpenAI client at it. No cloud hop, no API key collection, no copy-pasting failover URLs into every repo.

Use any backend you want — **oMLX** (Apple Silicon), **Ollama**, **LM Studio**, or a mix across several Macs. Switch routing with `netllm gateway` or a line in `config.toml` — no code changes, no lock-in. Your GPUs stay local; the API looks like OpenAI.

<table>
<tr><td><b>One URL for everything</b></td><td>Replace comma-separated <code>base_url</code> lists with <code>http://127.0.0.1:11400/v1</code>. Chat completions, model lists, and streaming — standard OpenAI shape.</td></tr>
<tr><td><b>Discovers what you already run</b></td><td>Probes <code>:8080</code> (oMLX), <code>:11434</code> (Ollama), and <code>:1234</code> (LM Studio). Health cache, circuit breaker, and per-backend model catalogs.</td></tr>
<tr><td><b>Swarm by default</b></td><td>Each host is an independent peer agent. mDNS (<code>_netllm._tcp</code>) finds neighbors; subnet scan and <code>swarm.peers</code> cover guest Wi‑Fi and static IPs.</td></tr>
<tr><td><b>Routing that matches the workload</b></td><td><code>local_first</code>, <code>failover</code>, <code>round_robin</code>, <code>least_load</code>, <code>latency_weighted</code> — Honcho-style pooling, extracted into standalone packages.</td></tr>
<tr><td><b>CLI that tells you what to run next</b></td><td>Rich status tables, <code>doctor</code> for misconfig, and serve hints for <code>--host 0.0.0.0</code>, <code>peers</code>, and <code>models --lan</code>. <code>./netllm</code> works from the repo without global PATH.</td></tr>
<tr><td><b>Honcho-ready</b></td><td>Drop-in for deriver, dialectic, dream, and connector enrichment — one router instead of embedded endpoint pools. See <a href="docs/honcho-integration.md">Honcho integration</a>.</td></tr>
<tr><td><b>Observable</b></td><td>Prometheus <code>/metrics</code>, per-backend in-flight counts, and <code>netllm test</code> for 1-token latency probes.</td></tr>
</table>

---

## Quick install

### macOS / Linux

```bash
git clone https://github.com/matthewdcage/llm-swam-router.git
cd llm-swam-router
uv sync
./netllm init
./netllm serve
```

The `./netllm` wrapper works **immediately** — no global install required.

### Global CLI (optional)

```bash
./netllm install   # uv tool install + ~/.local/bin in shell profile
```

New terminal tabs pick up `netllm` automatically; in the current tab, run the `export PATH=…` line if the installer prints one.

---

## Getting started

```bash
./netllm init              # config + scan local providers
./netllm serve             # agent on 127.0.0.1:11400
./netllm serve --host 0.0.0.0   # LAN — required for swarm peers
./netllm status            # backends, health, peers
./netllm models            # routed model catalog
./netllm models --lan      # include remote agents
./netllm peers             # mDNS browse (~3s)
./netllm discover          # probe oMLX / Ollama / LM Studio
./netllm test              # 1-token latency diagnose
./netllm gateway           # promote to LAN entrypoint
./netllm doctor            # PATH, mDNS, backend checks
```

Wire any OpenAI client:

```bash
export OPENAI_BASE_URL=http://127.0.0.1:11400/v1
export OPENAI_API_KEY=netllm-local
```

While `serve` is running (second terminal):

```bash
curl -sf http://127.0.0.1:11400/health && echo ok
./netllm status
./netllm models
```

---

## How the swarm fits together

```
  ┌──────────────┐   mDNS (_netllm._tcp)   ┌──────────────┐
  │  MacBook     │◄───────────────────────►│  Mac Studio  │
  │  netllm agent│                         │  netllm agent│
  └───┬──────┬───┘                         └───┬──────┬───┘
      │      │                                 │      │
   oMLX   Ollama                           oMLX   Ollama
   :8080  :11434                          :8080  :11434
      │      │                                 │      │
      └──────┴─────────── :11400/v1 ───────────┴──────┘
                         │
              Honcho · Cursor · Open WebUI · curl
```

| Discovery | When |
|-----------|------|
| **mDNS** | Default on home/office LAN |
| **Subnet scan** | `netllm peers --subnet-scan` when multicast is blocked |
| **Manual** | `swarm.peers` in config or `peers --save` |

Config: `~/.config/netllm/config.toml` — see `config.example.toml`.

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

Architecture notes: [docs/architecture-reference.md](docs/architecture-reference.md)

---

## Development

```bash
uv sync
uv run pytest tests/
uv run ruff check packages/
```

## License

MIT — see [LICENSE](LICENSE).
