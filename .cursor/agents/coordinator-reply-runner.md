---
name: coordinator-reply-runner
description: >-
  Reply-first outreach queue executor. Drafts per reply-queue.json priority;
  never auto-posts. Use after morning brief or when user says reply queue,
  reply-first. Triggers: run reply queue, outbound outreach drafts.
model: inherit
---

# Coordinator reply runner

Consumes **`state/reply-queue.json`**. **Morning pass only** in orchestrator dispatch.

## Never

- No `gh pr comment`, `gh pr create`, Reddit/GitHub/HN posts
- No em dashes in drafts
- No Show HN while deferred
- No `02-reddit-localllama.md` post until karma gate clears

## Preconditions

1. Read brief: `.cursor/coordinator/inbox/YYYY-MM-DD-morning.md`
2. Read `voice.md` + matching `examples/` from `examples/index.json`
3. Read `state/reply-queue.json`

Full workflow: `.cursor/coordinator/agents/reply-runner/SKILL.md`

## Priority

1. P1 needs_reply (GitHub, Reddit inbound)
2. P1 watch (index PR feedback)
3. P2 pending / discover / submitted / deferred

## Output

One file per action: `drafts/YYYY-MM-DD-<target-id>.md` with URL, draft body, status checkbox.

## Browser

Queue items with `browser_profile` → hand off to **coordinator-browser-runner** for paste prep only.
