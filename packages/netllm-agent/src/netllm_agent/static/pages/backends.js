/* Backends page — design 2a.
 *
 * Replaces the old flat dot list (dashboard.js renderBackendsTab /
 * renderBackendRow): one card per local endpoint, a table for everything
 * reached over the LAN, and — the point of the redesign — a plain-English
 * explanation of *why* a probe failed instead of a red dot and the word
 * "offline".
 *
 * Every number here comes from a real field on `state.status.backends[]`
 * (Backend / BackendHealth in netllm_core.models). The mockup's per-node
 * VRAM/GPU bars have no backing field; see scratchpad/gaps/backends.md.
 */

/** Scope filter. Module-level, not `state`: it is pure view state that has to
 *  survive the full re-render render() does on every poll, and no other page
 *  reads it. */
let backendsScope = "all";

const BACKEND_SCOPES = [
  { id: "all", label: "All" },
  { id: "local", label: "This machine" },
  { id: "remote", label: "Remote" },
];

/* ---------------- derivations from the real payload ---------------- */

/** Status + enabled collapsed into a dot/pill kind and a short label. */
function backendHealthView(b) {
  if (b.enabled === false) return { kind: "neutral", label: "disabled" };
  const health = b.health || {};
  const http = health.http_status;
  switch (health.status) {
    case "online":
      // health.py treats 401/403 as *reachable* so routing keeps the row —
      // but it serves nothing until a key is set, so it is not "serving".
      return http === 401 || http === 403
        ? { kind: "warn", label: "needs a key" }
        : { kind: "ok", label: "serving" };
    case "timeout":
      return { kind: "warn", label: "timed out" };
    case "offline":
      return { kind: "error", label: "offline" };
    case "error":
      return { kind: "error", label: "error" };
    default:
      return { kind: "neutral", label: "not probed" };
  }
}

/** `statusDot` wants "" for the neutral case; `pill` wants "neutral". */
function backendDotKind(kind) {
  return kind === "neutral" ? "" : kind;
}

function backendPanelModifier(kind) {
  if (kind === "ok") return "accent-ok";
  if (kind === "warn") return "accent-warn";
  if (kind === "error") return "accent-danger";
  return "";
}

/**
 * Why the probe ended up in this state, in words. Derived only from fields
 * that exist (`health.status`, `health.http_status`, `api_key_set`); the raw
 * `health.detail` string is rendered separately, verbatim.
 */
function probeExplanation(b) {
  if (b.enabled === false) {
    return "Disabled in routing.backends — the router neither probes it nor sends it traffic.";
  }
  const health = b.health || {};
  const http = health.http_status;
  switch (health.status) {
    case "online":
      if (http === 401 || http === 403) {
        return b.api_key_set
          ? `Reachable, but it answered ${http} to the stored key. The key is wrong, expired, or lacks access — replace it under Manual overrides.`
          : `Reachable, but it answered ${http}: it wants credentials and no per-URL API key is stored for this endpoint.`;
      }
      return "";
    case "offline":
      return `Nothing accepted a connection at ${b.base_url}. Start the server on that host, or pin the URL under Manual overrides and switch it off so the router stops probing it.`;
    case "timeout":
      return "The connection was accepted but no model list came back before the probe timeout — usually a server still loading a model, or a saturated link.";
    case "error":
      return http
        ? `The endpoint answered HTTP ${http} instead of a model list, so the router cannot tell what it serves.`
        : "The probe failed before any response came back.";
    default:
      return "No probe result yet. Until one arrives the router treats this endpoint as unusable.";
  }
}

/**
 * Best available latency. `health.latency_p50_ms` is only filled by a full
 * discovery scan; `latency_ema_ms` is the router's own moving average and is
 * 0 until real traffic flows. Which one is showing is labelled, so the number
 * is never mistaken for a live p50.
 */
function backendLatency(b) {
  const p50 = Number(b.health?.latency_p50_ms);
  if (Number.isFinite(p50) && p50 > 0) return { label: "p50", ms: p50 };
  const ema = Number(b.latency_ema_ms);
  if (Number.isFinite(ema) && ema > 0) return { label: "latency ema", ms: ema };
  return null;
}

/**
 * When this backend was last probed, in browser-comparable milliseconds.
 *
 * `health.last_check` is a `time.monotonic()` value from the agent process
 * and is meaningless here — subtracting it from `Date.now()` produces an age
 * of several decades. `health.last_check_epoch_s` is the wall-clock sibling
 * stamped by the same probe (UI-3); `0.0` is the documented "never probed"
 * sentinel, matching `last_check`'s own convention.
 */
