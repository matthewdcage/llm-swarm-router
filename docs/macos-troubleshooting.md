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
# DMG-only install (no git clone): bundled installer in the app
INSTALLER="/Applications/llm-swarm-router.app/Contents/Resources/Scripts/macos-app-install.sh"
chmod +x "$INSTALLER"
"$INSTALLER" --dmg ~/Downloads/llm-swarm-router.dmg

# Or from a repo checkout:
cd /path/to/llm-swarm-router
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

## Gatekeeper blocks install or launch

Release DMGs are **ad-hoc signed** until [Developer ID notarization](macos-code-signing.md) is enabled in CI. macOS may show *cannot verify free from malware* when you drag to **Applications**, or on first launch.

**macOS 26 (Tahoe) and later:** clearing quarantine or using the terminal installer is **not enough** for ad-hoc builds. Gatekeeper reports `no usable signature` and blocks launch (dialog with only **Done**). You need a **notarized Developer ID** DMG, or use the source CLI path below.

**Recommended:** use the bundled terminal installer (fixes copy + port cleanup; does not bypass Gatekeeper on Tahoe for ad-hoc builds):

**Upgrade** (app already installed):

```bash
# Eject stale DMG mounts first if install says mount failed
hdiutil detach "/Volumes/llm-swarm-router" -quiet 2>/dev/null || true
hdiutil detach "/Volumes/llm-swarm-router 1" -quiet 2>/dev/null || true

INSTALLER="/Applications/llm-swarm-router.app/Contents/Resources/Scripts/macos-app-install.sh"
chmod +x "$INSTALLER"
"$INSTALLER" --dmg ~/Downloads/llm-swarm-router.dmg
```

**First install** (no app in Applications yet):

```bash
open ~/Downloads/llm-swarm-router.dmg
INSTALLER="/Volumes/llm-swarm-router/llm-swarm-router.app/Contents/Resources/Scripts/macos-app-install.sh"
chmod +x "$INSTALLER" "/Volumes/llm-swarm-router/llm-swarm-router.app/Contents/Resources/Scripts/mount-dmg.sh"
"$INSTALLER" --dmg ~/Downloads/llm-swarm-router.dmg
```

If mount fails, install from the mounted volume without re-mounting:

```bash
open ~/Downloads/llm-swarm-router.dmg
"$INSTALLER" --source "/Volumes/llm-swarm-router/llm-swarm-router.app"
```

**Run without the menubar app (works today on ad-hoc / Gatekeeper-blocked Macs):**

```bash
cd /path/to/llm-swarm-router   # git clone
uv sync && ./netllm init && ./netllm serve
# Dashboard: http://127.0.0.1:11400/ui/
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
| Port 11400 in use | Expected while the menubar app runs the agent. Settings → **Restart Agent** or `netllm restart`: not a second `netllm serve`. After upgrade, run the bundled `macos-app-install.sh` or [repo upgrade script](macos-install.md#upgrade-from-a-release-dmg-clean-recommended) if an old process still holds the port |
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
