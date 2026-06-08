# Linux install — netllm

Install the mesh router as a background **systemd user service** or run from source.

## Package install (recommended)

Download from [GitHub Releases](https://github.com/matthewdcage/llm-swarm-router/releases):

| Format | Install |
|--------|---------|
| `.deb` | `sudo dpkg -i netllm_*_amd64.deb` |
| `.rpm` | `sudo rpm -Uvh netllm-*.rpm` |

Enable and start the agent:

```bash
systemctl --user daemon-reload
systemctl --user enable --now netllm
netllm status
```

Logs: `journalctl --user -u netllm -f`

## Source install (development)

```bash
git clone https://github.com/matthewdcage/llm-swarm-router.git
cd llm-swarm-router
uv sync
./netllm init
./netllm serve
```

Default discovery on Linux probes **Ollama** (`:11434`), **LM Studio** (`:1234`), and **vLLM** (`:8000`). oMLX is macOS-only but can be added manually to `discovery.providers`.

## LAN swarm

```bash
./netllm serve --host 0.0.0.0
./netllm peers
```

mDNS uses Avahi via `python-zeroconf`. If browse is empty, set static peers in `config.toml` or run `netllm peers --subnet-scan`.

## Wire editors

```bash
export OPENAI_BASE_URL=http://127.0.0.1:11400/v1
export OPENAI_API_KEY=netllm-local
export ANTHROPIC_BASE_URL=http://127.0.0.1:11400
export ANTHROPIC_API_KEY=netllm-local
```

See [editor-integration.md](editor-integration.md).