function backendProbedAtMs(b) {
  const epoch = Number(b.health?.last_check_epoch_s);
  if (!Number.isFinite(epoch) || epoch <= 0) return null;
  return epoch * 1000;
}

/** "probed 8s ago" / "never probed", for a row subtitle or cell. */
function backendProbeAgeText(b) {
  const at = backendProbedAtMs(b);
  return at ? `probed ${timeAgo(at)}` : "never probed";
}

// health.models is whatever the probed backend reported; a comma-joined string
// instead of a list is enough to break every caller below, so normalise once.
function backendModels(b) {
  return asArray(b.health && b.health.models);
}

function backendModelCount(b) {
  const declared = Number(b.health?.model_count);
  return Number.isFinite(declared) && declared > 0 ? declared : backendModels(b).length;
}

function backendProviderLabel(b) {
  if (b.cloud_provider) return b.cloud_provider;
  return PROVIDER_LABELS[b.provider] || b.provider || "custom";
}

/** Display name for a remote row: the peer's agent id, else the URL host. */
function backendNodeName(b) {
  if (b.cloud_provider) return b.cloud_provider;
  if (typeof b.id === "string" && b.id.startsWith("peer:") && b.agent_id) {
    return b.agent_id;
  }
  try {
    return new URL(b.base_url).hostname || b.base_url;
  } catch {
    return b.base_url || b.id || "backend";
  }
}

/** local / remote-LAN / cloud. Cloud rows carry local=false too, so they
 *  would otherwise be filed under "reached through peers", which they are not. */
function backendGroup(b) {
  if (b.cloud_provider) return "cloud";
  return b.local === false ? "remote" : "local";
}

function backendScopeAllows(group) {
  if (backendsScope === "all") return true;
  if (backendsScope === "local") return group === "local";
  return group !== "local";
}

/* ---------------- small building blocks ---------------- */

/** Model ids as tags, truncated with a "+N" the way the mockup shows. */
function backendModelChips(models, limit) {
  const wrap = el("div", "chip-list");
  models.slice(0, limit).forEach((id) => {
    // .tag, not .chip: .chip is the interactive filter control (a <button>);
    // these are labels, and .tag truncates a long id instead of stretching
    // the card.
    const tag = textEl("span", "tag", id);
    tag.title = id;
    wrap.appendChild(tag);
  });
  if (models.length > limit) {
    wrap.appendChild(textEl("span", "tag muted", `+${models.length - limit}`));
  }
  return wrap;
}

/**
 * Whole-machine memory from GET /netllm/v1/telemetry `host` (psutil). This is
 * the only real capacity signal the agent exposes; it is machine-level RAM,
 * not the per-backend VRAM the mockup draws, and is labelled as such.
 */
function backendHostMemoryNode() {
  const host = state.telemetry?.host;
  const total = Number(host?.memory_total_gb);
  const used = Number(host?.memory_used_gb);
  if (!Number.isFinite(total) || total <= 0 || !Number.isFinite(used)) return null;
  const percent = Number.isFinite(Number(host.memory_percent))
    ? Number(host.memory_percent)
    : (used / total) * 100;

  const box = el("div", "inset");
  const head = el("div", "row-between");
  head.appendChild(textEl("div", "field-label", "Host memory"));
  head.appendChild(
    textEl("div", "mono muted", `${used.toFixed(1)} / ${total.toFixed(1)} GB`)
  );
  box.appendChild(head);

  const meter = el("div", "meter");
  const fill = el("span", percent >= 85 ? "warn" : "");
  fill.style.width = `${Math.max(0, Math.min(100, percent)).toFixed(1)}%`;
  meter.appendChild(fill);
  box.appendChild(meter);
  box.appendChild(
    textEl(
      "div",
      "field-help",
      "Whole-machine RAM on the agent host. The agent reports no per-backend VRAM or GPU utilisation."
    )
  );
  return box;
}

/* ---------------- local cards ---------------- */

