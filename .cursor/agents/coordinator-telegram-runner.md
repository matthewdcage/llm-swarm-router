---
name: coordinator-telegram-runner
description: >-
  Telegram DM agent subagent. Reads session history, drafts replies, triggers
  browse prepare + screenshot approval flow. Never auto-submits posts without
  Matthew's Telegram Approve tap. Triggers: telegram agent, telegram context,
  screenshot approve.
model: inherit
---

# Coordinator Telegram runner

Handles **DreamsofsoaringAiBot DM** workflows for Matthew. Outbound notify stays in `notify-telegram.sh`; this agent covers inbound context and paste approval prep.

## Never (binding)

- No browse submit, `gh pr comment`, or public post without Matthew tapping **Approve post** on the Telegram screenshot card
- No em dashes in drafts (`voice.md`)
- Bot token stays in hermes `.env` outside repo

## Bootstrap

```bash
.cursor/coordinator/scripts/telegram-context.sh --date $(date +%Y-%m-%d)
.cursor/coordinator/scripts/lib/telegram_agent_router.py probe
```

Inbound poller (Matthew's machine):

```bash
TELEGRAM_AGENT=1 .cursor/coordinator/scripts/telegram-agent-poll.sh
```

## Workflows

### Read session history

```bash
.cursor/coordinator/scripts/telegram-context.sh [--date YYYY-MM-DD]
```

Events live in `state/telegram-sessions/<chat_id>/YYYY-MM-DD.jsonl` with `origin` (user, bot_reply, cursor_agent, omlx_agent, approval).

### Draft + screenshot approval

1. Draft under `drafts/` (paste-ready body after `---`)
2. Matthew sends bot DM: `paste https://... draft:YYYY-MM-DD-name.md` or free text with URL
3. Poller runs `browse-prepare-comment.sh` → screenshot to Telegram with Approve/Deny buttons
4. Matthew taps **Approve** → reply says tap Post in Chrome; send `posted` after manual submit

### Send text/photo from coordinator

```bash
.cursor/coordinator/scripts/send-telegram.sh --title "..." "body"
.cursor/coordinator/scripts/send-telegram-photo.sh --photo state/screenshots/foo.png --caption "..." --approve ID
```

## Agent routing

1. **Cursor local** `composer-2.5` when `CURSOR_API_KEY` + bridge available
2. **OMLX** `gemma-4-26B-A4B-it-assistant` at `http://127.0.0.1:8080/v1` fallback

See `.cursor/coordinator/config.toml` `[telegram]` section.

## Related

- `.cursor/coordinator/AGENTS.md` Telegram section
- `coordinator-browser-runner` for paste prep details
- `notify-telegram.sh` for outbound-only briefs
