/* Preferences page — design 2e. The old UI tab, plus updates, the logging
   location and About, in one place. Nothing here changes routing. */

/** Browser-local appearance preference — see applyTheme/storedTheme. */
const PREFS_THEME_OPTIONS = [
  ["system", "System"],
  ["light", "Light"],
  ["dark", "Dark"],
];

/** `ui.menubar_*` only affects the macOS menubar app, so it gets its own
    labelled subsection rather than sitting among the cross-platform rows. */
const PREFS_MENUBAR_PREFIX = "menubar_";

/** Labels copied verbatim from SettingsWindowView.swift so the two surfaces
    name the same switch the same way. */
const PREFS_MENUBAR_LABELS = {
  menubar_show_cpu: "Show CPU gauge",
  menubar_show_gpu: "Show GPU gauge",
  menubar_show_mem: "Show memory gauge",
  menubar_show_live: "Show live throughput (LIV)",
  menubar_models_favorites_only: "Models menu: favorites only",
  menubar_merge_gauges: "Merge gauges into one menubar item",
};

function prefsUiFieldNames() {
  // A malformed /config/schema can make `fields` a scalar, hold nulls, or carry
  // entries with no name — and every caller below does name.startsWith(...).
  return asArray(state.configSchema?.sections?.ui?.fields)
    .filter((f) => f && typeof f === "object" && typeof f.name === "string")
    .map((f) => f.name);
}

/**
 * `{name: {hidden: true}}` for every `ui.*` field `keep` rejects. Derived from
 * the schema rather than a hard-coded name list: a field added to UiConfig
 * then shows up in Behaviour on its own instead of silently losing its
 * control (which is what the Axis D parity kit fails on).
 */
function prefsHiddenUiOverrides(keep) {
  const overrides = {};
  prefsUiFieldNames().forEach((name) => {
    if (!keep(name)) overrides[name] = { hidden: true };
  });
  return overrides;
}

function prefsSchemaMissing(body) {
  body.appendChild(
    textEl(
      "p",
      "empty",
      "Config schema unavailable — the agent is unreachable or predates " +
        "/netllm/v1/config/schema. Refresh to retry."
    )
  );
}

/* ---------------- appearance ---------------- */

function prefsAppearanceSection(root) {
  const body = panel(
    root,
    "Appearance",
    "This browser only"
  );

  const field = el("div", "field");
  const labelId = "prefs-theme-label";
  const label = textEl("div", "field-label", "Theme");
  label.id = labelId;
  field.appendChild(label);

  const group = el("div", "segmented");
  group.setAttribute("role", "group");
  group.setAttribute("aria-labelledby", labelId);
  const current = storedTheme();
  const buttons = PREFS_THEME_OPTIONS.map(([value, text]) => {
    const b = button(text, "", () => {
      applyTheme(value);
      // Repaint the group in place rather than re-rendering the page: the
      // theme is not config, so nothing else on the page changes.
      buttons.forEach((other) =>
        other.setAttribute("aria-pressed", String(other.dataset.theme === value))
      );
    });
    b.dataset.theme = value;
    b.setAttribute("aria-pressed", String(value === current));
    group.appendChild(b);
    return b;
  });
  field.appendChild(group);
  field.appendChild(
    textEl(
      "div",
      "field-help",
      "Stored in this browser. It does not touch the config file, and the " +
        "macOS app follows the system setting independently."
    )
  );
  body.appendChild(field);
}

/* ---------------- behaviour (every ui.* field) ---------------- */

