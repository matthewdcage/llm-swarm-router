/* Logs page — design 2c. */

/*
 * GET /netllm/v1/logs now returns `records[]` — {line_no, ts, level,
 * level_label, logger, message, raw} — parsed server-side, next to the format
 * string that produced the line (netllm_agent.admin.parse_log_line). This page
 * used to hold its own copy of those regexes, which meant the day the
 * formatter changed the page would quietly mis-column every line and nothing
 * would fail.
 *
 * The regexes below are the *fallback* for an agent that predates `records`,
 * and only that. They are not the primary path and must not grow: a new log
 * shape is taught to the server, not to this file.
 */
const LOGS_STD_LINE =
  /^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})(?:[.,]\d+)?\s+([A-Za-z]{3,9})\s+([\w.-]+):\s?([\s\S]*)$/;
const LOGS_BARE_LEVEL = /^([A-Z]{3,9}):\s+([\s\S]*)$/;

/** Python level names -> the four `.log-level` variants dashboard.css styles. */
const LOGS_LEVEL_ALIASES = {
  warning: "warn",
  warn: "warn",
  error: "error",
  critical: "error",
  fatal: "error",
  info: "info",
  debug: "debug",
  trace: "debug",
};
const LOGS_LEVEL_ORDER = ["error", "warn", "info", "debug"];

/* The server caps its own tail at ?tail=200, but the page had no cap of its own:
 * a hostile or proxied /logs returning 50 000 lines cost ~1.1 s to render, 200 k
 * DOM nodes, and ~1.3 s per filter keystroke (paint() rebuilds the whole
 * stream). Filtering and the counts still run over every parsed line — only the
 * number of rows put in the DOM is bounded, and the footer says so.
 *
 * Paging works *with* that cap rather than against it: "Load older" prepends a
 * page to the buffer, filtering and counts see all of it, and the cap keeps
 * governing how much reaches the DOM. Loading five pages back does not turn
 * into a 2500-node repaint. */
const LOGS_RENDER_CAP = 500;
/* Coalesce a burst of typing into one repaint. */
const LOGS_SEARCH_DEBOUNCE_MS = 120;
/** How many older lines one "Load older" click asks for. */
const LOGS_PAGE_SIZE = 200;

// Module-local view state (not config, so it deliberately stays out of `state`):
// the caret position to restore after the core's 10s poll re-renders the page
// under the user's fingers, and whether the stream sticks to the newest line.
let logsSearchCaret = null;
let logsFollowTail = true;
/** Stream scroll to restore after a poll re-render when follow-tail is off. */
let logsStreamScrollTop = 0;
/** Pending debounced repaint, cleared whenever the page is rebuilt. */
let logsSearchTimer = null;
/** Source facet selection; empty means "every source". */
const logsSourceFilter = new Set();

/*
 * Paging state.
 *
 * The core's `loadLogs()` refetches a fixed `?tail=200` every 10 s and replaces
 * `state.logs` wholesale, so anything older than the newest page cannot live
 * there — the next poll would erase it. It lives here instead, keyed on the
 * absolute `line_no` the server assigns, and the render merges the two: older
 * pages first, then whatever the poll last put in `state.logs`. That is what
 * makes "Load older" survive the poll instead of flickering away under it.
 */
let logsOlderRecords = [];
/** `before` cursor for the next older page; null when at the start of the file. */
let logsOlderCursor = null;
/** File identity for the buffer above, so a rotation cannot splice two files. */
let logsBufferFile = "";
let logsBufferTotal = 0;
let logsLoadingOlder = false;

function logsResetPaging() {
  logsOlderRecords = [];
  logsOlderCursor = null;
}

/** Fallback parser for an agent that does not send `records`. */
function logsParseLine(raw) {
  const line = String(raw == null ? "" : raw);
  const std = LOGS_STD_LINE.exec(line);
  if (std) {
    return {
      raw: line,
      line_no: null,
      stamp: std[1],
      level: LOGS_LEVEL_ALIASES[std[2].toLowerCase()] || "",
      levelLabel: std[2],
      source: std[3],
      message: std[4],
    };
  }
  const bare = LOGS_BARE_LEVEL.exec(line);
  if (bare && LOGS_LEVEL_ALIASES[bare[1].toLowerCase()]) {
    return {
      raw: line,
      line_no: null,
      stamp: "",
      level: LOGS_LEVEL_ALIASES[bare[1].toLowerCase()],
      levelLabel: bare[1],
      source: "",
      message: bare[2],
    };
  }
  return {
    raw: line,
    line_no: null,
    stamp: "",
    level: "",
    levelLabel: "",
    source: "",
    message: line,
  };
}

