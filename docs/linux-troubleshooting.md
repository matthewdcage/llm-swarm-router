# Linux troubleshooting: netllm

Install guide: [linux-install.md](linux-install.md) · Overview: [platform-matrix.md](platform-matrix.md)

## First steps (all issues)

```bash
netllm doctor
curl -sf http://127.0.0.1:11400/health
netllm status
```

Dashboard: http://127.0.0.1:11400/ui/ · CLI fallback: `netllm status`.

From a repo checkout, use `./netllm` from the project root.

---

## Agent unreachable

| Symptom | Fix |
|---------|-----|
| `curl` to `/health` fails | **Packaged:** `systemctl --user enable --now netllm` then `netllm status` |
| Service not running | `journalctl --user -u netllm -f` for errors |
| **Source install** | `./netllm serve` in a terminal (foreground) |
| Port 11400 in use | `ss -ltnp \| grep 11400` or `netllm doctor`: stop duplicate agent with `netllm stop` or kill stale process |

Logs: `journalctl --user -u netllm -f` and `~/.local/state/netllm/logs/agent.log`.

**Verify:** `curl -sf http://127.0.0.1:11400/health`.

---

## systemd user service issues

| Symptom | Fix |
|---------|-----|
| `systemctl --user` commands fail | Ensure lingering is enabled for your user, or run from a graphical session |
| Unit not found after deb install | `systemctl --user daemon-reload` |
| Service starts then exits | Check `journalctl --user -u netllm -n 50`: often missing config; run `netllm init` |

---

## No models / backends offline

| Symptom | Fix |
|---------|-----|
| `netllm models` empty | Start **Ollama**, **LM Studio**, or **vLLM** on this host |
| Wrong ports | Linux defaults omit oMLX. Probes: Ollama `:11434`, LM Studio `:1234`, vLLM `:8000` |
| Stale config | `netllm discover` or dashboard **Discover providers** |

**Verify:** `netllm discover` && `netllm models`.

---

## CLI not found

| Install channel | Fix |
|-----------------|-----|
| **deb/rpm** | `which netllm` → `/usr/bin/netllm` |
| **uv tool / source** | `./netllm install` or `./netllm` from repo |
| Wrong binary on PATH | `netllm doctor` reports global vs repo mismatch |

---

## Swarm / LAN peers

| Symptom | Fix |
|---------|-----|
| `netllm peers` empty | Guided: `netllm init --swarm` / `netllm join URL --token T`. Manual: `netllm serve --host 0.0.0.0` |
| Peer "found but unreachable" | That machine is loopback-bound — guided swarm init or `serve --host 0.0.0.0` there |
| mDNS browse fails | Install **Avahi** (`avahi-daemon`). Firewall: `sudo firewall-cmd --permanent --add-service=mdns --add-port=11400/tcp && sudo firewall-cmd --reload` (or `sudo ufw allow 5353/udp && sudo ufw allow 11400/tcp`). `netllm doctor` prints these |
| Still no peers | LAN-bound agents auto-run one subnet scan after 10s; else `netllm peers --subnet-scan --save` or add URLs to `swarm.peers` in config |
| Join rejected (401) | Cluster token mismatch — `netllm swarm-token` on a joined machine, re-run `join` |

**Verify:** `netllm peers` and `netllm models --lan`.

---

## WSL / remote inference

oMLX is macOS-only. On Linux use Ollama, LM Studio, or vLLM. Run `netllm serve` on the same machine where inference listens on localhost.

---

## Editor connects but requests fail

1. Export `OPENAI_BASE_URL=http://127.0.0.1:11400/v1`, `OPENAI_API_KEY=netllm-local`, and matching `ANTHROPIC_*` vars.
2. Match model IDs from `netllm models`.
3. [editor-integration.md](editor-integration.md).

---

## Still stuck?

```bash
netllm doctor
netllm test
./scripts/agent-verify-setup.sh   # repo checkout
```

Maintainer rehearsal: `./scripts/emulate-user-install-linux.sh`

Bug reports: [GitHub issues](https://github.com/matthewdcage/llm-swarm-router/issues/new?template=bug_report.yml).

Other platforms: [macos-troubleshooting.md](macos-troubleshooting.md) · [windows-troubleshooting.md](windows-troubleshooting.md)