function prefsBehaviourSection(root) {
  const body = panel(root, "Behaviour", "Saved to the config file");
  if (!state.configSchema?.sections?.ui) {
    prefsSchemaMissing(body);
    return;
  }

  // Field order, widgets and defaults all come from
  // GET /netllm/v1/config/schema; overrides only carry what the schema
  // cannot express (platform-specific wording and help text).
  renderSchemaForm(
    body,
    "ui",
    Object.assign(prefsHiddenUiOverrides((n) => !n.startsWith(PREFS_MENUBAR_PREFIX)), {
      auto_start_on_launch: {
        label: "Auto-start on launch (macOS menubar)",
        help: "Off means you start the agent yourself from the menubar each time.",
      },
      check_for_updates_automatically: {
        // Same side effect the old UI tab carried: the hourly poll starts and
        // stops with the toggle, without waiting for a Save.
        label: "Check for updates automatically",
        help: "Hourly, and only while this page is visible.",
        onChange: (v) => (v ? startUpdatePolling() : stopUpdatePolling()),
      },
      log_dir: {
        label: "Log directory",
        help: "Blank uses the platform default. Applied when the agent restarts.",
      },
      model_favorites: {
        label: "Favourite models",
        help: "Pinned to the top of the menubar model picker.",
      },
    })
  );

  const menubarOverrides = prefsHiddenUiOverrides((n) =>
    n.startsWith(PREFS_MENUBAR_PREFIX)
  );
  const menubarNames = prefsUiFieldNames().filter((n) =>
    n.startsWith(PREFS_MENUBAR_PREFIX)
  );
  // Folded by default: not one of these controls does anything on the web
  // surface the user is looking at. They are saved here so the menubar app
  // picks them up, which is a reason to keep them reachable, not a reason to
  // put eight switches in front of someone on Linux.
  const menubar = collapsiblePanel(body, "macOS menu bar", null, {
    boxClass: "inset",
    storageKey: "prefs.menubar",
    defaultOpen: false,
    forceOpen: menubarNames.some((n) => draftDiffers(`ui.${n}`)),
    forceReason: "unsaved edits",
    summary: `${menubarNames.length} menubar setting${
      menubarNames.length === 1 ? "" : "s"
    } · no effect on this page`,
  });
  menubar.appendChild(
    textEl(
      "div",
      "field-help",
      "These only affect the macOS menubar app. They are saved to the same " +
        "config file on every platform."
    )
  );
  Object.entries(PREFS_MENUBAR_LABELS).forEach(([name, label]) => {
    if (menubarOverrides[name]?.hidden) return;
    menubarOverrides[name] = { label };
  });
  // menubar_merge_gauges is carried by every surface but read by none yet —
  // say so rather than implying it does something.
  if (menubarOverrides.menubar_merge_gauges) {
    menubarOverrides.menubar_merge_gauges.help =
      "Stored for the menubar app; no surface reads it yet.";
  }
  // No body.appendChild(menubar): collapsiblePanel already placed the box in
  // `body` and handed back its interior.
  renderSchemaForm(menubar, "ui", menubarOverrides);
}

/* ---------------- updates ---------------- */

function prefsUpdateNote(info) {
  if (!info) return pill("neutral", "Status unknown");
  if (info.update_available) return pill("warn", `v${info.latest} available`);
  // A failed check is a real failure, not an absence of news: the agent could
  // not reach the release feed, so "no update available" below is unproven.
  // Rendering it neutral read as "fine, nothing to do".
  if (info.error) return pill("warn", "Check failed");
  return pill("ok", info.current ? `Up to date · v${info.current}` : "Up to date");
}