/** A server `records[]` row in this page's shape. Wire data: type-check each field. */
function logsFromRecord(record) {
  const r = asObject(record);
  const level = typeof r.level === "string" ? r.level : "";
  const message = typeof r.message === "string" ? r.message : "";
  const raw = typeof r.raw === "string" ? r.raw : message;
  return {
    raw,
    line_no: Number.isFinite(r.line_no) ? r.line_no : null,
    stamp: typeof r.ts === "string" ? r.ts : "",
    // The server already normalised WARNING/CRITICAL/etc; anything outside the
    // four styled variants is shown without a colour rather than mislabelled.
    level: LOGS_LEVEL_ORDER.includes(level) ? level : "",
    levelLabel: typeof r.level_label === "string" ? r.level_label : "",
    source: typeof r.logger === "string" ? r.logger : "",
    message,
  };
}

/** The whole visible buffer: accumulated older pages + the newest page. */
function logsBuffer(logs) {
  const records = asArray(logs.records);
  if (records.length || Number.isFinite(logs.total_lines)) {
    const newest = records.map(logsFromRecord);
    const firstNew = Number.isFinite(logs.first_line_no) ? logs.first_line_no : null;
    // Drop any accumulated row the newest page now covers, so a line never
    // renders twice when the poll's window widens over it.
    const older =
      firstNew === null
        ? logsOlderRecords
        : logsOlderRecords.filter((l) => l.line_no !== null && l.line_no < firstNew);
    return older.concat(newest);
  }
  // Older agent: raw text only, and therefore no line numbers and no paging.
  const tail = logs.tail || logs.lines;
  const rawLines = typeof tail === "string" ? tail.split(/\r?\n/) : asArray(tail);
  return rawLines.map(logsParseLine);
}

/** "2026-08-10 14:02:11" -> "14:02:11"; anything shorter is shown as-is. */
function logsClockText(stamp) {
  if (!stamp) return "";
  const m = /(\d{2}:\d{2}:\d{2})/.exec(stamp);
  return m ? m[1] : stamp;
}

/** Last dotted segment of a logger name — "netllm_agent.service.core" -> "core". */
function logsSourceLabel(source) {
  if (!source) return "";
  const parts = source.split(".");
  return parts[parts.length - 1] || source;
}

