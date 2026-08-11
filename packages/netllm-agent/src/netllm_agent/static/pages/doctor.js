/* Doctor & test page — design 2d. */

/*
 * GET /netllm/v1/doctor returns `checks[]`: one row per check the agent ran,
 * passing or failing, each with a stable dotted `id` (netllm_agent.admin
 * .DOCTOR_CHECK_IDS -> netllm_core.doctor_checks). Fix buttons key off that
 * id.
 *
 * They used to key off a regex table matched against the finding's prose,
 * because prose was all the payload carried. That table is gone: it was wrong
 * by construction (a reworded finding silently lost or, worse, kept the wrong
 * button) and it could never offer anything for a check that passed, because a
 * passing check left no trace in the payload at all.
 *
 * Two sources of buttons, in this order:
 *   1. DOCTOR_ACTIONS_BY_ID — client-side actions for ids this build knows.
 *      Navigation and the discovery/scan helpers live here because they are
 *      dashboard behaviour, not server state.
 *   2. check.action — the server's own declared remediation. A newer agent
 *      shipping a check this page has never heard of still gets a working
 *      button, which is exactly what the old regex table could not do.
 * A check with neither keeps its remediation text and no button, rather than
 * being wired to a plausible-looking action that might not be the right one.
 */
const DOCTOR_ACTIONS_BY_ID = {
  "backends.healthy": [{ label: "Run discovery", run: () => runDiscover() }],
  "backends.auth_required": [
    { label: "Open Backends", run: () => navigate("backends") },
  ],
  "cloud.provider_key": [{ label: "Open Cloud", run: () => navigate("cloud") }],
  "cloud.unknown_provider": [
    { label: "Open Cloud", run: () => navigate("cloud") },
  ],
  "swarm.mdns_available": [
    { label: "Scan LAN for peers", run: () => runPeersScan(true) },
    { label: "Open Network", secondary: true, run: () => navigate("network") },
  ],
  "swarm.open_lan_no_token": [
    { label: "Open Network", run: () => navigate("network") },
  ],
  "swarm.token_but_open_inference": [
    { label: "Open Network", run: () => navigate("network") },
  ],
  "swarm.peer_config": [{ label: "Open Peers", run: () => navigate("peers") }],
  "agent.gateway_advertise": [
    { label: "Open Network", run: () => navigate("network") },
  ],
};

/** POST a server-declared `config_patch` / `admin_post` action and refresh. */
async function doctorRunDeclaredAction(action) {
  const endpoint = String(action.endpoint || "");
  // Only ever POST to the endpoint the payload named, and only when it is an
  // admin route on this agent. The server declares the body; it does not gain
  // an "apply fix id" executor, so there is nothing here that can be steered
  // into an arbitrary URL.
  if (!endpoint.startsWith("/netllm/v1/")) {
    showToast("Doctor action names an unexpected endpoint");
    return;
  }
  try {
    await api(endpoint, {
      method: String(action.method || "POST").toUpperCase(),
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(asObject(action.params)),
    });
    showToast("Applied — re-running checks");
    await refresh();
  } catch (e) {
    showToast("Fix failed: " + e.message);
  }
}

/** Buttons for one check row: known-id actions first, server-declared next. */
function doctorActionsFor(check) {
  const known = DOCTOR_ACTIONS_BY_ID[check.id];
  if (known) return known;
  const action = asObject(check.action);
  if (action.kind === "navigate" && action.target) {
    return [
      { label: action.label || "Open", run: () => navigate(String(action.target)) },
    ];
  }
  if (action.kind === "config_patch" || action.kind === "admin_post") {
    return [
      {
        label: action.label || "Apply fix",
        run: () => doctorRunDeclaredAction(action),
      },
    ];
  }
  return [];
}

/** One finding row: severity dot, title, remediation, and its fix buttons. */
function doctorFinding(kind, title, detail, actions) {
  const row = el("div", "finding");
  row.appendChild(statusDot(kind));
  const body = el("div", "finding-body");
  body.appendChild(textEl("div", "finding-title", title));
  if (detail) body.appendChild(textEl("div", "finding-detail", detail));
  if (actions && actions.length) {
    const bar = el("div", "row");
    actions.forEach((action) => {
      bar.appendChild(
        button(action.label, action.secondary ? "secondary" : "primary", (e) => {
          // Navigation actions re-render this page out from under the button, so
          // it is only re-enabled on the async paths that stay here.
          const btn = e.currentTarget;
          const result = action.run();
          if (result && typeof result.finally === "function") {
            btn.disabled = true;
            result.finally(() => {
              if (btn.isConnected) btn.disabled = false;
            });
          }
        })
      );
    });
    body.appendChild(bar);
  }
  row.appendChild(body);
  return row;
}

