# apps — native platform applications

## Purpose

Native shells around the shared Python agent. Today: macOS menubar (`netllm-mac`). Linux/Windows use packaged agent + web dashboard at `/ui/` (no native app yet).

## Ownership

Parent rail: [../AGENTS.md](../AGENTS.md). Release builds: [../packaging/AGENTS.md](../packaging/AGENTS.md).

## Local Contracts

- Native apps supervise the same agent core on `:11400`; never run menubar app and `./netllm serve` concurrently
- macOS in-app install/update only from `/Applications/llm-swarm-router.app` or staged `netllm-mac.app`
- macOS release DMGs require Developer ID notarization for Gatekeeper on macOS 26+ ([../docs/macos-code-signing.md](../docs/macos-code-signing.md))
- Design tokens shared with agent dashboard via `design-tokens.json` + `scripts/generate-dashboard-tokens.py`

## Work Guidance

- Platform-specific install/update logic stays in app + packaging scripts, not duplicated in Python agent
- macOS PRs touching this tree trigger `menubar-lifecycle` CI on `macos-14`

## Verification

```bash
scripts/verify-before-pr.sh          # macOS Swift build
scripts/verify-before-pr.sh --full   # + menubar e2e when Stage .app exists
scripts/test-menubar-e2e.sh          # bundled serve -q + 0.0.0.0 listen smoke before DMG
```

## Child DOX Index

| Path | Contract |
|------|----------|
| [`netllm-mac/AGENTS.md`](netllm-mac/AGENTS.md) | Swift menubar app |
