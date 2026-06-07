---
description: Wire Cursor, Claude Code, Codex, or Honcho to the local netllm router
allowed-tools: Read, Shell, Grep
---

Load and follow the skill at `.claude/skills/netllm-connect-editor/SKILL.md` (fallback: `.agents/skills/netllm-connect-editor/SKILL.md`).

Confirm the agent is running (`curl -sf http://127.0.0.1:11400/health`), list models with `./netllm models`, then guide the user through editor-specific wiring.

- Do not auto-edit user settings files without explicit consent — show copy-paste steps.
- Load `references/editor-settings.md` from the skill folder for per-editor detail.
- Verify with `./netllm test` or a minimal chat completion.