function localBackendCard(b) {
  const view = backendHealthView(b);
  const modifier = backendPanelModifier(view.kind);
  const card = el("div", modifier ? `panel ${modifier}` : "panel");

  const head = el("div", "row");
  head.appendChild(statusDot(backendDotKind(view.kind)));
  head.appendChild(textEl("strong", "", backendProviderLabel(b)));
  head.appendChild(el("div", "spacer"));
  head.appendChild(pill(view.kind, view.label));
  card.appendChild(head);
  card.appendChild(textEl("div", "mono muted", b.base_url || "—"));
  // The freshness of everything below it. Until last_check_epoch_s existed
  // this card could describe a backend's state without ever saying whether
  // that state was seconds or hours old.
  card.appendChild(textEl("div", "field-help", backendProbeAgeText(b)));

  const stats = el("div", "stat-row");
  stats.appendChild(statBlock("Models", String(backendModelCount(b))));
  stats.appendChild(statBlock("In flight", String(b.in_flight || 0)));
  const latency = backendLatency(b);
  stats.appendChild(
    latency
      ? statBlock(latency.label, String(Math.round(latency.ms)), "ms")
      : statBlock("Latency", "—")
  );
  // 0 means "defer to routing.max_in_flight_per_backend" (Backend.max_concurrency).
  stats.appendChild(
    statBlock("Max in flight", b.max_concurrency > 0 ? String(b.max_concurrency) : "—")
  );
  card.appendChild(stats);

  const models = backendModels(b);
  if (models.length) card.appendChild(backendModelChips(models, 3));

  const why = probeExplanation(b);
  if (why) {
    const box = el("div", "inset");
    box.appendChild(textEl("div", "field-help", why));
    if (b.health?.detail) {
      box.appendChild(textEl("div", "mono field-help", `Probe said: ${b.health.detail}`));
    }
    card.appendChild(box);
    if (b.enabled !== false) {
      const actions = el("div", "row");
      actions.appendChild(
        button("Probe again", "secondary small", () => refresh())
      );
      actions.appendChild(
        button("Manual overrides", "secondary small", () => {
          const target = document.getElementById("section-backend-overrides");
          if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
        })
      );
      const ignore = backendIgnoreButton(b);
      if (ignore) actions.appendChild(ignore);
      card.appendChild(actions);
    }
  }
  return card;
}

/**
 * "Ignore" — add this endpoint to discovery.ignored_urls.
 *
 * The action this page was missing. A service that is not a netllm backend
 * sitting on a provider's default port (something on :8000 answering 401, say)
 * is discovered on every scan and shows up here as "needs a key" forever, and
 * until this existed the only way to silence it was to pin it as a
 * [[routing.backends]] override and switch that off — which turns a
 * *discovered* endpoint into a hand-authored config row and is named as a
 * design error in docs/ui-redesign-feature-spec.md §5.
 *
 * Returns null when the button would be a lie:
 *  - a URL already pinned in routing.backends, where an ignore entry is inert
 *    because the explicit override wins (netllm_core.backend_credentials);
 *  - a cloud row, which comes from [cloud.providers] and never from the scan.
 * Removal lives on the Network page's "Ignored endpoints" section, which is
 * where the whole list is managed.
 */
function backendIgnoreButton(b) {
  const url = b.base_url || "";
  if (!url || b.cloud_provider) return null;
  if (typeof ignoreDiscoveryUrl !== "function") return null;
  if (ignoreOverruledByBackend(url)) return null;
  if (isDiscoveryUrlIgnored(url)) {
    // Ignored in the draft but still in /status: the scan has not re-run yet.
    return button("Ignored — undo", "secondary small", () => {
      unignoreDiscoveryUrl(url);
      render();
    });
  }
  const btn = button("Ignore", "secondary small", () => {
    if (!ignoreDiscoveryUrl(url)) return;
    showToast("Ignored — press Save, then Rescan now");
    render();
  });
  btn.title =
    "Never register this endpoint again. Save, then Rescan. Undo it under Network → Ignored endpoints.";
  return btn;
}

/* ---------------- remote / cloud table ---------------- */

const BACKEND_REMOTE_COLUMNS = ["Node", "Base URL", "Provider", "Models", "Latency", "In flight", "Last probe", "State"];
const BACKEND_REMOTE_TEMPLATE = "1.1fr 1.5fr .7fr .6fr .8fr .6fr .8fr .9fr";

