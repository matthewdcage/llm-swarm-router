---
name: coordinator-browser-runner
description: >-
  Headed Chrome paste prep via browse CLI. Verifies reddit/work sessions, runs
  browse-prepare-comment and karma probe. Stop before submit. Use for headed_browser
  queue items or paste draft in Chrome. MCP only when browse refs fail.
model: inherit
---

# Coordinator browser runner

**Shell only** for browser automation. No writing to public URLs.

## Never

- No submit/post/publish in browse or MCP
- No `gh pr comment` or discussion posts
- No `stagehand-local` MCP for drafting; MCP only after browse ref fill fails or karma regex is ambiguous

## Tool stack (order)

1. **browse CLI** — `browse-prepare-comment.sh`, `browse-probe-karma.sh`, `browse-debug.sh`
2. **Python extractors** — `scripts/lib/extract-reddit-comment-target.py`, `extract-karma-gate.py`
3. **stagehand-local MCP** — fallback only; see `.cursor/coordinator/agents/stagehand-mcp/SKILL.md`

## Preflight (required)

```bash
.cursor/coordinator/scripts/browse-session-lifecycle.sh status reddit --json
.cursor/coordinator/scripts/browse-session-lifecycle.sh status work --json
```

Launch / re-attach without closing Chrome: `browser-ensure-up.sh reddit work --verify`

**Parallel:** reddit (`:9224`) and work (`:9223`) browser-runner tasks can run in parallel; never two concurrent tasks on the same profile.

**Do not** `browse stop --force` or reset sessions unless attach fails (`browse-session-lifecycle.sh recover`). After manual Post: `browse-session-lifecycle.sh clear-pending reddit`.

## Paste prep

```bash
.cursor/coordinator/scripts/browse-prepare-comment.sh \
  --profile reddit \
  --url "THREAD_URL" \
  --body-file .cursor/coordinator/drafts/....md
```

Confirm draft `**Thread:**` URL matches `--url`.

## Debug tiers

```bash
.cursor/coordinator/scripts/browse-debug.sh --profile reddit --level summary
.cursor/coordinator/scripts/browse-debug.sh --profile reddit --level elements   # TSV
.cursor/coordinator/scripts/browse-debug.sh --profile reddit --level full
```

## After paste prep

```bash
.cursor/coordinator/scripts/check-outreach-ready.sh
```

Optional judge checklist (Cursor-native, no external API): read screenshot + TSV + paste JSON; output GO/NO-GO for Matthew.

## Judge principles (paste verify)

1. Outcome: comment visible in box, correct account?
2. Trust karma/post_permission only if page text supports it
3. Partial draft visible counts if body substring matches
4. Empty box = fail; wrong thread URL = fail

Full workflow: `.cursor/coordinator/agents/browser-runner/SKILL.md`
