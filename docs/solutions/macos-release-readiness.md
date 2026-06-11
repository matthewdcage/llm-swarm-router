# macOS release readiness — build, validation, and one-shot publish

Durable gate checklist for **stable macOS DMG** releases. Goal: no production tag until every gate below is green locally and in CI.

**Origin:** v0.2.3.3 shipped with uncommitted agent-lifecycle fixes (quit-from-`failed` left orphans on `:11400`; `serve` without `--replace`; doctor blind spot under `NETLLM_SUPERVISED=menubar`). Automated checks did not exercise the menubar supervisor path.

---

## Problem frame

| Layer | What we test today | What broke in production |
|-------|-------------------|--------------------------|
| **pytest** | Port/runtime helpers, doctor JSON in bundle context | Does not run menubar `ServerProcess.stop()` |
| **`scripts/test-menubar-e2e.sh`** | Bundled CLI on alt port `11401`, API smoke, DMG exists | Never launches `.app`, never quits app, never asserts `:11400` cleanup |
| **`scripts/ci.sh`** | lint, test, sdk, Linux/Windows packaging smoke | **No macOS job** |
| **`.github/workflows/release.yml`** | Builds DMG, version pin, SHA256 | **No post-build menubar lifecycle** |
| **`macos-app-install.sh`** | Upgrade teardown (quit + orphan kill) | Covers **upgrade**, not **daily quit** from `failed` |
| **Manual smoke** | Informal | Not required before tag |

**Root cause:** release pipeline validated *artifact build* and *CLI-in-isolation*, not *supervisor lifecycle* (start → conflict/failed → quit → port free).

---

## Scope

**In scope (v0.2.3.4 batch):**

- Land local patches: `ServerProcess.swift`, `runtime.py`, `local.py`, `main.py`, `test_runtime.py`
- New automated menubar lifecycle script + CI/release wiring
- Doctor/supervised regression tests
- Version bump, release notes, single publish runbook

**Out of scope (follow-ups):**

- GUI automation (cua-driver) for About/Settings/Updates menus
- Linux/Windows lifecycle parity (separate doc: `linux-windows-alpha-qa.md`)
- Swift unit tests in Xcode (optional; shell lifecycle test is the gate)

---

## Release gates (all must pass)

Gates are ordered. **Do not tag** until Gate 5 sign-off.

### Gate 0 — Change completeness

| Check | How |
|-------|-----|
| All lifecycle fixes committed on `main` | `git diff` empty vs intended file list (see §Work items) |
| Version pins aligned | `pyproject.toml`, `packages/netllm-core/.../version.py`, `apps/netllm-mac` `MARKETING_VERSION` / `build.sh`, `docs/release-notes/vX.Y.Z.md` |
| Release notes mention quit/failed/orphan/`--replace`/doctor | Human read |

**Files expected in the patch:**

- `apps/netllm-mac/Sources/Server/ServerProcess.swift`
- `packages/netllm-discovery/src/netllm_discovery/runtime.py`
- `packages/netllm-discovery/src/netllm_discovery/local.py`
- `packages/netllm-cli/src/netllm_cli/main.py`
- `tests/test_runtime.py`
- New: `scripts/test-menubar-lifecycle.sh` (see Gate 2)
- New/updated: `tests/test_doctor_supervised_port.py` (or extend `test_doctor_app_context.py`)

---

### Gate 1 — Core CI (existing)

```bash
./scripts/ci.sh lint
./scripts/ci.sh test    # 143+ tests, incl. new runtime/doctor tests
./scripts/ci.sh sdk
```

**Pass criteria:** zero failures; new tests cover `stop_netllm_on_port` health fallback and degraded `probe_netllm_agent`.

**CI workflow:** `.github/workflows/ci.yml` — unchanged matrix (Ubuntu + Windows). Gate 2 adds macOS.

---

### Gate 2 — macOS menubar lifecycle (NEW — critical)

**New script:** `scripts/test-menubar-lifecycle.sh`

Runs on **macOS only** (maintainer machine + CI `macos-14`).

#### Scenario matrix (each must pass)

