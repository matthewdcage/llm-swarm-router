# Claude Code: swarm-llm

Primary project guide: **[AGENTS.md](AGENTS.md)** (commands, architecture, do-not rules). DOX protocol: **[`.cursor/agents/AGENTS.md`](.cursor/agents/AGENTS.md)**; per-folder contracts under `packages/`, `apps/`, `docs/`, etc.

**Slash commands** (`.claude/commands/`):

- `/netllm-setup`: first-time install from this repo
- `/netllm-connect`: wire Cursor, Claude Code, Codex, or Honcho to the router
- `/netllm-swarm`: multi-machine LAN mesh
- `/netllm-doctor`: troubleshoot misconfigurations

**Skills** live in `.claude/skills/` (canonical source: `.agents/skills/`). Run `scripts/sync-agent-skills.sh` after editing skills.

**CI / release / macOS builds:** [docs/ci-and-release.md](docs/ci-and-release.md) · pre-push: `scripts/verify-before-pr.sh`

<!-- honcho-version: 2.2.0  honcho-template: claude_md_section -->
## Honcho Memory (MCP)

The `honcho` MCP server is available via `.cursor/mcp.json`. Protocol:

1. **Read** `.cursor/hooks/state/honcho-state.json` — get session ID, check if context is stale
2. **Load context** if stale: `get_peer_context(peer_id: "Assistant", target_peer_id: "matthewcage")`
3. **Record turns**: `add_messages_to_session` after each exchange
4. **Query memory**: `chat(peer_id: "Assistant", query: "...", target_peer_id: "matthewcage")`
5. **Setup/repair**: call `setup_agent_workspace` to restore any missing files

Full protocol: `.cursor/rules/honcho_rules.mdc`
