"""The shape of a doctor finding, shared by `netllm doctor` and the dashboard.

Before UI-6 a doctor payload was `{ok, issues[], notes[]}` where a finding was
prose. A client that wanted to put a *fix button* next to a finding had only
one way to decide which button: match the finding's text with a regex. That is
brittle by construction and silently wrong the moment the wording changes --
`static/pages/doctor.js` carried exactly such a table.

So every check now emits a row with a stable dotted `id`. `id` is the join key
for a fix action and the thing a support bundle can be diffed on.

`issues` and `notes` are *derived* from `checks` and keep the exact shape and
order they had before, because the macOS app and older dashboards read them:

    issues == [{title, fix} for c in checks if not c.ok and c.severity == "error"]
    notes  == [c.detail     for c in checks if not c.ok and c.severity == "warn"]

Both surfaces build rows through `doctor_check` and finish through
`doctor_report`, so the agent's payload and the CLI's `--json` cannot drift
apart on the shape -- the same reason `config_report` exists for the findings
themselves.
"""

from __future__ import annotations

from typing import Any

#: Every `severity` a row can carry. "info" is the severity of a row that
#: PASSED; a failing row is "error" (a real problem -- lands in `issues` and
#: clears top-level `ok`) or "warn" (advisory -- lands in `notes` and does
#: not). That second case is load-bearing: "LAN swarm is open, no cluster
#: token" has always been a note rather than a failure, and the structured
#: shape must not quietly promote it.
DOCTOR_SEVERITIES = ("error", "warn", "info")

#: `action.kind` is a closed set.
#:
#: `config_patch` and `admin_post` name an *existing* admin route and carry the
#: exact body the client should POST to it. There is deliberately no
#: `POST /netllm/v1/admin/doctor/fix {id}`: a route whose effect is chosen by
#: server code the caller cannot inspect turns one admin route into an
#: open-ended one, which is a privilege-escalation shape. Declaring the patch
#: and letting the client POST it to the route it already has is strictly safer
#: and needs no new endpoint.
#:
#: `navigate` is a client-side hint (a page key). `none` means there is nothing
#: to click -- the remediation is a terminal command or a human decision.
DOCTOR_ACTION_KINDS = ("config_patch", "admin_post", "navigate", "none")

_NO_ACTION: dict[str, Any] = {"kind": "none"}


def doctor_check(
    check_id: str,
    *,
    ok: bool,
    title: str,
    detail: str = "",
    fix: str = "",
    severity: str = "error",
    subject: str = "",
    action: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One `checks[]` row.

    `severity` names the severity of what was *found*, so a row that passed is
    always "info" regardless of what it would have reported.

    `subject` distinguishes rows from a check that fans out over several
    things (one backend per row, one deprecated key per row). `(id, subject)`
    is the unique key; `id` alone is what a client keys its fix button on, so
    the button survives the fan-out.

    For a warn-severity row, `detail` IS the legacy note string -- `notes`
    derives from it verbatim.
    """
    if severity not in DOCTOR_SEVERITIES:
        raise ValueError(f"unknown doctor severity {severity!r}")
    if action is not None and action.get("kind") not in DOCTOR_ACTION_KINDS:
        raise ValueError(f"unknown doctor action kind {action.get('kind')!r}")
    row: dict[str, Any] = {
        "id": check_id,
        "subject": subject,
        "title": title,
        "ok": ok,
        "severity": "info" if ok else severity,
        "detail": detail,
    }
    if fix:
        row["fix"] = fix
    row["action"] = action if (action and not ok) else dict(_NO_ACTION)
    return row


def extend_or_pass(
    checks: list[dict[str, Any]],
    check_id: str,
    findings: list[dict[str, str]],
    *,
    ok_title: str,
    ok_detail: str,
) -> None:
    """Append one failing row per `{title, fix}` finding, or a single passing
    row when the report came back clean.

    The passing row is the point: it is what lets a client say "N checks · M
    passed" instead of only being able to list what broke.

    `netllm_core.config_report` returns `{title, fix}` and nothing else -- it
    has no separate identifier to fan out on -- so `subject` is the finding's
    own title. That is what keeps `(id, subject)` unique for a config carrying
    two deprecated keys.
    """
    if not findings:
        checks.append(doctor_check(check_id, ok=True, title=ok_title, detail=ok_detail))
        return
    checks.extend(
        doctor_check(
            check_id,
            ok=False,
            title=finding["title"],
            detail=finding["title"],
            fix=finding["fix"],
            subject=finding["title"],
        )
        for finding in findings
    )


def derive_issues(checks: list[dict[str, Any]]) -> list[dict[str, str]]:
    """The pre-UI-6 `issues` list, rebuilt from `checks`."""
    return [
        {"title": c["title"], "fix": c.get("fix", "")}
        for c in checks
        if not c["ok"] and c["severity"] == "error"
    ]


def derive_notes(checks: list[dict[str, Any]]) -> list[str]:
    """The pre-UI-6 `notes` list, rebuilt from `checks`."""
    return [c["detail"] for c in checks if not c["ok"] and c["severity"] == "warn"]


def doctor_report(checks: list[dict[str, Any]]) -> dict[str, Any]:
    """`{ok, checks, issues, notes?}` -- the whole doctor payload.

    `notes` stays omitted-when-empty, exactly as both surfaces emitted it
    before, so a client testing `"notes" in payload` is unaffected.
    """
    issues = derive_issues(checks)
    notes = derive_notes(checks)
    payload: dict[str, Any] = {"ok": not issues, "checks": checks, "issues": issues}
    if notes:
        payload["notes"] = notes
    return payload