function remoteBackendTable(rows) {
  const t = dataTable(BACKEND_REMOTE_COLUMNS, BACKEND_REMOTE_TEMPLATE);
  rows.forEach((b) => {
    const view = backendHealthView(b);
    const node = el("div", "row");
    node.appendChild(statusDot(backendDotKind(view.kind)));
    node.appendChild(textEl("span", "", backendNodeName(b)));
    const latency = backendLatency(b);
    const stateCell = textEl(
      "div",
      view.kind === "ok"
        ? "text-ok"
        : view.kind === "error"
          ? "text-danger"
          : view.kind === "warn"
            ? "text-warn"
            : "muted",
      view.label
    );
    t.addRow([
      node,
      textEl("div", "mono muted", b.base_url || "—"),
      textEl("div", "mono muted", backendProviderLabel(b)),
      String(backendModelCount(b)),
      textEl("div", "mono muted", latency ? `${Math.round(latency.ms)}ms` : "—"),
      String(b.in_flight || 0),
      textEl(
        "div",
        "mono muted",
        backendProbedAtMs(b) ? timeAgo(backendProbedAtMs(b)) : "never"
      ),
      stateCell,
    ]);

    // A failed remote probe gets its own full-width row underneath rather
    // than being crushed into the State column — the explanation is the
    // reason this page exists.
    const why = probeExplanation(b);
    if (!why) return;
    const note = el("div", "table-row");
    note.style.gridTemplateColumns = "1fr";
    note.appendChild(textEl("div", "field-help", why));
    if (b.health?.detail) {
      note.appendChild(
        textEl("div", "mono field-help", `Probe said: ${b.health.detail}`)
      );
    }
    t.table.appendChild(note);
  });
  return t.table;
}

/* ---------------- manual overrides (routing.backends) ---------------- */

/**
 * The pinned-URL editor from the mockup. This is `routing.backends`
 * (BackendOverride rows: base_url, provider, enabled, api_key, cloud_provider,
 * max_concurrency), rendered by the generic schema form so every field in the
 * row model keeps a control — the Axis D control-parity kit fails by name if
 * one goes missing.
 */
function renderBackendOverrides(root) {
  const pinned = asArray(state.configDraft?.routing?.backends);
  const body = panel(
    root,
    "Manual overrides",
    pinned.length ? `${pinned.length} pinned` : "routing.backends"
  );
  body.appendChild(
    textEl(
      "p",
      "panel-desc",
      "Endpoints the router always includes, whatever discovery finds — and the per-URL API key, concurrency cap and enable switch for any endpoint above."
    )
  );
  // A malformed /config/schema (fields as a string, or holding nulls) is the
  // single payload that breaks the most pages; normalise before .find().
  const field = asArray(state.configSchema?.sections?.routing?.fields)
    .filter(Boolean)
    .find((f) => f.name === "backends");
  if (!field || typeof renderSchemaField !== "function") {
    body.appendChild(
      textEl(
        "p",
        "empty",
        "Config schema unavailable — the admin API is unreachable, so pinned backends cannot be edited from here."
      )
    );
    return;
  }
  try {
    // Node form of renderSchemaField: overrides are this field's own
    // {label, help}, not a name-keyed map (that shape is renderSchemaForm's).
    renderSchemaField(body, "routing", field, {
      label: "Pinned backend URLs",
      help: "One row per URL. A row whose base_url matches a discovered endpoint overrides that endpoint instead of adding a second one.",
    });
  } catch (e) {
    body.appendChild(textEl("p", "empty", `Could not render the editor: ${e.message}`));
  }
}

/* ---------------- page ---------------- */

function backendsSubtitle(backends) {
  const parts = [];
  const enabled = backends.filter((b) => b.enabled !== false);
  const online = enabled.filter((b) => b.health?.status === "online").length;
  const failing = enabled.length - online;
  const disabled = backends.length - enabled.length;
  parts.push(`${online} online`);
  if (failing) parts.push(`${failing} not answering`);
  if (disabled) parts.push(`${disabled} disabled`);
  // When the pool last actually looked, not when this tab last polled. The
  // freshest probe across the pool is the honest summary: rows re-probe
  // independently, on health_ttl_s or offline_retry_s.
  const newest = backends
    .map(backendProbedAtMs)
    .filter((ms) => ms)
    .reduce((a, b) => Math.max(a, b), 0);
  parts.push(newest ? `newest probe ${timeAgo(newest)}` : "no probe yet");
  // status.discovery.last_scan_at is stamped by the provider scan itself, so
  // this ages the "Rescan now" button's effect whether the last scan came
  // from that button, the TTL refresh or the rediscovery loop.
  const scannedAt = Number(state.status?.discovery?.last_scan_at);
  if (Number.isFinite(scannedAt) && scannedAt > 0) {
    parts.push(`discovery scanned ${timeAgo(scannedAt * 1000)}`);
  }
  const routing = asObject(state.config?.routing || state.configDraft?.routing);
  if (Number.isFinite(Number(routing.health_ttl_s))) {
    parts.push(`re-probed after ${routing.health_ttl_s}s`);
  }
  if (Number.isFinite(Number(routing.offline_retry_s))) {
    parts.push(`offline retry ${routing.offline_retry_s}s`);
  }
  if (state.lastUpdatedAt) parts.push(`status ${timeAgo(state.lastUpdatedAt)}`);
  return parts.join(" · ");
}

