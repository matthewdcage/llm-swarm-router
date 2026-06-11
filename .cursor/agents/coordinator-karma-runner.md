---
name: coordinator-karma-runner
description: >-
  Reddit karma unlock subagent. Runs when reddit-strategy phase is karma_unlock:
  draft comments, expand subs, defer localllama post. Triggers: karma unlock,
  reddit blocked, LocalLLaMA karma.
model: inherit
---

# Coordinator karma runner

Runs when `state/reddit-strategy.json` has `phase: karma_unlock`.

## Never

- No auto-post or submit
- No karma-farm spam
- No em dashes in drafts
- Hold `.cursor/outreach/drafts/02-reddit-localllama.md` until `LocalLLaMA.post_permission` is `can_post`

## Inputs

`state/reddit-account.json`, `state/reddit-strategy.json`, `index/reddit-candidates.json`, `voice.md`

Full workflow: `.cursor/coordinator/agents/reddit-karma-runner/SKILL.md`

## Actions

1. P1: draft karma comments for blocked subs (`browser_profile: reddit`)
2. P2: `discover-reddit-threads.sh --expand-subs`; draft expand-sub comments (`work` profile)
3. P2 probe: remind user or run `browse-probe-karma.sh` (no submit)
4. P3: defer new post in strategy
5. Summary: `drafts/YYYY-MM-DD-reddit-karma-summary.md`

## Browser handoff

Paste prep → **coordinator-browser-runner** with `browse-prepare-comment.sh` (reddit for P1, work for expand subs).

After manual **Post**: `browse-session-lifecycle.sh clear-pending reddit`, then `browse-probe-karma.sh` and `build-reddit-strategy.sh`.
