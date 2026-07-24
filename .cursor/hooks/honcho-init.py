#!/usr/bin/env python3
# honcho-version: 2.2.0  honcho-template: honcho_init_py
"""
Cursor sessionStart hook — Honcho session initialization.

Runs automatically at the start of every Cursor agent session. Reads
.cursor/hooks/state/honcho-state.json, creates today's daily session if the
date has rolled over (calling the Honcho REST API directly), updates the state
file, and injects an agent_message so the agent knows the current Honcho state
and what startup steps remain.

Fails open: all errors are caught; the state file is updated best-effort and
the agent falls back to the honcho_rules.mdc protocol.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from typing import Optional

STATE_FILE = ".cursor/hooks/state/honcho-state.json"
# Direct Honcho REST API (not the MCP proxy at :8787)
HONCHO_API = "http://localhost:8000"
WORKSPACE_ID = "default"


def main() -> None:
    try:
        sys.stdin.read()
    except Exception:
        pass

    today = datetime.now().strftime("%Y-%m-%d")
    now_iso = datetime.now(timezone.utc).isoformat()

    state = load_state(today, now_iso)
    user_peer: str = state["workspace"]["userPeerId"]
    assistant_peer: str = state["workspace"]["assistantPeerId"]

    session_created = False
    context_needed = False
    api_note = ""

    session_date: str = state["session"].get("date", "")
    session_id: str = state["session"].get("id", f"cursor-{today}")

    # New day → create today's session and add peers
    if session_date != today:
        new_session_id = f"cursor-{today}"
        err = create_session_with_peers(new_session_id, user_peer, assistant_peer)
        if err:
            api_note = err
        else:
            session_created = True

        state["session"]["id"] = new_session_id
        state["session"]["date"] = today
        state["session"]["createdAt"] = now_iso
        state["session"]["lastMessageAt"] = now_iso
        state["session"]["turnCount"] = 0
        state["memory"]["lastContextLoadedAt"] = None
        state["memory"]["turnsSinceLastDream"] = 0
        session_id = new_session_id
        context_needed = True

    # Check whether peer context is fresh for today
    last_ctx: str = (state.get("memory") or {}).get("lastContextLoadedAt") or ""
    if not last_ctx or last_ctx[:10] != today:
        context_needed = True

    save_state(state)

    turns: int = state["memory"].get("turnsSinceLastDream", 0)
    threshold: int = state["memory"].get("dreamThreshold", 30)
    turn_count: int = state["session"].get("turnCount", 0)

    lines = [
        "━━━ HONCHO SESSION READY (sessionStart hook) ━━━",
        f"  session_id:       {session_id}",
        f"  session_created:  {session_created}",
        f"  load_context:     {context_needed}",
        f"  turn_count:       {turn_count}",
        f"  turns_to_dream:   {turns}/{threshold}",
    ]
    if api_note:
        lines.append(f"  api_note:         {api_note} — retry via MCP if needed")

    lines += [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"STARTUP CHECKLIST (honcho_rules.mdc):",
        f"  [{'x' if not context_needed else ' '}] Peer context loaded for today",
        f"  [ ] call get_peer_context(peer_id='Assistant', target_peer_id='{user_peer}') if load_context=True",
        f"  [ ] call add_messages_to_session after each exchange",
        f"  [ ] schedule_dream when turns_to_dream reaches {threshold}",
    ]

    context_text = "\n".join(lines)
    # Output JSON with BOTH platform-specific context fields:
    #   Cursor sessionStart  → reads "additional_context" (snake_case)
    #   Claude Code SessionStart → reads "additionalContext" (camelCase)
    # Unknown fields are ignored by each platform, so this is safe cross-platform.
    print(json.dumps({
        "additional_context": context_text,   # Cursor
        "additionalContext": context_text,    # Claude Code
    }))


def load_state(today: str, now_iso: str) -> dict:
    default: dict = {
        "version": 1,
        "_comment": "Honcho MCP agent state — managed by honcho_rules.mdc and .cursor/hooks/honcho-init.py.",
        "workspace": {
            "id": WORKSPACE_ID,
            "baseUrl": "http://127.0.0.1:8787",
            "userPeerId": "matthewcage",
            "assistantPeerId": "Assistant",
        },
        "session": {
            "id": f"cursor-{today}",
            "date": today,
            "createdAt": now_iso,
            "lastMessageAt": now_iso,
            "turnCount": 0,
        },
        "memory": {
            "lastContextLoadedAt": None,
            "dreamLastScheduledAt": None,
            "turnsSinceLastDream": 0,
            "dreamThreshold": 30,
        },
        "queue": {
            "lastCheckedAt": now_iso,
            "totalWorkUnits": 0,
            "completedWorkUnits": 0,
            "pendingWorkUnits": 0,
        },
    }
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE) as f:
                data = json.load(f)
            for key in default:
                if key not in data:
                    data[key] = default[key]
            return data
    except Exception:
        pass
    return default


def create_session_with_peers(
    session_id: str, user_peer: str, assistant_peer: str
) -> Optional[str]:
    """Create a Honcho session and add both peers.

    Returns None on success, an error string if the API is unreachable.
    The session endpoint is idempotent (get_or_create), so re-running is safe.
    """
    try:
        r = subprocess.run(
            [
                "curl", "-sf", "-X", "POST",
                f"{HONCHO_API}/v3/workspaces/{WORKSPACE_ID}/sessions",
                "-H", "Content-Type: application/json",
                "-H", "Authorization: Bearer local-dev",
                "-d", json.dumps({"id": session_id}),
                "--connect-timeout", "3",
                "--max-time", "5",
            ],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            return f"Honcho API unreachable at {HONCHO_API} (run ./start.sh)"

        # Add peers — REST body is {peer_id: SessionPeerConfig, ...}
        peers_body = {user_peer: {}, assistant_peer: {}}
        subprocess.run(
            [
                "curl", "-s", "-X", "POST",
                f"{HONCHO_API}/v3/workspaces/{WORKSPACE_ID}/sessions/{session_id}/peers",
                "-H", "Content-Type: application/json",
                "-H", "Authorization: Bearer local-dev",
                "-d", json.dumps(peers_body),
                "--connect-timeout", "3",
                "--max-time", "5",
            ],
            capture_output=True,
            text=True,
        )
        return None
    except Exception as exc:
        return str(exc)


def save_state(state: dict) -> None:
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass


if __name__ == "__main__":
    main()