function prefsUpdatesSection(root) {
  const info = state.updateInfo;
  const body = panel(root, "Updates", prefsUpdateNote(info));

  const head = el("div", "row-between");
  const left = el("div");
  left.appendChild(textEl("div", "field-label", "Installed version"));
  left.appendChild(
    textEl(
      "div",
      "mono",
      info?.current || state.versionInfo?.version || state.status?.version || "—"
    )
  );
  head.appendChild(left);

  const actions = el("div", "row");
  actions.appendChild(
    button("Check now", "secondary", (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      loadUpdateCheck(true).then(() => {
        showToast("Update check complete");
        render();
      });
    })
  );
  if (info?.release_notes_url) {
    const notes = el("a", "btn secondary");
    // The update payload comes from whoever answers the release check, so this
    // URL is untrusted: setSafeHref admits only http(s) and otherwise leaves
    // the anchor inert (no href, aria-disabled) instead of carrying a live
    // `javascript:` URL that one missing target=_blank would make executable.
    if (setSafeHref(notes, info.release_notes_url)) {
      notes.target = "_blank";
      notes.rel = "noopener";
    }
    notes.textContent = "Release notes";
    actions.appendChild(notes);
  }
  head.appendChild(actions);
  body.appendChild(head);

  if (!info) {
    body.appendChild(
      textEl("p", "empty", "Update status unavailable — the check has not run yet.")
    );
    return;
  }

  if (info.error && !info.update_available) {
    body.appendChild(textEl("p", "empty", info.error));
    return;
  }

  const detail = el("div", "inset");
  if (info.update_available) {
    detail.appendChild(
      textEl("div", "", `Update available: v${info.latest} (you have v${info.current})`)
    );
    if (info.prerelease) {
      detail.appendChild(textEl("div", "field-help", "This release is a prerelease."));
    }
    if (info.upgrade_hint) {
      const row = el("div", "row");
      row.appendChild(codeEl(info.upgrade_hint));
      row.appendChild(
        button("Copy", "small secondary", () =>
          copyText(info.upgrade_hint, "Copied upgrade command")
        )
      );
      detail.appendChild(row);
    } else if (info.download_url) {
      const dl = el("a", "btn");
      // Same untrusted update payload as release_notes_url — validate the
      // scheme before it reaches href.
      if (setSafeHref(dl, info.download_url)) {
        dl.target = "_blank";
        dl.rel = "noopener";
      }
      dl.textContent = info.asset_name
        ? `Download ${info.asset_name}`
        : "Download update";
      detail.appendChild(dl);
    }
    // Say when there is no checksum rather than omitting the row. A missing
    // sha256 used to render as nothing at all, which reads identically to
    // "this build was not verified because we didn't look" — and the user
    // cannot verify a download whose absence of a checksum is invisible.
    if (info.download_url) {
      const sha = el("div", "row");
      sha.appendChild(textEl("span", "field-label", "sha256"));
      if (info.sha256) {
        sha.appendChild(textEl("span", "mono", info.sha256));
      } else {
        sha.appendChild(
          textEl("span", "text-warn", "not published for this release")
        );
        sha.appendChild(
          textEl(
            "span",
            "field-help",
            "The download cannot be checksum-verified."
          )
        );
      }
      detail.appendChild(sha);
    }
  } else {
    detail.appendChild(
      textEl("div", "muted", `You're on the latest version (v${info.current}).`)
    );
  }
  body.appendChild(detail);

  body.appendChild(
    textEl(
      "p",
      "field-help",
      "Automatic checks follow the Behaviour toggle above and only run while " +
        "this page is visible."
    )
  );
}

/* ---------------- logs location ---------------- */

function prefsLogsSection(root) {
  const body = panel(root, "Logs location", "Diagnostics");
  const dir = String(state.configDraft?.ui?.log_dir || "").trim();

  const row = el("div", "row-between");
  const left = el("div");
  left.appendChild(textEl("div", "field-label", "Log directory"));
  left.appendChild(textEl("div", "mono", dir || "Default for this platform"));
  row.appendChild(left);

  const actions = el("div", "row");
  if (dir) {
    actions.appendChild(
      button("Copy path", "secondary", () => copyText(dir, "Log directory copied"))
    );
  }
  actions.appendChild(button("Open logs", "secondary", () => navigate("logs")));
  row.appendChild(actions);
  body.appendChild(row);

  body.appendChild(
    textEl(
      "p",
      "field-help",
      "Change the directory under Behaviour. Unsaved edits show here as soon " +
        "as you type; the agent reopens its log file on restart."
    )
  );
}

/* ---------------- about ---------------- */

function prefsAboutRow(body, label, value, mono) {
  const row = el("div", "row-between");
  row.appendChild(textEl("span", "muted", label));
  row.appendChild(textEl("span", mono ? "mono" : "", value == null ? "—" : value));
  body.appendChild(row);
}

