---
description: Troubleshoot netllm misconfigurations (PATH, providers, mDNS, agent health)
allowed-tools: Read, Shell, Grep
---

Load and follow the skill at `.claude/skills/netllm-doctor/SKILL.md` (fallback: `.agents/skills/netllm-doctor/SKILL.md`).

Run `./netllm doctor` and additional checks from the skill. Produce a structured report: Problem → Fix → Verify command for each issue found.

- Prefer `./netllm` from repo root.
- Re-run doctor after applying fixes.
