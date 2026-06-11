# macOS troubleshooting: netllm

Install guide: [macos-install.md](macos-install.md) · Overview: [platform-matrix.md](platform-matrix.md)

## First steps (all issues)

```bash
netllm doctor
curl -sf http://127.0.0.1:11400/health
netllm status
tail -20 ~/Library/Application\ Support/netllm/logs/app.log   # menubar native launch (v0.2.3.6+)
```

Dashboard: http://127.0.0.1:11400/ui/ · menubar **Open Dashboard** or **Copy Client Env**.

### `/ui/` returns `{"detail":"Not Found"}`

Usually an **old agent still owns the port** after a drag-to-Applications upgrade (the running process was not quit before replace).

```bash
# Recommended upgrade (macOS 26+): rebuild and install from source
cd /path/to/llm-swarm-router && git checkout v0.3.0.2
uv sync && uv pip install venvstacks
apps/netllm-mac/Scripts/build.sh release
packaging/scripts/macos-app-install.sh --source apps/netllm-mac/build/Stage/llm-swarm-router.app

# Or from a repo checkout with a downloaded DMG (after notarization):
# ./scripts/upgrade-mac-app.sh ~/Downloads/llm-swarm-router.dmg
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

## Gatekeeper blocks install or launch

Release DMGs are **ad-hoc signed** until [Developer ID notarization](macos-code-signing.md) is enabled in CI. macOS may show *cannot verify free from malware* when you drag to **Applications**, or on first launch.

**macOS 26 (Tahoe) and later:** ad-hoc menubar apps are blocked (`no usable signature`). Clearing quarantine or mounting the DMG does **not** fix this. Use **build from source + install script** (recommended):

```bash
git clone https://github.com/matthewdcage/llm-swarm-router.git
cd llm-swarm-router && git checkout v0.3.0.2   # or latest tag
uv sync && uv pip install venvstacks
apps/netllm-mac/Scripts/build.sh release
packaging/scripts/macos-app-install.sh --source apps/netllm-mac/build/Stage/llm-swarm-router.app
```

**CLI only (no Gatekeeper issue with menubar):**

```bash
cd /path/to/llm-swarm-router
uv sync && ./netllm init && ./netllm serve
# Dashboard: http://127.0.0.1:11400/ui/
```

### After notarized DMGs ship

Use the bundled terminal installer (not drag-only):

**Upgrade** (app already installed):

```bash
INSTALLER="/Applications/llm-swarm-router.app/Contents/Resources/Scripts/macos-app-install.sh"
chmod +x "$INSTALLER"
"$INSTALLER" --dmg ~/Downloads/llm-swarm-router.dmg
```

**First install** from mounted DMG:

```bash
open ~/Downloads/llm-swarm-router.dmg
INSTALLER="/Volumes/llm-swarm-router/llm-swarm-router.app/Contents/Resources/Scripts/macos-app-install.sh"
chmod +x "$INSTALLER" "/Volumes/llm-swarm-router/llm-swarm-router.app/Contents/Resources/Scripts/mount-dmg.sh"
"$INSTALLER" --dmg ~/Downloads/llm-swarm-router.dmg
```

From a repo checkout:

```bash
cd /path/to/llm-swarm-router
./scripts/upgrade-mac-app.sh ~/Downloads/llm-swarm-router.dmg
```

**Maintainer: build a notarized DMG locally** (after Developer ID cert is in Keychain):

```bash
export APPLE_ID='you@example.com'
export APPLE_TEAM_ID='XXXXXXXXXX'
export APPLE_APP_SPECIFIC_PASSWORD='xxxx-xxxx-xxxx-xxxx'
packaging/scripts/local-notarized-dmg.sh
```

**Manual alternatives (older macOS or signed builds only):**

| Step | Action |
|------|--------|
| Clear download quarantine | `xattr -cr ~/Downloads/llm-swarm-router.dmg` then open the DMG and drag again |
| First launch only | Right-click **llm-swarm-router** in Applications → **Open** once (not double-click) |
| After a blocked attempt | **System Settings → Privacy & Security → Open Anyway** (appears after macOS blocks the app) |
| Avoid | Disabling Gatekeeper globally (`spctl --master-disable`) — not recommended |

**Homebrew** (`brew install netllm`) uses Apple's brew signing path and usually avoids this for the CLI agent; the menubar DMG is separate.

Maintainers: enable notarized releases per [macos-code-signing.md](macos-code-signing.md).

---

## Agent unreachable

| Symptom | Fix |
|---------|-----|
| `curl` to `/health` fails | Menubar → **Start Agent**, or `netllm start` |
| Port 11400 in use | Expected while the menubar app runs the agent. Settings → **Restart Agent** or `netllm restart`: not a second `netllm serve`. After upgrade, run [build + install script](macos-install.md#build-from-source--install-script-recommended-on-macos-26) or bundled `macos-app-install.sh` if an old process still holds the port |
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
| `netllm peers` empty | Guided: `netllm init --force --swarm` (first machine) + `netllm join URL --token T` (others). Manual: `netllm serve --host 0.0.0.0` (or enable LAN in welcome wizard) |
| Peer "found but unreachable" | That machine is loopback-bound — run the guided swarm init or `serve --host 0.0.0.0` there |
| Join rejected (401) | Cluster token mismatch — `netllm swarm-token` on a joined machine, re-run `join` |
| mDNS browse fails | LAN-bound agents auto-run one subnet scan after 10s. Also: **Subnet scan at startup** in menubar Settings → Swarm (or `subnet_scan = true`), `netllm peers --subnet-scan --save`, or static `swarm.peers` |
| Firewall suspicion | `netllm doctor` prints macOS firewall guidance (allow incoming for python/netllm) |
| Guest Wi‑Fi | mDNS often blocked: use static peers |
| Same model, different names per machine | Map once with `[routing.model_aliases]` in config |

**Web dashboard:** Prefer **Open Dashboard** from the menubar (`http://127.0.0.1:11400/ui/`). If you open `http://<LAN-IP>:11400/ui/` on the same Mac, admin tabs should work; from another machine on the LAN, status/models are read-only unless you set `swarm.cluster_token` and pass `Authorization: Bearer <token>`.

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
