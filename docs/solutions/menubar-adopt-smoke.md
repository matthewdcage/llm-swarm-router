# Menubar adopt manual smoke checklist

Manual gate for **PR #43** (v0.4.5.1+): when a healthy netllm agent is already listening on `:11400`, the menubar app must **adopt** it and show a consistent running state everywhere — header, Start/Stop menu, and Settings hero.

**Bug class (pre-#43):** header showed **Agent stopped** while **Stop Agent** was visible after `adoptHealthyListener` on launch.

**Automated coverage:** Swift unit tests for `settingsStatusLabel` and `statusTitle`; no GUI test for header ↔ menu alignment. Run this checklist before tagging when menubar supervisor code changes.

Related: [macOS release readiness](macos-release-readiness.md) · [v0.4.5.1 release notes](../release-notes/v0.4.5.1.md)

---

## Preconditions

| Requirement | Check |
|-------------|-------|
| macOS host with Stage or `/Applications` build | `apps/netllm-mac/Scripts/build.sh release` or installed `.app` |
| Menubar app **not** running | Activity Monitor: quit `llm-swarm-router` / `netllm-mac` |
| Port free before Path A setup | `lsof -i :11400` → empty |
| Config present | `~/.config/netllm/config.toml` (from `./netllm init`) |
| Health endpoint works when agent up | `curl -sf http://127.0.0.1:11400/health` |

**Do not** leave both `./netllm serve` and the menubar app supervising concurrently except during the adopt window under test — quit one cleanly before the next scenario.

---

## Pass criteria (both paths)

After adopt completes (within ~2 s of menubar launch or **Start Agent**):

| Surface | Expected | Fail signal |
|---------|----------|-------------|
| Menubar header | **Agent running · :11400** (green) | **Agent stopped** while port is healthy |
| Menubar menu | **Stop Agent** visible; **Start Agent** hidden | Start visible while `/health` returns 200 |
| Settings → Status hero | **Running** | **Stopped** or **Failed** while `/health` returns 200 |
| CLI | `curl -sf http://127.0.0.1:11400/health` → `ok` | Health fails or wrong PID killed on adopt |

Optional: open **Settings → Status** and confirm live poll (2 s) keeps **Running** without flicker to **Stopped**.

---

## Path A — CLI orphan, menubar launch adopt

Simulates dev/CLI workflow: agent started outside the menubar supervisor, then the app opens and adopts on launch.

### Setup

1. Confirm menubar app is quit and port is free:
   ```bash
   lsof -i :11400
   ```
2. From repo root, start a foreground orphan agent (no `NETLLM_SUPERVISED=menubar`):
   ```bash
   cd /path/to/llm-swarm-router
   ./netllm serve
   ```
3. In another terminal, verify health:
   ```bash
   curl -sf http://127.0.0.1:11400/health && echo ok
   ```

### Exercise

4. Launch menubar app (pick one):
   ```bash
   open apps/netllm-mac/build/Stage/llm-swarm-router.app
   # or
   open /Applications/llm-swarm-router.app
   ```
5. Wait for launch reconcile (`reconcileListeningPort(adoptOrphan:)` when `autoStartOnLaunch` is enabled — default after welcome).

### Verify

6. Click menubar icon → check header text and Start/Stop items against **Pass criteria**.
7. **Settings…** → **Status** tab → hero label **Running**.
8. **Stop Agent** from menubar → agent exits, port frees, header **Agent stopped**, menu shows **Start Agent**.
9. Quit menubar app (`Cmd+Q`) → `lsof -i :11400` empty.

### Teardown

```bash
# If ./netllm serve still running in Path A setup terminal, Ctrl+C there first.
lsof -i :11400   # should be empty after menubar quit + stop
```

---

## Path B — Menubar start() adopt (port already healthy)

Simulates reinstall or **Start Agent** after an orphan survived app quit: supervisor state is `.stopped` or `.failed` but `/health` is already 200.

### Setup

1. Quit menubar; ensure port free.
2. Start orphan via CLI (same as Path A steps 2–3):
   ```bash
   ./netllm serve
   curl -sf http://127.0.0.1:11400/health && echo ok
   ```
3. Launch menubar app but **do not** rely on launch adopt alone — if header already matches pass criteria, proceed to step 4 for the explicit start path.

### Exercise

4. If header shows **Agent stopped** with healthy port (reproduces pre-#43 bug), click **Start Agent**.
   - `ServerProcess.start()` detects healthy port → `adoptHealthyListener()` → `.running` without spawning a second agent.
5. Alternatively, from a clean `.stopped` supervisor state with orphan port: menubar **Start Agent** from menu.

### Verify

6. Same **Pass criteria** table — header, menu, Settings hero aligned within one menu open (no stale frame).
7. Confirm no duplicate agent: one `lsof -i :11400` listener; menubar log shows adopt, not twin spawn:
   ```bash
   tail -20 ~/Library/Application\ Support/netllm/logs/agent.log
   ```

### Teardown

1. **Stop Agent** from menubar (or quit app — both should release `:11400`).
2. Ctrl+C any remaining `./netllm serve` terminal.
3. `lsof -i :11400` → empty.

---

## Negative control (optional, pre-#43 builds only)

On a build **before** v0.4.5.1, repeat Path A step 4. Expected failure mode: **Agent stopped** header + **Stop Agent** menu simultaneously. Do not ship if this reproduces on current `main`.

---

## Release gate linkage

Include this checklist in pre-release manual gates when:

- `apps/netllm-mac/Sources/Menubar/MenubarAppModel.swift` status/header logic changes
- `apps/netllm-mac/Sources/Server/ServerProcess.swift` adopt/start/stop changes
- `apps/netllm-mac/Sources/AppView/AgentSupervisor.swift` Settings status label changes

Automated companions (run first):

```bash
cd apps/netllm-mac && swift test
scripts/verify-before-pr.sh          # macOS
scripts/test-menubar-e2e.sh          # bundled CLI smoke (does not cover adopt UI)
scripts/test-menubar-lifecycle.sh    # port cleanup on quit (does not cover adopt UI)
```

Sign-off: maintainer initials + date on the [closure roadmap](../closure-roadmap-2026-08-03.md) pre-release checklist item.
