# Claude Code — swarm-llm

Primary project guide: **[AGENTS.md](AGENTS.md)** (commands, architecture, do-not rules).

**Slash commands** (`.claude/commands/`):

- `/netllm-setup` — first-time install from this repo
- `/netllm-connect` — wire Cursor, Claude Code, Codex, or Honcho to the router
- `/netllm-swarm` — multi-machine LAN mesh
- `/netllm-doctor` — troubleshoot misconfigurations

**Skills** live in `.claude/skills/` (canonical source: `.agents/skills/`). Run `scripts/sync-agent-skills.sh` after editing skills.