| ID | Scenario | Setup | Action | Assert |
|----|----------|-------|--------|--------|
| L1 | Clean quit after running | Build release `.app`; launch via `open -gj`; control socket `start`; wait `running` | `osascript` quit app OR SIGTERM main bundle PID; wait ≤15s | `lsof -ti :11400` empty |
| L2 | Quit from `failed` with orphan | Pre-bind `:11400` with bundled CLI `serve -q` (orphan); launch app; auto-start or `start` → supervisor `failed` | Quit app | `lsof -ti :11400` empty |
| L3 | `stop` via control socket | App running, agent `running` | `netllm stop` (control socket) | Port free; state `stopped` |
| L4 | `forceRestart` / menubar restart path | Agent `running` | Control socket `restart` | Single listener on `:11400`; `/health` 200 |
| L5 | `serve --replace` in bundle | Occupied port (L2 orphan setup) | App `start` | Supervisor ends `running` (not `failed`); one PID on port |
| L6 | Doctor under menubar supervision | Agent running via app; `NETLLM_SUPERVISED=menubar` | `./netllm doctor` | Flags if supervisor state ≠ running; does not false-negative on occupied port |

#### Implementation notes

- Reuse control socket helpers from `packages/netllm-cli/src/netllm_cli/lifecycle/darwin.py` (same path production uses).
- Use `NETLLM_TEST_PORT=11400` only when app config points at default; isolate with temp config under `~/Library/Application Support/netllm` if needed.
- Launch from `apps/netllm-mac/build/Stage/llm-swarm-router.app` (same tree `release.yml` verifies).
- Trap cleanup: always kill stray PIDs on exit.

**Pass criteria:** script exits 0; prints `ALL LIFECYCLE CHECKS PASSED`.

**Wire into automation:**

1. **CI:** new job `menubar-lifecycle` on `macos-14` in `.github/workflows/ci.yml` (after lint, can run parallel to test).
2. **Release:** add step after `build.sh release` in `.github/workflows/release.yml` `build-macos` job:

   ```bash
   bash scripts/test-menubar-e2e.sh
   bash scripts/test-menubar-lifecycle.sh
   ```

3. **Local pre-tag:** maintainer runs both scripts on the **same** built Stage app used for DMG.

---

### Gate 3 — Build + bundle verification (existing, tightened)

```bash
apps/netllm-mac/Scripts/build.sh release
bash scripts/test-menubar-e2e.sh
bash packaging/scripts/create-dmg.sh
```

| Check | Assert |
|-------|--------|
| Bundled CLI version | `Contents/MacOS/netllm-cli --version` matches `pyproject.toml` |
| `serve` args in binary | `strings` or grep build log: menubar uses `serve -q --replace` |
| venvstacks export | `packaging/_export/cpython-3.11` present |
| DMG | `dist/llm-swarm-router.dmg` + `.sha256` |
| Info.plist | `CFBundleShortVersionString` == release version |

---

### Gate 4 — User-path rehearsal

```bash
./scripts/emulate-user-install-mac.sh   # Stage → macos-app-install.sh --source → /Applications
```

Then manual (5 min):

| Step | Expected |
|------|----------|
| Menubar **Start Agent** (if not auto) | `Agent: running` |
| `curl -sf http://127.0.0.1:11400/health` | 200 |
| `curl -sf http://127.0.0.1:11400/ui/` | HTML dashboard |
| **Updates → Check for Updates** | Sees latest tag or “up to date” |
| **Quit** from menubar | `lsof -i :11400` empty within 15s |
| Relaunch → **Start Agent** | No “exited with code 0 / auto-restart failed” |

**Upgrade path (if prior build installed):**

```bash
packaging/scripts/macos-app-install.sh --source apps/netllm-mac/build/Stage/llm-swarm-router.app
# After notarization: ./scripts/upgrade-mac-app.sh dist/llm-swarm-router.dmg
```

---

### Gate 5 — Pre-tag sign-off (human)

Print and tick before `git tag`:

```
[ ] Gate 0: all files committed, version bump complete
[ ] Gate 1: ./scripts/ci.sh (lint + test + sdk) green
[ ] Gate 2: test-menubar-lifecycle.sh green (local macOS)
[ ] Gate 3: release build + test-menubar-e2e.sh green
[ ] Gate 4: emulate-user-install-mac.sh + quit/port check
[ ] Release notes drafted (docs/release-notes/vX.Y.Z.md)
[ ] README / docs/platform-matrix.md latest version link updated
[ ] No untracked local patches outside the release commit
```