/* /doctor is admin data, but it is still wire data: `checks` and `issues` have
 * both arrived as a string and as [null, null]. Everything below is rendered
 * field-by-field, so drop anything that has no fields. */
function doctorCheckRows(doctor) {
  return asArray(doctor?.checks)
    .filter((c) => c && typeof c === "object")
    .map((c) => asObject(c));
}

function doctorIssueRows(doctor) {
  return asArray(doctor?.issues)
    .filter(Boolean)
    .map((i) => (typeof i === "object" ? i : { title: String(i) }));
}

/** Failing rows worth a red dot. Falls back to `issues` on an older agent. */
function doctorProblems(doctor) {
  const checks = doctorCheckRows(doctor);
  if (checks.length) {
    return checks.filter((c) => c.ok === false && c.severity === "error");
  }
  return doctorIssueRows(doctor).map((i) => ({
    id: "",
    title: i.title,
    fix: i.fix,
    severity: "error",
    ok: false,
    action: {},
  }));
}

/** Advisory rows. Falls back to the flat `notes` strings on an older agent. */
function doctorAdvisories(doctor) {
  const checks = doctorCheckRows(doctor);
  if (checks.length) {
    return checks.filter((c) => c.ok === false && c.severity === "warn");
  }
  return asArray(doctor?.notes)
    .filter(Boolean)
    .map((n) => ({ id: "", title: String(n), detail: "", severity: "warn", ok: false }));
}

/** Whether the passed-check inventory is expanded. View state, not config. */
let doctorShowPassed = false;

function doctorReportText() {
  const doctor = asObject(state.doctor);
  const lines = [`netllm doctor — ${state.status?.hostname || "agent"}`];
  const listen = state.status?.listen_url || state.status?.listen;
  if (listen) lines.push(`listen: ${listen}`);
  lines.push("");
  const checks = doctorCheckRows(doctor);
  if (checks.length) {
    const passed = checks.filter((c) => c.ok).length;
    lines.push(`${checks.length} checks · ${passed} passed`, "");
    // The whole inventory, ids included: a support bundle is diffed on the id,
    // and "which checks ran" is half of what a bug report needs.
    checks.forEach((check) => {
      const mark = check.ok ? "ok  " : check.severity === "warn" ? "warn" : "FAIL";
      lines.push(`  [${mark}] ${check.id}${check.subject ? ` (${check.subject})` : ""}`);
      lines.push(`         ${check.title || ""}`);
      if (!check.ok && check.detail && check.detail !== check.title) {
        lines.push(`         ${check.detail}`);
      }
      if (!check.ok && check.fix) lines.push(`         fix: ${check.fix}`);
    });
    return lines.join("\n");
  }
  // Older agent: prose only.
  const issues = doctorIssueRows(doctor);
  lines.push(issues.length ? `${issues.length} issue(s):` : "No issues reported.");
  issues.forEach((issue) => {
    lines.push(`  ! ${issue.title || "issue"}`);
    if (issue.fix) lines.push(`    fix: ${issue.fix}`);
  });
  const notes = asArray(doctor.notes).filter(Boolean);
  if (notes.length) {
    lines.push("", `${notes.length} note(s):`);
    notes.forEach((note) => lines.push(`  - ${note}`));
  }
  return lines.join("\n");
}

const DOCTOR_CLI = [
  ["netllm doctor", "full checks"],
  ["netllm doctor --verbose", "list every check, passed included"],
  ["netllm test", "1-token latency probe"],
  ["netllm test --api anthropic", "messages probe"],
  ["netllm models --lan", "remote models"],
  ["netllm gateway", "promote role"],
  ["netllm config-edit", "open in $EDITOR"],
];

