# macOS troubleshooting: netllm

Install guide: [macos-install.md](macos-install.md) · Overview: [platform-matrix.md](platform-matrix.md)

## First steps (all issues)

```bash
netllm doctor
curl -sf http://127.0.0.1:11400/health
netllm status
```

Dashboard: http://127.0.0.1:11400/ui/ · menubar **Open Dashboard** or **Copy Client Env**.

### `/ui/` returns `{"detail":"Not Found"}`

Usually an **old agent still owns the port** after a drag-to-Applications upgrade (the running process was not quit before replace).

```bash
# Clean upgrade from a downloaded release DMG
./scripts/upgrade-mac-app.sh ~/Downloads/llm-swarm-router.dmg
```

Or manually: menubar **Quit** → `brew services stop netllm` (if used) → confirm `lsof -i :11400` is empty → relaunch from Applications.

Diagnostic:

```bash
curl -s http://127.0.0.1:11400/ | python3 -m json.tool   # v0.2.3+ includes "dashboard"
lsof -nP -iTCP:11400 -sTCP:LISTEN
defaults read /Applications/llm-swarm-router.app/Contents/Info CFBundleShortVersionString
```

From a repo checkout, prefer `./netllm` if PATH is uncertain.

---

## Agent unreachable

| Symptom | Fix |
|---------|-----|
| `curl` to `/health` fails | Menubar → **Start Agent**, or `netllm start` |
| Port 11400 in use | Expected while the menubar app runs the agent. Settings → **Restart Agent** or `netllm restart`: not a second `netllm serve`. After upgrade, run `upgrade-mac-app.sh` if an old process still holds the port |
| DMG app won’t launch | Right-click **llm-swarm-router** in Applications → **Open** once (Gatekeeper) |
| Homebrew agent down | `brew services restart netllm` · logs: `$(brew --prefix)/var/log/netllm.log` |

**Verify:** menubar shows “Agent: running” and `curl -sf http://127.0.0.1:11400/health`.

---

## No models / backends offline

| Symptom | Fix |
|---------|-----|
| `netllm models` empty | Start **oMLX**, **Ollama**, or **LM Studio** locally |
| Discovery missed a port | Settings → **Discovery** → **Refresh provider scan**, or `netllm discover` |
| oMLX on non-default port | Settings → Discovery, or `discovery.provider_urls` in `~/.config/netllm/config.toml` |

Default probes: oMLX `:8080` / `:8088` / `:8081`, Ollama `:11434`, LM Studio `:1234`, vLLM `:8000`.

**Verify:** `netllm discover` then `netllm models`.

---

## CLI not found

| Install channel | Fix |
|-----------------|-----|
| **DMG app** | Shim at `~/.config/netllm/bin/netllm`: add to PATH or use full path |
| **Homebrew** | `which netllm` should show Homebrew prefix |
| **Source** | Run `./netllm` from repo root or `./netllm install` |

DMG/menubar installs: `netllm doctor` does not require a global CLI on PATH.

---

## Swarm / LAN peers

| Symptom | Fix |
|---------|-----|
| `netllm peers` empty | Use `netllm serve --host 0.0.0.0` (or enable LAN in welcome wizard). Set `swarm.cluster_token` on untrusted networks |
| mDNS browse fails | Source installs: `uv sync` (zeroconf). Fallback: `netllm peers --subnet-scan --save` or static `swarm.peers` |
| Guest Wi‑Fi | mDNS often blocked: use static peers |

**Verify:** `netllm peers` and `netllm models --lan`.

---

## Editor connects but requests fail

1. Menubar **Copy Client Env**, or export:
   `OPENAI_BASE_URL=http://127.0.0.1:11400/v1` · `OPENAI_API_KEY=netllm-local` · same host for `ANTHROPIC_*`.
2. Model name must match `netllm models` exactly.
3. [editor-integration.md](editor-integration.md).

---

## Still stuck?

```bash
netllm doctor
netllm test
scripts/agent-verify-setup.sh   # from repo root
```

Report a bug: [GitHub issues](https://github.com/matthewdcage/llm-swarm-router/issues/new?template=bug_report.yml) (include macOS version, install channel, `./netllm doctor` output, redact tokens).

Other platforms: [linux-troubleshooting.md](linux-troubleshooting.md) · [windows-troubleshooting.md](windows-troubleshooting.md)