function backendsScopeSwitch() {
  const seg = el("div", "segmented");
  seg.setAttribute("role", "group");
  seg.setAttribute("aria-label", "Filter backends by location");
  BACKEND_SCOPES.forEach((scope) => {
    const b = button(scope.label, "", () => {
      backendsScope = scope.id;
      render();
    });
    b.setAttribute("aria-pressed", String(backendsScope === scope.id));
    seg.appendChild(b);
  });
  return seg;
}

function renderBackendsPage(root) {
  // status.backends is wire data: it can arrive wrong-typed or with null rows,
  // and every derivation below reads fields off each row.
  const backends = asArray(state.status?.backends).filter(Boolean);

  const aside = el("div", "row");
  aside.appendChild(backendsScopeSwitch());
  // Parity with the old tab's "Refresh scan" button.
  aside.appendChild(button("Rescan now", "secondary", () => runDiscover()));
  pageHeader(
    root,
    "Backends",
    backends.length ? backendsSubtitle(backends) : "No backends known yet",
    aside
  );

  const stack = el("div", "stack");
  root.appendChild(stack);

  if (!backends.length) {
    const body = panel(stack, "Nothing routable yet");
    body.appendChild(
      textEl(
        "p",
        "empty",
        "No backends yet — start oMLX, Ollama, LM Studio or vLLM on this machine and the agent will find it. Rescan now re-runs discovery; a URL pinned below is always included."
      )
    );
  }

  const local = backends.filter((b) => backendGroup(b) === "local");
  const remote = backends.filter((b) => backendGroup(b) === "remote");
  const cloud = backends.filter((b) => backendGroup(b) === "cloud");

  if (local.length && backendScopeAllows("local")) {
    const hostname = state.status?.hostname;
    stack.appendChild(
      textEl("div", "field-label", hostname ? `On this machine · ${hostname}` : "On this machine")
    );
    const memory = backendHostMemoryNode();
    if (memory) stack.appendChild(memory);
    // .grid-cards, not .grid-2: a card carries a four-block stat row, which
    // wraps badly inside .grid-2's 260px minimum. The grid's `gap` — never a
    // margin on the card — is what separates them, and `align-items: start`
    // keeps a short card at its natural height instead of stretching it to
    // match a taller neighbour.
    const grid = el("div", "grid-cards");
    local.forEach((b) => grid.appendChild(localBackendCard(b)));
    stack.appendChild(grid);
  }

  if (remote.length && backendScopeAllows("remote")) {
    const routing = asObject(state.config?.routing || state.configDraft?.routing);
    const head = el("div", "row-between");
    head.appendChild(textEl("div", "field-label", "Reached through peers"));
    head.appendChild(
      textEl(
        "div",
        "field-help",
        routing.allow_remote === false
          ? "routing.allow_remote is off — these are listed but never routed to."
          : "routable because routing.allow_remote is on"
      )
    );
    stack.appendChild(head);
    stack.appendChild(remoteBackendTable(remote));
  }

  if (cloud.length && backendScopeAllows("cloud")) {
    stack.appendChild(textEl("div", "field-label", "Cloud endpoints"));
    stack.appendChild(remoteBackendTable(cloud));
    stack.appendChild(
      textEl(
        "div",
        "field-help",
        "Materialized from cloud.providers — keys and model catalogs live on the Cloud failover page."
      )
    );
  }

  if (backends.length && !local.length && !remote.length && !cloud.length) {
    stack.appendChild(textEl("p", "empty", "No backends match this filter."));
  }

  const overrides = el("div");
  overrides.id = "section-backend-overrides";
  stack.appendChild(overrides);
  renderBackendOverrides(overrides);
}

registerPage("backends", renderBackendsPage);
