# .agents — canonical agent skills

## Purpose

Source of truth for netllm agent skills. Sync to `.claude/`, `.cursor/`, and `.github/` via `scripts/sync-agent-skills.sh` after any edit.

## Ownership

| Skill | Path | Triggers |
|-------|------|----------|
| `netllm-setup` | `skills/netllm-setup/SKILL.md` | install, `/netllm-setup` |
| `netllm-connect-editor` | `skills/netllm-connect-editor/SKILL.md` | connect Cursor/Claude/Codex, `/netllm-connect` |
| `netllm-swarm` | `skills/netllm-swarm/SKILL.md` | LAN mesh, `/netllm-swarm` |
| `netllm-doctor` | `skills/netllm-doctor/SKILL.md` | troubleshoot, `/netllm-doctor` |
| `coordinator-platform` | `skills/coordinator-platform/SKILL.md` | coordinator browser paste, session verify, snapshot, doctor, Telegram preflight (local maintainer; gitignored) |

Parent rail: [../AGENTS.md](../AGENTS.md). Editor reference: [../docs/editor-integration.md](../docs/editor-integration.md).

## Local Contracts

- Edit skills only under `.agents/`; never edit synced copies directly
- Run `scripts/sync-agent-skills.sh` after changes before commit
- Slash commands in `.claude/commands/` point at these skills

## Work Guidance

- Skill descriptions must include trigger phrases for reliable agent routing
- Keep setup/connect/swarm/doctor flows aligned with `./netllm` CLI behavior (v0.4.0.1: open trusted-LAN swarm by default; secured token optional via `--secure`, `--create`, or menubar toggle)

## Verification

```bash
scripts/sync-agent-skills.sh
# Diff synced copies under .claude/skills/, .cursor/skills/, .github/skills/
```

## Child DOX Index

| Path | Contract |
|------|----------|
| [`skills/netllm-setup/`](skills/netllm-setup/) | First-time install skill |
| [`skills/netllm-connect-editor/`](skills/netllm-connect-editor/) | Editor wiring skill |
| [`skills/netllm-swarm/`](skills/netllm-swarm/) | LAN swarm skill |
| [`skills/netllm-doctor/`](skills/netllm-doctor/) | Troubleshooting skill |
| [`skills/coordinator-platform/`](skills/coordinator-platform/) | Coordinator platform preflight (gitignored locally) |

Individual skill folders use SKILL.md as their contract; no per-skill AGENTS.md unless a skill grows multi-file maintenance docs.

Updated: 2026-06-12 (coordinator-platform skill; dispatch bridge verification in skill)