function renderDoctorPage(root) {
  const doctor = state.doctor;
  const checks = doctorCheckRows(doctor);
  const problems = doctorProblems(doctor);
  const advisories = doctorAdvisories(doctor);
  const passed = checks.filter((c) => c.ok);
  const ok = !!doctor?.ok;

  const summary = [];
  if (checks.length) {
    // The inventory exists now, so the subtitle can count what ran rather than
    // only what broke.
    summary.push(`${checks.length} checks · ${passed.length} passed`);
  }
  summary.push(problems.length ? `${problems.length} needing attention` : "no issues");
  if (advisories.length) {
    summary.push(`${advisories.length} note${advisories.length === 1 ? "" : "s"}`);
  }
  summary.push(`checked ${timeAgo(state.lastUpdatedAt)}`);

  const actions = el("div", "row");
  actions.append(
    doctor ? pill(ok ? "ok" : "warn", ok ? "All checks passed" : "Issues found") : pill("neutral", "Unavailable"),
    button("Copy report", "secondary", () =>
      copyText(doctorReportText(), "Doctor report copied")
    ),
    button("Run all checks", "primary", async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      try {
        await refresh();
      } catch (err) {
        showToast("Doctor failed: " + err.message);
        if (btn.isConnected) btn.disabled = false;
      }
    })
  );
  pageHeader(root, "Doctor & test", summary.join(" · "), actions);

  const columns = el("div", "grid-2");
  const left = el("div", "stack");
  const right = el("div", "stack");
  columns.append(left, right);
  root.appendChild(columns);

  /* ---- findings ---- */

  const findingsBody = panel(
    left,
    "Findings",
    problems.length ? `${problems.length} to fix` : null,
    problems.length ? "accent-danger" : ok ? "accent-ok" : null
  );

  if (!doctor) {
    findingsBody.appendChild(
      textEl("p", "empty", "Doctor has not reported yet — try Run all checks.")
    );
  } else if (doctor.error) {
    findingsBody.appendChild(textEl("p", "empty", doctor.error));
  } else if (!problems.length) {
    findingsBody.appendChild(
      doctorFinding(
        "ok",
        "No issues reported",
        checks.length
          ? `All ${checks.length} checks came back clean.`
          : "Every check the agent runs came back clean."
      )
    );
  } else {
    problems.forEach((check) => {
      findingsBody.appendChild(
        doctorFinding(
          "error",
          check.title || "Issue",
          // `detail` is the evidence, `fix` the remediation. Older agents send
          // only `fix`, so fall back to it rather than rendering nothing.
          [check.detail && check.detail !== check.title ? check.detail : "", check.fix]
            .filter(Boolean)
            .join(" — "),
          doctorActionsFor(check)
        )
      );
    });
  }

  if (advisories.length) {
    const notesBody = panel(
      left,
      "Notes",
      "Advisory — nothing is broken",
      "accent-warn"
    );
    advisories.forEach((check) => {
      notesBody.appendChild(
        doctorFinding(
          "warn",
          check.detail || check.title || "Note",
          check.fix || "",
          doctorActionsFor(check)
        )
      );
    });
  }

  /* ---- passed inventory ---- */

  if (passed.length) {
    const passedBody = panel(
      left,
      "Passed",
      `${passed.length} check${passed.length === 1 ? "" : "s"}`,
      "accent-ok"
    );
    const toggle = el("div", "row");
    toggle.appendChild(
      button(doctorShowPassed ? "Hide" : "Show what passed", "secondary small", () => {
        doctorShowPassed = !doctorShowPassed;
        render();
      })
    );
    passedBody.appendChild(toggle);
    if (doctorShowPassed) {
      passed.forEach((check) => {
        passedBody.appendChild(
          doctorFinding("ok", check.title || check.id, check.detail || "")
        );
      });
    }
  }

  /* ---- reference strip ---- */

  const cliBody = panel(right, "CLI reference", "Run these in a terminal");
  DOCTOR_CLI.forEach(([cmd, desc]) => {
    const row = el("div", "row");
    const code = codeEl(cmd);
    code.classList.add("spacer");
    row.append(
      code,
      textEl("span", "muted", desc),
      button("Copy", "secondary small", () => copyText(cmd, "Command copied"))
    );
    cliBody.appendChild(row);
  });

  const envBody = panel(
    right,
    "Client environment",
    "Point an editor or SDK at this agent"
  );
  if (state.envText) {
    // <pre> so the export lines keep their newlines without a new CSS class.
    envBody.appendChild(textEl("pre", "inset mono", state.envText));
    const envActions = el("div", "row");
    envActions.append(
      button("Copy", "primary", () => copyText(state.envText, "Client env copied")),
      button("Snippets", "secondary", () => navigate("integrations"))
    );
    envBody.appendChild(envActions);
  } else {
    envBody.appendChild(textEl("p", "empty", "Client env unavailable."));
  }
}

registerPage("doctor", renderDoctorPage);