**Rule:** If any box is unchecked, **no tag**.

---

### Gate 6 — Post-publish verification

After GitHub Release `published` (workflow attaches artifacts):

| Check | How |
|-------|-----|
| Release workflow green | Actions → Release workflow |
| Assets present | DMG, `.sha256`, deb, rpm, zip, winget yaml |
| Download DMG from Releases page | Install on a second machine (clean field test) |
| In-app update OR clean install | Version matches tag; Gate 4 subset on real hardware |
| `GET /netllm/v1/update/check` | Returns new version for older clients |

---

## One-shot execution runbook (v0.2.3.4)

Execute **once** when Gates 0–5 are green. Do not interleave partial releases.

### Phase A — Implement missing automation (if not done)

1. Add `scripts/test-menubar-lifecycle.sh` (L1–L6).
2. Extend doctor tests for supervised + failed supervisor messaging.
3. Wire Gate 2 into `ci.yml` and `release.yml`.
4. Commit lifecycle + test + CI wiring: `fix(macos): quit and failed-state agent cleanup`.

### Phase B — Version and docs

1. Bump to `0.2.3.4` (all pins per `AGENTS.md` SDK/release checklist).
2. Write `docs/release-notes/v0.2.3.4.md` (quit handler, `--replace`, doctor, SSL verify on loopback scan).
3. Update `docs/macos-troubleshooting.md` if behavior changes for users.

### Phase C — Full local validation

```bash
./scripts/ci.sh
apps/netllm-mac/Scripts/build.sh release
bash scripts/test-menubar-e2e.sh
bash scripts/test-menubar-lifecycle.sh
./scripts/emulate-user-install-mac.sh
# manual Gate 4 checklist + quit/port verify
```

### Phase D — Publish (single sequence)

```bash
git push origin main
git tag v0.2.3.4
git push origin v0.2.3.4
gh release create v0.2.3.4 --title "v0.2.3.4" --notes-file docs/release-notes/v0.2.3.4.md
```

Wait for `.github/workflows/release.yml` → Gate 6.

### Phase E — Field verify

Field machine: install from Releases DMG → Gate 4 subset → confirm update notification from `0.2.3.3` or older.

---

## Ongoing policy (every stable macOS release)

1. **No tag without `test-menubar-lifecycle.sh`** — treat like `scripts/ci.sh test`.
2. **Release workflow must run menubar e2e + lifecycle** — not only `build.sh`.
3. **Any change to** `ServerProcess.swift`, `AppDelegate.swift`, `macos-app-install.sh`, `lifecycle/darwin.py` **requires** lifecycle script update or explicit scenario note in release notes.
4. **Keep** `docs/solutions/linux-windows-alpha-qa.md` for non-macOS; this doc owns macOS stable gates.
5. **Regressions** get a row in the scenario matrix (L7+) before the next tag.

---

## Risk register

| Risk | Mitigation |
|------|------------|
| CI macOS minutes / flake on `open -gj` | Retry launch in script; 30s timeouts; artifact Stage app from same job |
| Lifecycle script needs GUI session | Use `macos-14` runner (has window server); avoid headless assumptions |
| Orphan not netllm (foreign process on 11400) | `stop_netllm_on_port` only kills netllm-like PIDs; L2 uses bundled CLI |
| SSL/CA in bundled Python | `local.py` `verify=False` for loopback; add e2e status probe in menubar-e2e |
| Doctor still silent | L6 + `test_doctor_supervised_port` assert message contains “supervisor” |

---

## Decision log

| Decision | Rationale |
|----------|-----------|
| Shell lifecycle test over XCTest | Matches real user path; uses production control socket; runnable in release CI |
| Fail release job if lifecycle fails | Stronger than maintainer-only manual smoke |
| Gate 4 remains partly manual | Updates UI and menubar labels need eyes; port/quit checks are scriptable |
| One-shot runbook | User request: single confirmed e2e execution, not incremental partial releases |

---

## Related docs

- `docs/solutions/linux-windows-alpha-qa.md` — Linux/Windows packaging QA
- `docs/macos-troubleshooting.md` — user-facing orphan/port guidance
- `packaging/scripts/macos-app-install.sh` — upgrade teardown (complements quit handler)
- `scripts/test-menubar-e2e.sh` — bundled CLI + API smoke (Gate 3, not lifecycle)