function logsFormatBytes(bytes) {
  const n = Number(bytes);
  if (!Number.isFinite(n) || n < 0) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/** Fetch one page older than the current cursor and prepend it to the buffer. */
async function logsLoadOlder() {
  if (logsLoadingOlder || !logsOlderCursor) return;
  logsLoadingOlder = true;
  try {
    const page = asObject(
      await api(`/netllm/v1/logs?tail=${LOGS_PAGE_SIZE}&before=${logsOlderCursor}`)
    );
    const rows = asArray(page.records).map(logsFromRecord);
    // Server-assigned line numbers are absolute, so a page cannot overlap the
    // buffer; belt and braces, drop anything already held.
    const known = new Set(logsOlderRecords.map((l) => l.line_no));
    logsOlderRecords = rows
      .filter((l) => l.line_no !== null && !known.has(l.line_no))
      .concat(logsOlderRecords);
    logsOlderCursor = Number.isFinite(page.next_before) ? page.next_before : null;
  } catch (e) {
    showToast("Could not load older lines: " + e.message);
  } finally {
    logsLoadingOlder = false;
    if (state.page === "logs") render();
  }
}

function renderLogsPage(root) {
  const existingStream = document.querySelector("#page-logs .log-stream");
  if (existingStream && !logsFollowTail) {
    logsStreamScrollTop = existingStream.scrollTop;
  }
  if (logsSearchTimer) {
    clearTimeout(logsSearchTimer);
    logsSearchTimer = null;
  }
  const logs = asObject(state.logs);
  const logFile = logs.log_file || "";
  const totalLines = Number.isFinite(logs.total_lines) ? logs.total_lines : null;

  // A different file, or a file that got shorter, means rotation: the absolute
  // line numbers in the buffer now point into a file that no longer exists, so
  // splicing them onto the new tail would invent history.
  if (logFile !== logsBufferFile || (totalLines !== null && totalLines < logsBufferTotal)) {
    logsResetPaging();
    logsBufferFile = logFile;
  }
  if (totalLines !== null) logsBufferTotal = totalLines;
  // Seed the cursor from the newest page every render: the poll moves the
  // window forward, and the cursor must follow it until the user pages back.
  if (!logsOlderRecords.length) {
    logsOlderCursor = Number.isFinite(logs.next_before) ? logs.next_before : null;
  }

  const parsed = logsBuffer(logs);
  const paged = Number.isFinite(logs.total_lines);

  const logDir = logs.log_dir || state.configDraft?.ui?.log_dir || "";
  const subtitleParts = [];
  if (logFile) subtitleParts.push(logFile);
  if (logs.exists) subtitleParts.push(logsFormatBytes(logs.size_bytes));
  if (totalLines !== null) {
    subtitleParts.push(
      `${parsed.length} of ${totalLines} line${totalLines === 1 ? "" : "s"}`
    );
  } else {
    subtitleParts.push(
      parsed.length ? `tailing last ${parsed.length} lines` : "no lines yet"
    );
  }

  const actions = el("div", "row");
  actions.append(
    button("Copy all", "secondary", () =>
      copyText(parsed.map((l) => l.raw).join("\n"), "Log lines copied")
    ),
    button("Copy path", "secondary", () =>
      copyText(logFile || logDir, "Log path copied")
    ),
    button("Copy folder", "secondary", () => copyText(logDir, "Log directory copied"))
  );
  if (logs.download_url && logs.exists) {
    // A real link, not a fetch: the browser streams the file straight to disk
    // and never holds an unbounded log in memory. Admin-gated server-side.
    const link = textEl("a", "btn secondary", "Download");
    link.href = String(logs.download_url);
    link.setAttribute("download", "agent.log");
    actions.appendChild(link);
  }
  actions.appendChild(
    button("Refresh", "primary", (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      loadLogs().then(() => {
        if (state.page === "logs") render();
      });
    })
  );
  pageHeader(root, "Logs", subtitleParts.join(" · "), actions);

  if (!state.logs) {
    root.appendChild(textEl("p", "empty", "Loading logs…"));
    return;
  }
  if (logs.error) {
    const body = panel(root, "Logs unavailable", null, "accent-warn");
    body.appendChild(textEl("p", "empty", logs.error));
    return;
  }

  const body = panel(root, null, null);

  /* ---- filter bar ---- */

  // Built first so the follow-tail toggle can scroll it, appended below the bar.
  const stream = el("div", "log-stream");
  stream.setAttribute("role", "log");
  stream.setAttribute("aria-label", "Agent log lines");
  stream.addEventListener("scroll", () => {
    if (!logsFollowTail) logsStreamScrollTop = stream.scrollTop;
  });

  const bar = el("div", "inset field-grid");

  const searchField = el("div", "field");
  const searchLabel = textEl("label", "field-label", "Filter lines");
  searchLabel.htmlFor = "logs-search";
  const search = el("input");
  search.type = "search";
  search.id = "logs-search";
  search.placeholder = "Filter lines…";
  search.value = state.logSearchText || "";
  search.oninput = () => {
    state.logSearchText = search.value;
    logsSearchCaret = search.selectionStart;
    // paint() is O(rendered rows) and runs on every keystroke; debounce it so a
    // long tail does not make the field feel stuck.
    if (logsSearchTimer) clearTimeout(logsSearchTimer);
    logsSearchTimer = setTimeout(() => {
      logsSearchTimer = null;
      paint();
    }, LOGS_SEARCH_DEBOUNCE_MS);
  };
  search.onfocus = () => {
    logsSearchCaret = search.selectionStart;
  };
  // A blur fired because render() detached the node is not the user leaving the
  // field, so the caret intent survives the poll re-render.
  search.onblur = () => {
    if (search.isConnected) logsSearchCaret = null;
  };
  searchField.append(searchLabel, search);
  bar.appendChild(searchField);

  const levelField = el("div", "field");
  levelField.appendChild(textEl("div", "field-label", "Level"));
  const levelRow = el("div", "row");
  levelRow.setAttribute("role", "group");
  levelRow.setAttribute("aria-label", "Filter by log level");
  const levelChips = [];

  const allChip = textEl("button", "chip", "all");
  allChip.type = "button";
  allChip.onclick = () => {
    state.logLevelFilter.clear();
    paint();
  };
  levelRow.appendChild(allChip);

  LOGS_LEVEL_ORDER.forEach((level) => {
    const chip = el("button", "chip");
    chip.type = "button";
    chip.appendChild(document.createTextNode(level));
    const count = textEl("span", "mono", "");
    chip.appendChild(count);
    chip.onclick = () => {
      if (state.logLevelFilter.has(level)) state.logLevelFilter.delete(level);
      else state.logLevelFilter.add(level);
      paint();
    };
    levelRow.appendChild(chip);
    levelChips.push({ level, chip, count });
  });
  levelField.appendChild(levelRow);
  bar.appendChild(levelField);

  // Source chips come from the logger names actually present in the buffer, so
  // the facet never advertises a category this file cannot show.
  const sources = [];
  parsed.forEach((line) => {
    const label = logsSourceLabel(line.source);
    if (label && !sources.includes(label)) sources.push(label);
  });
  sources.sort();
  const sourceChips = [];
  if (sources.length) {
    const sourceField = el("div", "field");
    sourceField.appendChild(textEl("div", "field-label", "Source"));
    const sourceRow = el("div", "row");
    sourceRow.setAttribute("role", "group");
    sourceRow.setAttribute("aria-label", "Filter by log source");
    sources.forEach((label) => {
      const chip = textEl("button", "chip", label);
      chip.type = "button";
      chip.onclick = () => {
        if (logsSourceFilter.has(label)) logsSourceFilter.delete(label);
        else logsSourceFilter.add(label);
        paint();
      };
      sourceRow.appendChild(chip);
      sourceChips.push({ label, chip });
    });
    sourceField.appendChild(sourceRow);
    bar.appendChild(sourceField);
  }

  bar.appendChild(el("div", "spacer"));

  const followField = el("div", "field");
  followField.appendChild(textEl("div", "field-label", "Stream"));
  followField.appendChild(
    switchRow("Follow tail", logsFollowTail, (checked) => {
      logsFollowTail = checked;
      if (checked) stream.scrollTop = stream.scrollHeight;
    })
  );
  bar.appendChild(followField);

  const nodeField = el("div", "field");
  const nodeLabel = textEl("label", "field-label", "Node scope");
  nodeLabel.htmlFor = "logs-node";
  const nodeSelect = el("select");
  nodeSelect.id = "logs-node";
  const hostname = state.status?.hostname || "this node";
  const onlyOption = textEl("option", "", `${hostname} · this node`);
  onlyOption.value = state.logNodeFilter || "local";
  nodeSelect.appendChild(onlyOption);
  // GET /netllm/v1/logs reads this host's agent.log only — peer log proxying is
  // deferred (spec §3 tranche 3), so the control is present but inert.
  nodeSelect.disabled = true;
  nodeField.append(
    nodeLabel,
    nodeSelect,
    textEl("div", "field-help", "Peer logs are not aggregated yet.")
  );
  bar.appendChild(nodeField);

  body.appendChild(bar);

  /* ---- older-page control ---- */

  if (paged) {
    const pager = el("div", "row");
    if (logsOlderCursor) {
      pager.appendChild(
        button(
          logsLoadingOlder ? "Loading…" : `Load ${LOGS_PAGE_SIZE} older lines`,
          "secondary small",
          () => logsLoadOlder()
        )
      );
      pager.appendChild(
        textEl(
          "span",
          "field-help",
          `${logsOlderCursor - 1} older line${logsOlderCursor - 1 === 1 ? "" : "s"} in the file`
        )
      );
    } else {
      pager.appendChild(textEl("span", "field-help", "Start of file."));
    }
    if (logsOlderRecords.length) {
      pager.appendChild(
        button("Back to newest", "secondary small", () => {
          logsResetPaging();
          render();
        })
      );
    }
    body.appendChild(pager);
  }

  /* ---- stream ---- */

  body.appendChild(stream);

  const foot = el("div", "row-between");
  const footLeft = el("div", "row");
  footLeft.append(statusDot("ok"), textEl("span", "muted", "Live · refreshes every 10s"));
  const footRight = textEl("div", "muted mono", "");
  foot.append(footLeft, footRight);
  body.appendChild(foot);

  // The message column only gets its own source cell when the buffer actually
  // carries logger names; otherwise the 3-column CSS default is right.
  const template = sources.length ? "74px 58px 108px 1fr" : "74px 58px 1fr";

  function matchesText(line, needle) {
    return !needle || line.raw.toLowerCase().includes(needle);
  }

  function matchesSource(line) {
    if (!logsSourceFilter.size) return true;
    return logsSourceFilter.has(logsSourceLabel(line.source));
  }

  function paint() {
    const needle = (state.logSearchText || "").trim().toLowerCase();
    const scoped = parsed.filter((l) => matchesText(l, needle) && matchesSource(l));

    // Level counts are faceted: they describe what the *other* filters left,
    // which is what makes "warn 3" a useful thing to click.
    const counts = {};
    scoped.forEach((l) => {
      if (l.level) counts[l.level] = (counts[l.level] || 0) + 1;
    });
    levelChips.forEach(({ level, chip, count }) => {
      const n = counts[level] || 0;
      count.textContent = n ? ` ${n}` : "";
      chip.setAttribute("aria-pressed", String(state.logLevelFilter.has(level)));
      chip.disabled = n === 0 && !state.logLevelFilter.has(level);
    });
    allChip.setAttribute("aria-pressed", String(state.logLevelFilter.size === 0));
    sourceChips.forEach(({ label, chip }) => {
      chip.setAttribute("aria-pressed", String(logsSourceFilter.has(label)));
    });

    const visible = state.logLevelFilter.size
      ? scoped.filter((l) => state.logLevelFilter.has(l.level))
      : scoped;

    stream.textContent = "";
    if (!visible.length) {
      stream.appendChild(
        textEl(
          "p",
          "empty",
          logs.exists
            ? parsed.length
              ? "No lines match these filters."
              : "The log file is empty."
            : "No log output yet — the agent writes agent.log when it starts."
        )
      );
    }
    // Newest lines are the ones being read, so the cap keeps the tail end.
    const shown =
      visible.length > LOGS_RENDER_CAP ? visible.slice(-LOGS_RENDER_CAP) : visible;
    if (shown.length < visible.length) {
      stream.appendChild(
        textEl(
          "p",
          "field-help",
          `Showing the most recent ${shown.length} of ${visible.length} matching lines.`
        )
      );
    }
    shown.forEach((line) => {
      const row = el("div", "log-line");
      row.style.gridTemplateColumns = template;
      const time = textEl("span", "log-time", logsClockText(line.stamp) || "—");
      if (line.stamp) time.title = line.stamp;
      row.appendChild(time);
      row.appendChild(
        textEl(
          "span",
          line.level ? `log-level ${line.level}` : "log-level muted",
          line.levelLabel || "—"
        )
      );
      if (sources.length) {
        const src = textEl("span", "muted", logsSourceLabel(line.source) || "—");
        if (line.source) src.title = line.source;
        row.appendChild(src);
      }
      row.appendChild(textEl("span", "", line.message));
      stream.appendChild(row);
    });

    const footParts = [`${visible.length} of ${parsed.length} lines`];
    if (shown.length < visible.length) footParts.push(`${shown.length} rendered`);
    if (totalLines !== null) footParts.push(`${totalLines} in file`);
    else if (logs.truncated) footParts.push("file has more");
    if (logs.exists) footParts.push(logsFormatBytes(logs.size_bytes));
    footRight.textContent = footParts.join(" · ");

    if (logsFollowTail) stream.scrollTop = stream.scrollHeight;
    else stream.scrollTop = logsStreamScrollTop;
  }

  paint();

  if (logsSearchCaret !== null) {
    search.focus();
    const pos = Math.min(logsSearchCaret, search.value.length);
    search.setSelectionRange(pos, pos);
  }
}

registerPage("logs", renderLogsPage);