function prefsAboutSection(root) {
  const body = panel(root, "About", "llm-swarm-router");
  const info = asObject(state.versionInfo);
  const version = info.version || state.status?.version || null;
  const agentId = state.config?.agent?.agent_id || state.status?.agent_id || null;
  const sdks = asObject(info.sdk_versions);
  // The macOS About panel (AboutView.swift) shows exactly this: brand mark,
  // display name, version, tagline, and the CLI + dashboard pointers.
  const dashboardUrl = `${window.location.origin}/ui/`;

  const head = el("div", "row");
  head.appendChild(brandLogoEl(40));
  const title = el("div");
  title.appendChild(textEl("div", "", "llm-swarm-router"));
  title.appendChild(
    textEl("div", "muted", version ? `v${version} · MIT` : "MIT")
  );
  head.appendChild(title);
  body.appendChild(head);

  body.appendChild(
    textEl("p", "panel-desc", "Mesh router for local LLM backends")
  );

  const rows = el("div", "stack");
  prefsAboutRow(rows, "Version", version ? `v${version}` : null);
  prefsAboutRow(rows, "Build", info.build);
  prefsAboutRow(rows, "Platform", info.platform);
  prefsAboutRow(rows, "Install method", info.install_method);
  prefsAboutRow(rows, "OpenAI SDK", sdks.openai ? `v${sdks.openai}` : "not installed");
  prefsAboutRow(
    rows,
    "Anthropic SDK",
    sdks.anthropic ? `v${sdks.anthropic}` : "not installed"
  );
  prefsAboutRow(rows, "Agent ID", agentId, true);
  body.appendChild(rows);

  const pointers = el("div", "inset");
  const cli = el("div", "row");
  cli.appendChild(textEl("span", "muted", "CLI"));
  cli.appendChild(codeEl("netllm"));
  pointers.appendChild(cli);
  const dash = el("div", "row");
  dash.appendChild(textEl("span", "muted", "Dashboard"));
  dash.appendChild(textEl("span", "mono", dashboardUrl));
  pointers.appendChild(dash);
  body.appendChild(pointers);

  const actions = el("div", "row");
  if (state.updateInfo?.release_notes_url) {
    const notes = el("a", "btn secondary");
    // Untrusted update payload — scheme-checked before it reaches href.
    if (setSafeHref(notes, state.updateInfo.release_notes_url)) {
      notes.target = "_blank";
      notes.rel = "noopener";
    }
    notes.textContent = "Release notes";
    actions.appendChild(notes);
  }
  actions.appendChild(
    button("Copy versions", "secondary", () => {
      const lines = [
        `llm-swarm-router ${version ? `v${version}` : "(version unknown)"}`,
        `platform: ${info.platform || "unknown"}`,
        `install: ${info.install_method || "unknown"}`,
        `openai sdk: ${sdks.openai || "not installed"}`,
        `anthropic sdk: ${sdks.anthropic || "not installed"}`,
        `agent id: ${agentId || "unknown"}`,
      ];
      copyText(lines.join("\n"), "Version details copied");
    })
  );
  body.appendChild(actions);
}

/* ---------------- page ---------------- */

function renderPreferencesPage(root) {
  pageHeader(
    root,
    "Preferences",
    "App behaviour, updates and diagnostics — nothing here affects routing"
  );
  // About first, by request. Reordering this one array moves the jump rail
  // and the body together — railLayout() builds both from it, and marks the
  // first entry active/aria-current on the initial render.
  railLayout(root, [
    { id: "prefs-about", label: "About", render: prefsAboutSection },
    { id: "prefs-appearance", label: "Appearance", render: prefsAppearanceSection },
    { id: "prefs-behaviour", label: "Behaviour", render: prefsBehaviourSection },
    { id: "prefs-updates", label: "Updates", render: prefsUpdatesSection },
    { id: "prefs-logs", label: "Logs location", render: prefsLogsSection },
  ]);
}

registerPage("preferences", renderPreferencesPage);
