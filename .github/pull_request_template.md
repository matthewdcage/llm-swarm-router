## Summary

<!-- What does this PR change and why? Link related issues: Fixes #123 -->

## Type of change

- [ ] Bug fix (non-breaking change that fixes an issue)
- [ ] New feature (non-breaking change that adds functionality)
- [ ] Breaking change (fix or feature that would cause existing behavior to change)
- [ ] Documentation only
- [ ] Packaging / CI / tooling

## Platforms

- [ ] macOS (menubar app)
- [ ] macOS / Linux / Windows (CLI / agent)
- [ ] Linux packaging
- [ ] Windows packaging
- [ ] Not platform-specific

## Test plan

<!-- How did you verify this? Commands run, manual steps, screenshots -->

```bash
./scripts/ci.sh
```

- [ ] Tests added or updated for behavior changes
- [ ] `./netllm doctor` passes locally (if agent/CLI touched)
- [ ] Docs updated (README, CONTRIBUTING, install guides, AGENTS.md) if user-facing

## SDK bump (only if updating `openai` or `anthropic`)

- [ ] One SDK package per PR (`netllm-sdk-openai` **or** `netllm-sdk-anthropic`)
- [ ] `uv.lock` updated and committed
- [ ] [docs/sdk-versions.md](docs/sdk-versions.md) updated (resolved version + date)
- [ ] Upstream changelog reviewed (link in PR description)
- [ ] Layer changed: adapter / bridge / agent / probes (see `docs/sdk-versions.md`)
- [ ] `./scripts/ci.sh sdk` passed

## Checklist

- [ ] PR is focused: not mixing unrelated changes
- [ ] Conventional commit message(s) (`feat:`, `fix:`, `docs:`, etc.)
- [ ] No secrets, API keys, or `.cursor/mcp.json` committed
- [ ] Agent skills synced (`scripts/sync-agent-skills.sh`) if `.agents/skills/` changed

## Screenshots / recordings

<!-- For UI changes (macOS app, future web dashboard) -->
