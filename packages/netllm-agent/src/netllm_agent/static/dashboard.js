/* llm-swarm-router web dashboard — native Settings parity */

const PROVIDERS = ["omlx", "ollama", "lmstudio", "vllm"];
// STRATEGIES was hand-maintained here; routing.default_strategy's options
// now come from the schema's Literal introspection (phase 3) instead.
const ROLES = ["peer", "gateway"];
// Fallback only, used when the admin config API is unreachable (offline
// dashboard) and configDraft.cloud.providers is empty. Once connected,
// the provider list comes from the agent's config summary (server is the
// single source of truth for the provider set + all display metadata —
// see admin.cloud_provider_registry_payload / GET /netllm/v1/cloud/providers).
const CLOUD_PROVIDER_IDS_BOOTSTRAP = [
  "moonshot",
  "zai",
  "openai",
  "anthropic",
  "openrouter",
];

const state = {
  tab: "status",
  dirty: false,
  config: null,
  configDraft: null,
  // Form shape for the editable config sections — see
  // config_schema.py / GET /netllm/v1/config/schema. null until fetched
  // (or against an older agent that predates the endpoint); sections not
  // yet migrated to renderSchemaForm don't need it at all.
  configSchema: null,
  status: null,
  versionInfo: null,
  updateInfo: null,
  models: [],
  doctor: null,
  envText: "",
  lanPeers: [],
  healthy: false,
  pollTimer: null,
  updatePollTimer: null,
  logsPollTimer: null,
  logs: null,
  adminWarned: false,
};

function showToast(msg) {
  const toast = document.getElementById("toast");
  toast.textContent = msg;
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 2400);
}

function setBanner(text, kind = "info") {
  const el = document.getElementById("global-banner");
  if (!text) {
    el.hidden = true;
    el.textContent = "";
    el.className = "banner";
    return;
  }
  el.hidden = false;
  el.className = `banner ${kind}`;
  el.textContent = text;
}

function markDirty(dirty = true) {
  state.dirty = dirty;
  document.getElementById("btn-save").disabled = !dirty;
}

function cloneConfig(cfg) {
  return JSON.parse(JSON.stringify(cfg));
}

async function api(path, options = {}) {
  const res = await fetch(path, options);
  if (!res.ok) {
    const text = await res.text();
    let message = text || `${res.status} ${res.statusText}`;
    try {
      const json = JSON.parse(text);
      if (json && typeof json.detail === "string") {
        message = json.detail;
      }
    } catch {
      /* keep raw text */
    }
    throw new Error(message);
  }
  if (res.status === 204) return null;
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return res.json();
  return res.text();
}

async function loadHealth() {
  try {
    await api("/health");
    state.healthy = true;
  } catch {
    state.healthy = false;
  }
}

function omlxAdminURLFromStatus(status) {
  if (status?.omlx_admin_url) return status.omlx_admin_url;
  const omlx = (status?.backends || []).find(
    (b) =>
      b.provider === "omlx" &&
      b.enabled !== false &&
      b.health?.status !== "offline"
  );
  if (!omlx?.base_url) return "http://127.0.0.1:8080/admin";
  let base = omlx.base_url.replace(/\/$/, "");
  if (base.endsWith("/v1")) base = base.slice(0, -3);
  return `${base}/admin`;
}

function updateOmlxAdminLink() {
  const btn = document.getElementById("btn-omlx-admin");
  if (!btn) return;
  btn.href = omlxAdminURLFromStatus(state.status);
}

async function loadCore() {
  const status = await api("/netllm/v1/status");
  state.status = status;
  updateOmlxAdminLink();
  const [models, env] = await Promise.all([
    api("/v1/models"),
    api("/netllm/v1/client-env"),
  ]);
  state.models = models.data || [];
  const vars = env.vars || env;
  state.envText = Object.entries(vars)
    .map(([k, v]) => `export ${k}=${v}`)
    .join("\n");
  try {
    state.doctor = await api("/netllm/v1/doctor");
  } catch (e) {
    state.doctor = { ok: false, issues: [], error: e.message };
    warnAdminLimited(
      "Doctor unavailable (admin API). Use http://127.0.0.1:11400/ui/ on this Mac or set a cluster token."
    );
  }
  try {
    const config = await api("/netllm/v1/config");
    if (!state.dirty) {
      state.config = config;
      state.configDraft = cloneConfig(config);
    }
  } catch (e) {
    warnAdminLimited(
      "Config editor unavailable (admin API). Use http://127.0.0.1:11400/ui/ on this Mac or set a cluster token."
    );
    if (!state.configDraft) {
      state.configDraft = emptyConfigDraft();
    }
  }
  updateStatusLine();
}

function warnAdminLimited(message) {
  if (state.adminWarned) return;
  state.adminWarned = true;
  setBanner(message, "warn");
}

// Section key -> default draft object, built by walking a fetched
// GET /netllm/v1/config/schema section's fields. Falls back to a fixed
// literal when the schema hasn't loaded yet (or predates this endpoint)
// — see docs/config-schema-rewrite-plan.md §5 phase 2 / §4 bootstrap.
function schemaSectionDefaults(schema, sectionKey, fallback) {
  const section = schema?.sections?.[sectionKey];
  if (!section) return fallback;
  const out = {};
  section.fields.forEach((f) => {
    out[f.name] = f.default;
  });
  return out;
}

function emptyConfigDraft() {
  // Every section's default now walks the fetched schema (phase 3); the
  // literal second argument is the fallback for a schema-unavailable
  // agent (predates the endpoint) or a fetch failure — see plan §4.
  return {
    agent: schemaSectionDefaults(state.configSchema, "agent", {
      listen: "127.0.0.1:11400",
      role: "peer",
      advertise: true,
      agent_id: "",
      hostname: "",
    }),
    discovery: schemaSectionDefaults(state.configSchema, "discovery", {
      providers: [...PROVIDERS],
      provider_urls: {},
      custom_endpoints: [],
    }),
    swarm: schemaSectionDefaults(state.configSchema, "swarm", {
      mdns: true,
      subnet_scan: false,
      subnet_cidrs: [],
      heartbeat_interval_s: 10,
      peers: [],
    }),
    routing: schemaSectionDefaults(state.configSchema, "routing", {
      default_strategy: "local_first",
      allow_remote: true,
      require_same_model_for_shard: true,
      backends: [],
      policies: [],
    }),
    ui: schemaSectionDefaults(state.configSchema, "ui", {
      auto_start_on_launch: true,
      log_dir: "",
      check_for_updates_automatically: true,
    }),
    cloud: schemaSectionDefaults(state.configSchema, "cloud", {
      enabled: true,
      fallback: "cloud",
      fallback_enabled: true,
      providers: {},
    }),
  };
}

function checkForUpdatesAutomatically() {
  const ui = state.config?.ui || state.configDraft?.ui;
  if (ui && ui.check_for_updates_automatically === false) return false;
  return true;
}

async function loadConfigSchema() {
  // Fetched once per refresh; a stale/missing schema just means the
  // migrated section(s) fall back to their "schema unavailable" message
  // in renderSchemaForm — every other tab is unaffected.
  try {
    state.configSchema = await api("/netllm/v1/config/schema");
  } catch {
    state.configSchema = null;
  }
}

async function loadVersionInfo() {
  try {
    state.versionInfo = await api("/netllm/v1/version");
  } catch {
    state.versionInfo = null;
  }
}

async function loadUpdateCheck(force = false) {
  try {
    const q = force ? "?force=1" : "";
    state.updateInfo = await api(`/netllm/v1/update/check${q}`);
  } catch (e) {
    state.updateInfo = { error: e.message, update_available: false };
  }
}

function updateStatusBadge() {
  const badge = document.getElementById("status-badge");
  if (!badge) return;
  badge.className = `badge ${state.healthy ? "ok" : "warn"}`;
  badge.replaceChildren();
  badge.appendChild(el("span", "dot"));
  badge.appendChild(
    document.createTextNode(state.healthy ? "Running" : "Unreachable")
  );
}

function updateStatusLine() {
  updateStatusBadge();
  const el = document.getElementById("agent-status-line");
  if (!state.healthy) {
    el.textContent = "Cannot reach /health — restart the agent";
    return;
  }
  const s = state.status;
  if (!s) {
    el.textContent = "Loading agent status…";
    return;
  }
  let line = s.listen_url || "";
  const peers = (s.peers || []).length;
  const online = onlineBackends().length;
  const parts = [`${online} backend${online === 1 ? "" : "s"} online`];
  if (peers > 0) parts.push(`${peers} peer${peers === 1 ? "" : "s"}`);
  if (line) parts.unshift(line);
  el.textContent = parts.join(" · ");
}

function onlineBackends() {
  return (state.status?.backends || []).filter(
    (b) => b.health && b.health.status === "online"
  );
}

function activeSummary() {
  const s = state.status;
  if (!s) return "Start agent to load status.";
  const online = onlineBackends().length;
  const parts = [
    `${online} backend${online === 1 ? "" : "s"} online`,
    `role: ${s.role || "peer"}`,
    s.routing_strategy || "local_first",
  ];
  const peers = (s.peers || []).length;
  if (peers > 0) parts.unshift(`${peers} peer${peers === 1 ? "" : "s"} connected`);
  return parts.join(" · ");
}

function el(tag, cls) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  return node;
}

function textEl(tag, cls, content) {
  const node = el(tag, cls);
  node.textContent = content;
  return node;
}

function codeEl(content) {
  const node = el("code");
  node.textContent = content;
  return node;
}

function brandLogoEl(size) {
  const pic = el("picture", "brand-logo");
  const source = document.createElement("source");
  source.srcset = "logo-dark.png";
  source.media = "(prefers-color-scheme: dark)";
  const img = document.createElement("img");
  img.src = "logo-light.png";
  img.alt = "";
  img.width = size;
  img.height = size;
  pic.append(source, img);
  return pic;
}

function appendInfoRow(parent, label, value) {
  const row = el("div", "info-row");
  row.append(textEl("span", "", label), textEl("span", "", value || "—"));
  parent.appendChild(row);
}

function renderStatusTab() {
  const root = document.getElementById("tab-status");
  root.replaceChildren();
  root.appendChild(textEl("h1", "page-title", "Status"));

  const hero = el("div", "card hero-card");
  const logo = brandLogoEl(52);
  const meta = el("div", "hero-meta");
  meta.style.flex = "1";
  meta.appendChild(textEl("h2", "", "llm-swarm-router"));
  const versionLine = state.versionInfo?.version
    ? `v${state.versionInfo.version}`
    : "Version unknown";
  meta.appendChild(textEl("p", "muted-sm", versionLine));
  meta.appendChild(textEl("p", "", state.status?.listen_url || "—"));
  const sub1 = el("p");
  sub1.append("Mesh router for local LLM backends · CLI: ", codeEl("netllm"));
  meta.appendChild(sub1);
  const badge = el("span", `badge ${state.healthy ? "ok" : "warn"}`);
  badge.appendChild(el("span", "dot"));
  badge.appendChild(document.createTextNode(state.healthy ? "Running" : "Unreachable"));
  hero.append(logo, meta, badge);
  root.appendChild(hero);

  const online = onlineBackends().length;
  const total = (state.status?.backends || []).length;
  const peerCount = (state.status?.peers || []).length;
  root.appendChild(textEl("div", "section-label", "Routing stats"));
  const metrics = el("div", "metrics");
  [
    ["Backends", `${online}/${total}`, online > 0 ? "Online backends" : "No backends online"],
    ["Peers", String(peerCount), "Connected swarm peers"],
    ["Models", String(state.models.length), "Routed model catalog"],
  ].forEach(([label, value, sub]) => {
    const m = el("div", "metric-card");
    m.append(textEl("div", "label", label), textEl("div", "value", value), textEl("div", "sub", sub));
    metrics.appendChild(m);
  });
  root.appendChild(metrics);

  root.appendChild(textEl("div", "section-label", "Active now"));
  root.appendChild(Object.assign(el("div", "card"), { textContent: activeSummary() }));

  root.appendChild(textEl("div", "section-label", "System"));
  const sys = el("div", "card");
  appendInfoRow(sys, "Version", state.versionInfo?.version ? `v${state.versionInfo.version}` : "—");
  appendInfoRow(
    sys,
    "OpenAI SDK",
    state.versionInfo?.sdk_versions?.openai
      ? `v${state.versionInfo.sdk_versions.openai}`
      : "—",
  );
  appendInfoRow(
    sys,
    "Anthropic SDK",
    state.versionInfo?.sdk_versions?.anthropic
      ? `v${state.versionInfo.sdk_versions.anthropic}`
      : "—",
  );
  appendInfoRow(sys, "Install method", state.versionInfo?.install_method || "—");
  appendInfoRow(sys, "Platform", state.versionInfo?.platform || "—");
  appendInfoRow(sys, "Agent ID", state.status?.agent_id);
  appendInfoRow(sys, "Hostname", state.status?.hostname);
  appendInfoRow(sys, "Role", state.status?.role);
  appendInfoRow(sys, "Strategy", state.status?.routing_strategy);
  appendInfoRow(sys, "Listen", state.status?.listen_url);
  root.appendChild(sys);

  root.appendChild(textEl("div", "section-label", "Updates"));
  root.appendChild(renderUpdateCard());

  root.appendChild(textEl("div", "section-label", "Quick actions"));
  const actions = el("div", "topbar-actions");
  const d1 = textEl("button", "", "Discover providers");
  d1.onclick = runDiscover;
  const d2 = textEl("button", "secondary", "Scan LAN peers");
  d2.onclick = () => runPeersScan(false);
  const d3 = textEl("button", "secondary", "Run doctor");
  d3.onclick = () => refresh().catch((e) => showToast(e.message));
  actions.append(d1, d2, d3);
  root.appendChild(actions);

  root.appendChild(textEl("div", "section-label", "Restart agent"));
  const hint = el("div", "card");
  hint.appendChild(textEl("p", "empty", "After changing listen address or port, restart the agent: netllm restart (packaged install) or menubar Settings → Restart Agent."));
  root.appendChild(hint);
}

function renderUpdateCard() {
  const card = el("div", "card update-card");
  const info = state.updateInfo;
  const actions = el("div", "topbar-actions");

  const checkBtn = textEl("button", "secondary", "Check for updates");
  checkBtn.onclick = () => {
    loadUpdateCheck(true)
      .then(() => {
        renderStatusTab();
        showToast("Update check complete");
      })
      .catch((e) => showToast(e.message));
  };
  actions.appendChild(checkBtn);

  if (!info) {
    card.appendChild(textEl("p", "empty", "Loading update status…"));
    card.appendChild(actions);
    return card;
  }

  if (info.error && !info.update_available) {
    card.appendChild(textEl("p", "empty", info.error));
    card.appendChild(actions);
    return card;
  }

  if (info.update_available) {
    card.appendChild(
      textEl(
        "p",
        "",
        `Update available: v${info.latest} (you have v${info.current})`
      )
    );
    if (info.upgrade_hint) {
      const hintRow = el("div", "cmd");
      hintRow.appendChild(codeEl(info.upgrade_hint));
      const copy = textEl("button", "secondary", "Copy");
      copy.onclick = () =>
        navigator.clipboard
          .writeText(info.upgrade_hint)
          .then(() => showToast("Copied upgrade command"));
      hintRow.appendChild(copy);
      card.appendChild(hintRow);
    } else if (info.download_url) {
      const dl = textEl("button", "", "Download update");
      dl.onclick = () => window.open(info.download_url, "_blank", "noopener");
      actions.prepend(dl);
    }
  } else {
    card.appendChild(
      textEl("p", "empty", `You're on the latest version (v${info.current}).`)
    );
  }

  if (info.release_notes_url) {
    const notes = el("a", "btn secondary");
    notes.href = info.release_notes_url;
    notes.target = "_blank";
    notes.rel = "noopener";
    notes.textContent = "Release notes";
    actions.appendChild(notes);
  }

  card.appendChild(actions);
  return card;
}

function renderBackendRow(b) {
  const row = el("div", "backend-row");
  const health = (b.health && b.health.status) || "unknown";
  row.appendChild(el("span", `status-dot ${health === "online" ? "online" : "offline"}`));
  const body = el("div");
  body.appendChild(textEl("strong", "", b.provider || "custom"));
  body.appendChild(textEl("div", "muted-sm", b.base_url || ""));
  const models = (b.health && b.health.models) || [];
  body.appendChild(textEl("div", "muted-sm", `${health} · ${models.length} model(s)`));
  row.appendChild(body);
  return row;
}

function renderBackendsTab() {
  const root = document.getElementById("tab-backends");
  root.replaceChildren();
  root.appendChild(textEl("h1", "page-title", "Backends"));
  root.appendChild(textEl("div", "section-label", "Routed backends (from agent)"));
  const card = el("div", "card");
  const backends = state.status?.backends || [];
  if (!backends.length) {
    card.appendChild(textEl("p", "empty", "No backends yet — start oMLX or Ollama on this Mac."));
  } else {
    backends.forEach((b) => card.appendChild(renderBackendRow(b)));
  }
  root.appendChild(card);
  root.appendChild(textEl("div", "section-label", "Local providers"));
  const scan = textEl("button", "", "Refresh scan");
  scan.onclick = runDiscover;
  root.appendChild(scan);
}

function renderModelsTab() {
  const root = document.getElementById("tab-models");
  root.replaceChildren();
  root.appendChild(textEl("h1", "page-title", "Models"));
  root.appendChild(textEl("div", "section-label", "Routed models (agent)"));
  const card = el("div", "card");
  if (!state.models.length) {
    card.appendChild(textEl("p", "empty", "No routed models — start agent and backends."));
  } else {
    const table = el("table");
    const thead = el("thead");
    const hr = el("tr");
    hr.append(textEl("th", "", "Model"), textEl("th", "", "Owner"));
    thead.appendChild(hr);
    table.appendChild(thead);
    const tbody = el("tbody");
    state.models.forEach((m) => {
      const tr = el("tr");
      const td1 = el("td");
      td1.appendChild(codeEl(m.id || ""));
      tr.append(td1, textEl("td", "", m.owned_by || "—"));
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    card.appendChild(table);
  }
  root.appendChild(card);
  root.appendChild(textEl("div", "section-label", "LAN models"));
  root.appendChild(textEl("p", "empty", "Full LAN model merge: netllm models --lan in terminal."));
}

function renderPeerRow(p) {
  const row = el("div", "peer-row");
  row.appendChild(el("span", "status-dot online"));
  const body = el("div");
  const name = p.hostname || p.agent_id || "peer";
  body.appendChild(textEl("strong", "", p.self ? `${name} (this machine)` : name));
  body.appendChild(textEl("div", "muted-sm", p.listen_url || ""));
  const meta = p.agent_id ? `role: ${p.role || "peer"} — ${p.agent_id}` : `role: ${p.role || "peer"}`;
  body.appendChild(textEl("div", "muted-sm", meta));
  if (Array.isArray(p.also_reachable_at) && p.also_reachable_at.length) {
    body.appendChild(textEl("div", "muted-sm", `also reachable at: ${p.also_reachable_at.join(", ")}`));
  }
  row.appendChild(body);
  return row;
}

function renderPeersTab() {
  const root = document.getElementById("tab-peers");
  root.replaceChildren();
  root.appendChild(textEl("h1", "page-title", "Peers"));
  root.appendChild(textEl("div", "section-label", "Connected swarm peers"));
  const connected = el("div", "card");
  const peers = state.status?.peers || [];
  if (!peers.length) {
    connected.appendChild(textEl("p", "empty", "No peers connected to running agent."));
  } else {
    peers.forEach((p) => connected.appendChild(renderPeerRow(p)));
  }
  root.appendChild(connected);
  root.appendChild(textEl("div", "section-label", "LAN discovery"));
  const actions = el("div", "topbar-actions");
  const s1 = textEl("button", "", "Scan network");
  s1.onclick = () => runPeersScan(false);
  const s2 = textEl("button", "secondary", "Scan & save to config");
  s2.onclick = () => runPeersScan(true);
  actions.append(s1, s2);
  root.appendChild(actions);
  root.appendChild(textEl("div", "section-label", "Scan results"));
  const scanCard = el("div", "card");
  if (!state.lanPeers.length) {
    scanCard.appendChild(textEl("p", "empty", "No scan yet — click Scan network."));
  } else {
    state.lanPeers.forEach((p) => scanCard.appendChild(renderPeerRow(p)));
  }
  root.appendChild(scanCard);
  root.appendChild(textEl("div", "section-label", "Static peers in config"));
  root.appendChild(renderStringListEditor("swarm.peers", "http://10.0.0.32:11400"));
}

function parseListen() {
  const listen = state.configDraft?.agent?.listen || "127.0.0.1:11400";
  const idx = listen.lastIndexOf(":");
  if (idx < 0) return { host: "127.0.0.1", port: 11400 };
  return { host: listen.slice(0, idx), port: parseInt(listen.slice(idx + 1), 10) || 11400 };
}

function setListen(host, port) {
  state.configDraft.agent.listen = `${host}:${port}`;
  markDirty();
}

function checkboxRow(label, checked, onChange) {
  const row = el("label", "checkbox-row");
  const cb = document.createElement("input");
  cb.type = "checkbox";
  cb.checked = checked;
  cb.onchange = () => onChange(cb.checked);
  row.append(cb, document.createTextNode(label));
  return row;
}

// --- Generic schema-driven form renderer (docs/config-schema-rewrite-plan.md) ---
//
// renderSchemaForm(sectionKey, schema, draft, onDirty, overrides) walks a
// fetched config schema section and renders one input per field,
// dispatching on the field's "widget" to these DOM helpers — the same
// low-level building blocks (checkboxRow, el/textEl) every hand-written
// render*Tab() already uses. `overrides` is the escape hatch the plan's
// §6 risk 1 calls for: per-field {label?, placeholder?, onChange?(value)}
// for the handful of fields that need bespoke copy or a side effect
// (e.g. ui.check_for_updates_automatically starting/stopping a poll
// timer) without forking the whole section back into hand-written code.

function schemaFieldLabel(field, overrides) {
  if (overrides?.label) return overrides.label;
  return field.name.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function schemaFieldRow(field, draft, onDirty, overrides, inputType) {
  const group = el("div", "form-group");
  group.appendChild(textEl("label", "", schemaFieldLabel(field, overrides)));
  const input = document.createElement("input");
  input.type = inputType;
  const current = draft[field.name];
  input.value = current === undefined || current === null ? "" : current;
  if (overrides?.placeholder) input.placeholder = overrides.placeholder;
  if (overrides?.step) input.step = overrides.step;
  input.oninput = () => {
    draft[field.name] = inputType === "number" ? Number(input.value) : input.value;
    onDirty();
    if (overrides?.onChange) overrides.onChange(draft[field.name]);
  };
  group.appendChild(input);
  return group;
}

function schemaSelectRow(field, draft, onDirty, overrides) {
  const group = el("div", "form-group");
  group.appendChild(textEl("label", "", schemaFieldLabel(field, overrides)));
  const select = document.createElement("select");
  // Optional Literal fields (e.g. RoutingPolicy.api_format/strategy) get a
  // leading "unset" option mapping to null, matching what the pydantic
  // model actually allows — a required select has no such option.
  const options = overrides?.options || field.options || [];
  const withBlank = field.optional ? ["", ...options] : options;
  withBlank.forEach((opt) => {
    const optionEl = document.createElement("option");
    optionEl.value = opt;
    optionEl.textContent = overrides?.optionLabels?.[opt] || opt || "(default)";
    select.appendChild(optionEl);
  });
  select.value = draft[field.name] ?? field.default ?? "";
  select.onchange = () => {
    draft[field.name] = select.value || (field.optional ? null : select.value);
    onDirty();
    if (overrides?.onChange) overrides.onChange(draft[field.name]);
  };
  group.appendChild(select);
  return group;
}

// Secret fields (write_only: true) never round-trip a real value from the
// server — draft[field.name] is always "". A typed value is stashed on
// `draft[pendingKey]` instead (default "_pending_<field>", overridable —
// swarm.cluster_token keeps its pre-existing "_cluster_token" key so
// buildConfigPatch's existing read of it needs no change) and left for
// the section's patch-builder to pick up, same convention the
// hand-written cloud/swarm code already used before this field existed.
function schemaSecretRow(field, draft, onDirty, overrides) {
  const group = el("div", "form-group");
  const pendingKey = overrides?.pendingKey || `_pending_${field.name}`;
  group.appendChild(textEl("label", "", schemaFieldLabel(field, overrides)));
  const input = document.createElement("input");
  input.type = "password";
  input.autocomplete = "off";
  input.placeholder = overrides?.placeholder || "Leave blank to keep existing";
  input.oninput = () => {
    draft[pendingKey] = input.value;
    onDirty();
  };
  group.appendChild(input);
  return group;
}

// Plain list-of-strings (routing.model_aliases values aside — those are
// dict_list_strings below): add/remove rows syncing straight back into
// draft[field.name], generalizing the pre-existing renderStringListEditor
// (which is keyed by a dotted path into the *global* configDraft) to work
// against any draft object.
function schemaListStringsRow(field, draft, onDirty, overrides) {
  const wrap = el("div", "card string-list");
  const list = el("div", "string-list");

  function sync() {
    const inputs = list.querySelectorAll("input");
    draft[field.name] = [...inputs].map((i) => i.value.trim()).filter(Boolean);
    onDirty();
  }

  function addRow(value = "") {
    const item = el("div", "string-list-item");
    const input = document.createElement("input");
    input.type = "text";
    input.value = value;
    input.placeholder = overrides?.placeholder || "";
    input.oninput = sync;
    const rm = textEl("button", "secondary", "−");
    rm.onclick = () => {
      item.remove();
      sync();
    };
    item.append(input, rm);
    list.appendChild(item);
  }

  (draft[field.name] || []).forEach(addRow);
  const add = textEl("button", "secondary", `+ Add ${overrides?.itemLabel || ""}`.trim());
  add.onclick = () => addRow();
  wrap.append(list, add);
  return wrap;
}

// dict[str, list[str]] (discovery.provider_urls: one string-list per
// known key). Unlike schemaDictRow below, keys here come from
// overrides.keys (a fixed, known set — e.g. the provider id list), not
// from user-typed row keys.
function schemaDictListStringsRow(field, draft, onDirty, overrides) {
  const wrap = el("div");
  if (!draft[field.name]) draft[field.name] = {};
  const dict = draft[field.name];
  (overrides?.keys || Object.keys(dict)).forEach((key) => {
    wrap.appendChild(textEl("div", "section-label", overrides?.keyLabels?.[key] || key));
    const rowWrap = el("div", "card string-list");
    const list = el("div", "string-list");
    function sync() {
      const inputs = list.querySelectorAll("input");
      dict[key] = [...inputs].map((i) => i.value.trim()).filter(Boolean);
      onDirty();
    }
    function addRow(value = "") {
      const item = el("div", "string-list-item");
      const input = document.createElement("input");
      input.type = "text";
      input.value = value;
      input.placeholder = overrides?.placeholder || "";
      input.oninput = sync;
      const rm = textEl("button", "secondary", "−");
      rm.onclick = () => {
        item.remove();
        sync();
      };
      item.append(input, rm);
      list.appendChild(item);
    }
    (dict[key] || []).forEach(addRow);
    const add = textEl("button", "secondary", "+ Add URL");
    add.onclick = () => addRow();
    rowWrap.append(list, add);
    wrap.appendChild(rowWrap);
  });
  return wrap;
}

// list[BaseModel] (routing.policies / routing.backends): one removable
// mini-form per item, fields driven by field.item_schema — the same
// dispatch schemaFieldsCard uses for a whole section, just scoped to one
// array element. `overrides.newItem()` supplies the "+ Add" default (the
// plan's §6 risk 1 default_factory hint resolves to this); falls back to
// field.item_schema's own per-field defaults.
function schemaListOfObjectsRow(field, draft, onDirty, overrides) {
  const wrap = el("div", "card");
  if (!draft[field.name]) draft[field.name] = [];
  const list = el("div", "string-list");

  function itemDefault() {
    if (overrides?.newItem) return overrides.newItem();
    const out = {};
    (field.item_schema || []).forEach((f) => (out[f.name] = f.default));
    return out;
  }

  function addRow(entry) {
    const item = el("div", "string-list-item schema-list-item");
    const fieldsCard = schemaFieldsCard(field.item_schema || [], entry, onDirty, overrides?.itemOverrides);
    item.appendChild(fieldsCard);
    const rm = textEl("button", "secondary", "−");
    rm.onclick = () => {
      const idx = draft[field.name].indexOf(entry);
      if (idx >= 0) draft[field.name].splice(idx, 1);
      item.remove();
      onDirty();
    };
    item.appendChild(rm);
    list.appendChild(item);
  }

  draft[field.name].forEach((entry) => addRow(entry));
  wrap.appendChild(list);
  const add = textEl("button", "secondary", `+ Add ${overrides?.itemLabel || "row"}`);
  add.onclick = () => {
    const entry = itemDefault();
    draft[field.name].push(entry);
    addRow(entry);
    onDirty();
  };
  wrap.appendChild(add);
  return wrap;
}

// dict[str, BaseModel] with arbitrary user-typed keys (routing.model_pools:
// pool name -> ModelPool). Cloud's dict[str, CloudProviderConfig] is NOT
// rendered with this widget — its keys are a fixed, server-known registry
// id set, not user-typed, so the cloud tab builds per-provider cards
// directly with schemaFieldsCard (see renderCloudProviderCard).
function schemaDictOfObjectsRow(field, draft, onDirty, overrides) {
  const wrap = el("div", "card");
  if (!draft[field.name]) draft[field.name] = {};
  const dict = draft[field.name];
  const list = el("div", "string-list");

  function addRow(key, entry) {
    const item = el("div", "string-list-item schema-list-item");
    const keyInput = document.createElement("input");
    keyInput.type = "text";
    keyInput.value = key;
    keyInput.placeholder = overrides?.keyPlaceholder || "name";
    keyInput.oninput = () => {
      const newKey = keyInput.value.trim();
      if (!newKey || newKey === key) return;
      delete dict[key];
      dict[newKey] = entry;
      key = newKey;
      onDirty();
    };
    item.appendChild(keyInput);
    item.appendChild(schemaFieldsCard(field.item_schema || [], entry, onDirty, overrides?.itemOverrides));
    const rm = textEl("button", "secondary", "−");
    rm.onclick = () => {
      delete dict[key];
      item.remove();
      onDirty();
    };
    item.appendChild(rm);
    list.appendChild(item);
  }

  Object.entries(dict).forEach(([key, entry]) => addRow(key, entry));
  wrap.appendChild(list);
  const add = textEl("button", "secondary", `+ Add ${overrides?.itemLabel || "row"}`);
  add.onclick = () => {
    const entry = {};
    (field.item_schema || []).forEach((f) => (entry[f.name] = f.default));
    const key = "";
    dict[key] = entry;
    addRow(key, entry);
    onDirty();
  };
  wrap.appendChild(add);
  return wrap;
}

function renderSchemaField(field, draft, onDirty, overrides) {
  if (field.widget === "toggle") {
    const checked = draft[field.name] !== false;
    return checkboxRow(schemaFieldLabel(field, overrides), checked, (v) => {
      draft[field.name] = v;
      onDirty();
      if (overrides?.onChange) overrides.onChange(v);
    });
  }
  if (field.widget === "select") return schemaSelectRow(field, draft, onDirty, overrides);
  if (field.widget === "number") return schemaFieldRow(field, draft, onDirty, overrides, "number");
  if (field.widget === "secret") return schemaSecretRow(field, draft, onDirty, overrides);
  if (field.widget === "list_strings") return schemaListStringsRow(field, draft, onDirty, overrides);
  if (field.widget === "dict_list_strings") return schemaDictListStringsRow(field, draft, onDirty, overrides);
  if (field.widget === "list") return schemaListOfObjectsRow(field, draft, onDirty, overrides);
  if (field.widget === "dict") return schemaDictOfObjectsRow(field, draft, onDirty, overrides);
  // "text" and any unrecognized widget render as a plain text input
  // rather than silently omitting the field.
  return schemaFieldRow(field, draft, onDirty, overrides, "text");
}

/**
 * Render every editable (non-read_only) field in `fields` (a schema
 * section's .fields, or a nested .item_schema) against `draft`, one card.
 * `overrides` is keyed by field name — see the header comment above.
 */
function schemaFieldsCard(fields, draft, onDirty, overrides = {}) {
  const card = el("div", "card");
  fields
    // `overrides[name].hidden` opts a field out of the generic layout
    // entirely — e.g. cloud.providers, which a caller renders itself
    // (per-provider cards need live registry metadata the schema doesn't
    // carry) rather than as a generic dict-of-objects widget.
    .filter((f) => !f.read_only && !overrides[f.name]?.hidden)
    .forEach((field) => {
      card.appendChild(renderSchemaField(field, draft, onDirty, overrides[field.name]));
    });
  return card;
}

/**
 * Render every editable field of one top-level config schema section —
 * thin wrapper over schemaFieldsCard for the common case.
 */
function renderSchemaForm(sectionKey, schema, draft, onDirty, overrides = {}) {
  const section = schema?.sections?.[sectionKey];
  if (!section) {
    const card = el("div", "card");
    card.appendChild(
      textEl("p", "muted-sm", "Config schema unavailable — check the admin API connection.")
    );
    return card;
  }
  return schemaFieldsCard(section.fields, draft, onDirty, overrides);
}

function renderAgentTab() {
  const root = document.getElementById("tab-agent");
  root.replaceChildren();
  root.appendChild(textEl("h1", "page-title", "Agent"));
  const card = el("div", "card");
  const { host, port } = parseListen();
  const lan = host === "0.0.0.0";
  let portInput;

  card.appendChild(
    checkboxRow("LAN mode (0.0.0.0)", lan, (v) =>
      setListen(v ? "0.0.0.0" : "127.0.0.1", parseInt(portInput.value, 10) || 11400)
    )
  );

  const portGroup = el("div", "form-group");
  portGroup.appendChild(textEl("label", "", "Port"));
  portInput = document.createElement("input");
  portInput.type = "number";
  portInput.value = port;
  portInput.onchange = () =>
    setListen(lan ? "0.0.0.0" : "127.0.0.1", parseInt(portInput.value, 10) || 11400);
  portGroup.appendChild(portInput);
  card.appendChild(portGroup);

  const roleGroup = el("div", "form-group");
  roleGroup.appendChild(textEl("label", "", "Role"));
  const roleSel = document.createElement("select");
  ROLES.forEach((r) => {
    const opt = document.createElement("option");
    opt.value = r;
    opt.textContent = r;
    if (state.configDraft.agent.role === r) opt.selected = true;
    roleSel.appendChild(opt);
  });
  roleSel.onchange = () => {
    state.configDraft.agent.role = roleSel.value;
    markDirty();
  };
  roleGroup.appendChild(roleSel);
  card.appendChild(roleGroup);

  card.appendChild(
    checkboxRow("Advertise on LAN", !!state.configDraft.agent.advertise, (v) => {
      state.configDraft.agent.advertise = v;
      markDirty();
    })
  );

  appendInfoRow(card, "Agent ID", state.configDraft.agent.agent_id);
  appendInfoRow(card, "Hostname", state.configDraft.agent.hostname);
  appendInfoRow(card, "Listen", state.configDraft.agent.listen);
  card.appendChild(textEl("p", "empty", "Changes apply after Save + restart agent."));
  root.appendChild(card);
}

function renderDiscoveryTab() {
  const root = document.getElementById("tab-discovery");
  root.replaceChildren();
  root.appendChild(textEl("h1", "page-title", "Discovery & known servers"));
  root.appendChild(
    textEl(
      "p",
      "empty",
      "Configure which local providers to scan, pin custom server URLs, and add static swarm agents. Click Save in the toolbar when done."
    )
  );

  root.appendChild(textEl("div", "section-label", "Providers to scan"));
  const card = el("div", "card");
  PROVIDERS.forEach((p) => {
    card.appendChild(
      checkboxRow(p, (state.configDraft.discovery.providers || []).includes(p), (v) => {
        const list = new Set(state.configDraft.discovery.providers || []);
        if (v) list.add(p);
        else list.delete(p);
        state.configDraft.discovery.providers = [...list];
        markDirty();
      })
    );
  });
  root.appendChild(card);

  // custom_endpoints/provider_urls/(swarm.)peers are schema-driven (phase
  // 3) — `providers` above stays hand-written: its UX is "toggle each
  // known provider on/off" against the fixed PROVIDERS list, not a
  // free-text list editor, and the schema has no enum for a bare
  // list[str] to drive that generically (see plan §6 risk 1's escape
  // hatch — this is exactly the "field needs a hand-authored widget"
  // case §2 point 4 anticipates).
  const discoveryFields = state.configSchema?.sections?.discovery?.fields || [];
  const byDiscoveryName = Object.fromEntries(discoveryFields.map((f) => [f.name, f]));

  root.appendChild(textEl("div", "section-label", "Custom servers (OpenAI-compatible)"));
  root.appendChild(
    textEl("p", "empty", "Any base URL the agent should route to — vLLM, custom gateways, etc.")
  );
  if (byDiscoveryName.custom_endpoints) {
    root.appendChild(
      renderSchemaField(byDiscoveryName.custom_endpoints, state.configDraft.discovery, markDirty, {
        placeholder: "http://127.0.0.1:8000/v1",
      })
    );
  }

  root.appendChild(textEl("div", "section-label", "Known provider URLs"));
  root.appendChild(
    textEl("p", "empty", "Pin non-default ports (e.g. oMLX on :8088). Tried before automatic port scan.")
  );
  if (byDiscoveryName.provider_urls) {
    root.appendChild(
      renderSchemaField(byDiscoveryName.provider_urls, state.configDraft.discovery, markDirty, {
        keys: PROVIDERS,
        placeholder: "http://127.0.0.1:8080/v1",
      })
    );
  }

  root.appendChild(textEl("div", "section-label", "Known swarm agents (static peers)"));
  root.appendChild(
    textEl("p", "empty", "Other netllm agents on your LAN — used when mDNS is blocked.")
  );
  const swarmPeersField = (state.configSchema?.sections?.swarm?.fields || []).find(
    (f) => f.name === "peers"
  );
  if (swarmPeersField) {
    root.appendChild(
      renderSchemaField(swarmPeersField, state.configDraft.swarm, markDirty, {
        placeholder: "http://10.0.0.32:11400",
      })
    );
  }
}

function renderSwarmTab() {
  const root = document.getElementById("tab-swarm");
  root.replaceChildren();
  root.appendChild(textEl("h1", "page-title", "Swarm"));
  // Whole section migrated to the schema-driven renderer (phase 3) —
  // this also surfaces require_token_for_inference/peer_stale_after_s/
  // rediscover_interval_s, which the hand-written version never exposed
  // (see docs/config-schema-rewrite-plan.md §1's Swift/JS drift
  // evidence — this dashboard had the same gap Swift did).
  const tokenLabel = state.configDraft.swarm.cluster_token_set
    ? "Cluster token (secured)"
    : "Cluster token (open trusted LAN)";
  root.appendChild(
    renderSchemaForm("swarm", state.configSchema, state.configDraft.swarm, markDirty, {
      cluster_token: { label: tokenLabel, pendingKey: "_cluster_token" },
      heartbeat_interval_s: { step: "0.5" },
      peer_stale_after_s: { step: "0.5" },
      rediscover_interval_s: { step: "0.5" },
      subnet_cidrs: { placeholder: "10.0.0.0/24", itemLabel: "CIDR" },
      peers: { placeholder: "http://10.0.0.32:11400", itemLabel: "peer" },
    })
  );
}

// Resolves a schema field's "default_factory" hint (§6 risk 1 — a
// named builder for a sensible "Add row" default instead of an empty
// one) to the actual JS factory function.
const SCHEMA_ITEM_FACTORIES = {
  local_openai_policy: () => ({
    name: "local-openai",
    model_prefix: "",
    api_format: "openai",
    strategy: null,
    prefer_provider: null,
    allow_cloud: false,
    enabled: true,
  }),
};

function renderRoutingTab() {
  const root = document.getElementById("tab-routing");
  root.replaceChildren();
  root.appendChild(textEl("h1", "page-title", "Routing"));

  const fields = state.configSchema?.sections?.routing?.fields || [];
  const byName = Object.fromEntries(fields.map((f) => [f.name, f]));
  const draft = state.configDraft.routing;

  const card = el("div", "card");
  [
    "default_strategy",
    "allow_remote",
    "require_same_model_for_shard",
    "max_in_flight_per_backend",
    "follow_gateway",
    "spillover_max_local_in_flight",
    "health_ttl_s",
    "offline_retry_s",
    "max_backend_failures",
  ].forEach((name) => {
    if (byName[name]) card.appendChild(renderSchemaField(byName[name], draft, markDirty));
  });
  root.appendChild(card);

  root.appendChild(textEl("div", "section-label", "Routing policies"));
  root.appendChild(
    textEl(
      "p",
      "empty",
      "First match wins. Cloud routing requires allow_cloud on an explicit policy row."
    )
  );
  if (byName.policies) {
    root.appendChild(
      renderSchemaField(byName.policies, draft, markDirty, {
        itemLabel: "routing policy",
        newItem: SCHEMA_ITEM_FACTORIES[byName.policies.default_factory],
        itemOverrides: {
          api_format: { optionLabels: { "": "any api_format" } },
          strategy: { optionLabels: { "": "default strategy" } },
        },
      })
    );
  }

  root.appendChild(textEl("div", "section-label", "Backend overrides"));
  root.appendChild(
    textEl("p", "empty", "Manual routing entries for specific upstream URLs (optional).")
  );
  if (byName.backends) {
    root.appendChild(renderSchemaField(byName.backends, draft, markDirty, { itemLabel: "backend" }));
  }

  root.appendChild(textEl("div", "section-label", "Model pools"));
  root.appendChild(
    textEl(
      "p",
      "empty",
      "Host-scoped catch-all: listed hosts accept any requested model name, bypassing model_aliases matching, as long as they serve one of the pool's models."
    )
  );
  if (byName.model_pools) {
    root.appendChild(
      renderSchemaField(byName.model_pools, draft, markDirty, {
        itemLabel: "model pool",
        keyPlaceholder: "pool name",
      })
    );
  }
}


const CLOUD_FALLBACK_MODES = ["cloud", "local", "none"];

function renderCloudTab() {
  const root = document.getElementById("tab-cloud");
  root.replaceChildren();
  root.appendChild(textEl("h1", "page-title", "Cloud"));
  if (!state.configDraft.cloud) {
    state.configDraft.cloud = { enabled: true, fallback: "cloud", fallback_enabled: true, providers: {} };
  }
  const draft = state.configDraft.cloud;
  const summaryProviders = (state.config?.cloud?.providers) || {};

  // Master switch / fallback direction / fallback_enabled: plain scalar
  // fields, schema-driven (phase 3).
  root.appendChild(
    renderSchemaForm("cloud", state.configSchema, draft, markDirty, {
      enabled: { label: "Cloud enabled (master switch)" },
      fallback: {
        optionLabels: {
          cloud: "cloud (local first, cloud fallback)",
          local: "local (cloud first, local fallback)",
          none: "none (no automatic fallback)",
        },
      },
      fallback_enabled: { label: "Fallback enabled" },
      // providers is rendered separately below (per-provider cards need
      // the live registry summary — display name, regions, api_key_set —
      // that only /netllm/v1/config carries, not the shape-only schema).
      providers: { hidden: true },
    })
  );

  root.appendChild(textEl("div", "section-label", "Providers"));
  root.appendChild(
    textEl("p", "empty", "Keys are write-only — a key already stored is never shown back.")
  );

  // Server-driven provider set (matches config_summary's cloud.providers,
  // which always lists every registry provider) — bootstrap list only
  // covers the admin-API-unreachable case.
  const providerIds = Object.keys(draft.providers).length
    ? Object.keys(draft.providers)
    : CLOUD_PROVIDER_IDS_BOOTSTRAP;
  const itemSchema = state.configSchema?.sections?.cloud?.fields?.find(
    (f) => f.name === "providers"
  )?.item_schema;
  providerIds.forEach((pid) => {
    root.appendChild(renderCloudProviderCard(pid, draft, summaryProviders[pid] || {}, itemSchema));
  });
}

// cloud.providers is a dict[str, CloudProviderConfig], but unlike
// routing.model_pools its keys are NOT user-typed — they're the fixed,
// server-known registry id set (GET /netllm/v1/cloud/providers), each
// with live display metadata (name, regions, notes, api_key_set) the
// bare shape schema doesn't carry. So this renders one card per known id
// directly with schemaFieldsCard/renderSchemaField, rather than going
// through schemaDictOfObjectsRow's generic add/remove-key UI.
function renderCloudProviderCard(pid, cloudDraft, summary, itemSchema) {
  if (!cloudDraft.providers[pid]) {
    cloudDraft.providers[pid] = { enabled: false, region: "", api_format: null };
  }
  const entry = cloudDraft.providers[pid];
  const card = el("div", "card");
  const title = summary.display_name || pid;
  card.appendChild(textEl("div", "section-label", title));
  if (summary.notes) {
    card.appendChild(textEl("p", "empty", summary.notes));
  }
  if (!itemSchema) {
    card.appendChild(textEl("p", "muted-sm", "Config schema unavailable."));
    return card;
  }
  const byName = Object.fromEntries(itemSchema.map((f) => [f.name, f]));
  const regions = summary.regions && summary.regions.length ? summary.regions : [""];
  const keyLabel = summary.api_key_set ? "API key (set — enter to replace)" : "API key";
  ["enabled", "region", "api_format", "api_key"].forEach((name) => {
    if (!byName[name]) return;
    const overrides =
      name === "enabled"
        ? { label: `Enable ${title}` }
        : name === "region"
          ? { label: "Region / profile", options: regions, optionLabels: { "": "default" } }
          : name === "api_format"
            ? { optionLabels: { "": `default (${summary.default_api_format || "openai"})` } }
            : { label: keyLabel, placeholder: summary.api_key_set ? "•••••••• (unchanged if left blank)" : "sk-..." };
    card.appendChild(renderSchemaField(byName[name], entry, markDirty, overrides));
  });
  return card;
}

function renderUiTab() {
  const root = document.getElementById("tab-ui");
  root.replaceChildren();
  root.appendChild(textEl("h1", "page-title", "UI"));
  // First tab migrated to the schema-driven renderer (phase 2 of
  // docs/config-schema-rewrite-plan.md) — field order, labels, and
  // widgets come from GET /netllm/v1/config/schema instead of being
  // hand-built here. `overrides` covers the two things the schema can't
  // express: the auto-start label's macOS-specific parenthetical, and
  // check_for_updates_automatically's start/stop-polling side effect.
  root.appendChild(
    renderSchemaForm("ui", state.configSchema, state.configDraft.ui, markDirty, {
      check_for_updates_automatically: {
        label: "Check for updates automatically",
        onChange: (v) => (v ? startUpdatePolling() : stopUpdatePolling()),
      },
      auto_start_on_launch: { label: "Auto-start on launch (macOS menubar)" },
      log_dir: { label: "Log directory", placeholder: "Default platform log dir" },
    })
  );
}

async function loadLogs() {
  state.logs = await api("/netllm/v1/logs?tail=200");
}

function renderLogsTab() {
  const root = document.getElementById("tab-logs");
  root.replaceChildren();
  root.appendChild(textEl("h1", "page-title", "Logs"));

  const logs = state.logs;
  if (!logs) {
    root.appendChild(textEl("p", "empty", "Loading logs…"));
    loadLogs()
      .then(() => {
        if (state.tab === "logs") renderLogsTab();
      })
      .catch((e) => showToast("Logs failed: " + e.message));
    return;
  }

  const meta = el("div", "card");
  appendInfoRow(meta, "Log directory", logs.log_dir);
  appendInfoRow(meta, "Log file", logs.log_file);
  appendInfoRow(
    meta,
    "Size",
    logs.exists ? `${logs.size_bytes} bytes` : "File not created yet"
  );
  if (logs.truncated) {
    meta.appendChild(textEl("p", "muted-sm", "Showing the last 200 lines (file has more)."));
  }
  root.appendChild(meta);

  const actions = el("div", "card");
  const refresh = textEl("button", "secondary", "Refresh");
  refresh.onclick = () => {
    loadLogs()
      .then(() => renderLogsTab())
      .catch((e) => showToast("Refresh failed: " + e.message));
  };
  const copyPath = textEl("button", "secondary", "Copy log path");
  copyPath.onclick = () =>
    navigator.clipboard.writeText(logs.log_file).then(() => showToast("Copied log path"));
  const copyDir = textEl("button", "secondary", "Copy log directory");
  copyDir.onclick = () =>
    navigator.clipboard.writeText(logs.log_dir).then(() => showToast("Copied log directory"));
  actions.append(refresh, copyPath, copyDir);
  root.appendChild(actions);

  const pre = el("pre", "log-view");
  pre.textContent = (logs.tail || []).join("\n") || (logs.exists ? "" : "(no log output yet)");
  root.appendChild(pre);
}

function renderToolsTab() {
  const root = document.getElementById("tab-tools");
  root.replaceChildren();
  root.appendChild(textEl("h1", "page-title", "Doctor & Test"));

  root.appendChild(textEl("div", "section-label", "Doctor"));
  const docCard = el("div", "card");
  const ok = state.doctor?.ok;
  const badge = el("span", `badge ${ok ? "ok" : "warn"}`);
  badge.appendChild(el("span", "dot"));
  badge.appendChild(document.createTextNode(ok ? "All checks passed" : "Issues found"));
  docCard.appendChild(badge);
  (state.doctor?.issues || []).forEach((issue) => {
    const div = el("div", "issue");
    div.appendChild(textEl("div", "title", issue.title));
    div.appendChild(textEl("div", "fix", issue.fix));
    docCard.appendChild(div);
  });
  if (!(state.doctor?.issues || []).length && ok) {
    docCard.appendChild(textEl("p", "empty", "No issues reported."));
  }
  root.appendChild(docCard);

  root.appendChild(textEl("div", "section-label", "CLI tools"));
  const cmds = [
    ["netllm test", "1-token latency probe via agent"],
    ["netllm test --api anthropic", "Messages API probe"],
    ["netllm gateway", "Promote agent role to gateway"],
    ["netllm models --lan", "Models on remote LAN agents"],
    ["netllm config-edit", "Open config.toml in $EDITOR"],
    ["netllm doctor", "Full doctor (PATH, port, providers)"],
  ];
  cmds.forEach(([cmd, desc]) => {
    const card = el("div", "card cli-card");
    card.appendChild(textEl("div", "muted-sm", desc));
    const row = el("div", "cmd");
    row.appendChild(codeEl(cmd));
    const copy = textEl("button", "secondary", "Copy");
    copy.onclick = () => navigator.clipboard.writeText(cmd).then(() => showToast("Copied"));
    row.appendChild(copy);
    card.appendChild(row);
    root.appendChild(card);
  });

  root.appendChild(textEl("div", "section-label", "Client environment"));
  const envPre = el("pre");
  envPre.textContent = state.envText;
  root.appendChild(envPre);
}

const BLOCKED_PATH_KEYS = new Set(["__proto__", "constructor", "prototype"]);

function getByPath(obj, path) {
  return path.split(".").reduce((o, k) => {
    if (BLOCKED_PATH_KEYS.has(k)) return undefined;
    return o ? o[k] : undefined;
  }, obj);
}

function setByPath(obj, path, value) {
  const parts = path.split(".");
  if (parts.some((k) => BLOCKED_PATH_KEYS.has(k))) return;
  let cur = obj;
  for (let i = 0; i < parts.length - 1; i++) {
    const key = parts[i];
    if (
      !Object.prototype.hasOwnProperty.call(cur, key) ||
      typeof cur[key] !== "object" ||
      cur[key] === null
    ) {
      cur[key] = {};
    }
    cur = cur[key];
  }
  cur[parts[parts.length - 1]] = value;
}

function renderStringListEditor(path, placeholder) {
  const wrap = el("div", "card string-list");
  const list = el("div", "string-list");
  const items = getByPath(state.configDraft, path) || [];

  function sync() {
    const inputs = list.querySelectorAll("input");
    setByPath(
      state.configDraft,
      path,
      [...inputs].map((i) => i.value.trim()).filter(Boolean)
    );
    markDirty();
  }

  function addRow(value = "") {
    const item = el("div", "string-list-item");
    const input = document.createElement("input");
    input.type = "text";
    input.value = value;
    input.placeholder = placeholder;
    input.oninput = sync;
    const rm = textEl("button", "secondary", "−");
    rm.onclick = () => {
      item.remove();
      sync();
    };
    item.append(input, rm);
    list.appendChild(item);
  }

  items.forEach(addRow);
  const add = textEl("button", "secondary", "+ Add");
  add.onclick = () => addRow();
  wrap.append(list, add);
  return wrap;
}

const TAB_RENDERERS = {
  status: renderStatusTab,
  backends: renderBackendsTab,
  models: renderModelsTab,
  peers: renderPeersTab,
  agent: renderAgentTab,
  discovery: renderDiscoveryTab,
  swarm: renderSwarmTab,
  routing: renderRoutingTab,
  cloud: renderCloudTab,
  ui: renderUiTab,
  logs: renderLogsTab,
  tools: renderToolsTab,
};

function render() {
  if (!state.configDraft) state.configDraft = emptyConfigDraft();
  const fn = TAB_RENDERERS[state.tab];
  if (fn) fn();
}

function switchTab(tab) {
  state.tab = tab;
  document.querySelectorAll(".nav-item").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tab);
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === `tab-${tab}`);
  });
  if (tab === "logs") {
    startLogsPolling();
  } else {
    stopLogsPolling();
  }
  render();
}

// Turns one list/dict item's draft object (built by schemaListOfObjectsRow
// / schemaDictOfObjectsRow / renderCloudProviderCard, each entry a plain
// {field: value, ...} possibly carrying "_pending_<field>" secret
// scratch keys) into the wire shape admin.apply_config_patch expects:
// read_only fields dropped, write_only fields included only when a
// pending value was actually typed (so omission preserves the prior
// stored value server-side, same contract as before this rewrite).
function schemaItemToPatch(itemSchema, entry) {
  const out = {};
  (itemSchema || []).forEach((f) => {
    if (f.read_only) return;
    if (f.write_only) {
      const pendingKey = `_pending_${f.name}`;
      if (entry[pendingKey]) out[f.name] = entry[pendingKey];
      return;
    }
    out[f.name] = entry[f.name];
  });
  return out;
}

// Builds one section's patch by walking its schema fields — list/dict of
// objects recurse through schemaItemToPatch, write_only scalars use
// `pendingKeys[field]` (default "_pending_<field>"; swarm.cluster_token
// keeps its pre-existing "_cluster_token" key, passed explicitly), and a
// missing schema (older agent / fetch failure) falls back to a shallow
// copy of the draft stripped of "_"-prefixed scratch keys rather than
// silently emitting {} and wiping the section on save.
function buildSchemaSectionPatch(sectionKey, schema, draft, pendingKeys = {}) {
  const section = schema?.sections?.[sectionKey];
  if (!section) {
    const out = {};
    Object.entries(draft || {}).forEach(([k, v]) => {
      if (!k.startsWith("_")) out[k] = v;
    });
    return out;
  }
  const patch = {};
  section.fields.forEach((field) => {
    if (field.read_only) return;
    if (field.write_only) {
      const pendingKey = pendingKeys[field.name] || `_pending_${field.name}`;
      if (draft[pendingKey]) patch[field.name] = draft[pendingKey];
      return;
    }
    if (field.widget === "list" && field.item_schema) {
      patch[field.name] = (draft[field.name] || []).map((entry) =>
        schemaItemToPatch(field.item_schema, entry)
      );
      return;
    }
    if (field.widget === "dict" && field.item_schema) {
      const out = {};
      Object.entries(draft[field.name] || {}).forEach(([key, entry]) => {
        if (!key) return; // skip an in-progress "type a name" row
        out[key] = schemaItemToPatch(field.item_schema, entry);
      });
      patch[field.name] = out;
      return;
    }
    patch[field.name] = draft[field.name];
  });
  return patch;
}

function buildConfigPatch() {
  const d = state.configDraft;
  const schema = state.configSchema;
  return {
    agent: buildSchemaSectionPatch("agent", schema, d.agent),
    discovery: buildSchemaSectionPatch("discovery", schema, d.discovery),
    swarm: buildSchemaSectionPatch("swarm", schema, d.swarm, {
      cluster_token: "_cluster_token",
    }),
    routing: buildSchemaSectionPatch("routing", schema, d.routing),
    ui: buildSchemaSectionPatch("ui", schema, d.ui),
    cloud: buildCloudPatch(d.cloud, schema),
  };
}

function buildCloudPatch(cloudDraft, schema) {
  const draft = cloudDraft || { enabled: true, fallback: "cloud", fallback_enabled: true, providers: {} };
  const itemSchema = schema?.sections?.cloud?.fields?.find((f) => f.name === "providers")?.item_schema;
  const providers = {};
  Object.entries(draft.providers || {}).forEach(([pid, entry]) => {
    providers[pid] = itemSchema
      ? schemaItemToPatch(itemSchema, entry)
      : {
          enabled: !!entry.enabled,
          region: entry.region || "",
          api_format: entry.api_format || null,
          ...(entry._pending_api_key ? { api_key: entry._pending_api_key } : {}),
        };
  });
  return {
    enabled: draft.enabled !== false,
    fallback: draft.fallback || "cloud",
    fallback_enabled: draft.fallback_enabled !== false,
    providers,
  };
}

async function saveConfig() {
  document.getElementById("btn-save").disabled = true;
  try {
    const result = await api("/netllm/v1/admin/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildConfigPatch()),
    });
    Object.values(state.configDraft.cloud?.providers || {}).forEach((entry) => {
      delete entry._pending_key;
    });
    state.config = cloneConfig(state.configDraft);
    markDirty(false);
    const saveNotes = [];
    if (result.needs_restart) {
      saveNotes.push("Saved — restart agent to apply listen/port changes.");
    }
    if (result.warnings && result.warnings.length) {
      saveNotes.push(result.warnings.join(" "));
    }
    if (saveNotes.length) {
      setBanner(saveNotes.join(" "), "warn");
    } else {
      setBanner("Configuration saved.", "ok");
    }
    showToast("Configuration saved");
  } catch (e) {
    showToast("Save failed: " + e.message);
    markDirty(true);
  }
}

async function runDiscover() {
  const btn = document.getElementById("btn-discover");
  btn.disabled = true;
  try {
    await api("/netllm/v1/admin/discover", { method: "POST" });
    showToast("Discovery complete");
    await refresh();
  } catch (e) {
    showToast("Discover failed: " + e.message);
  } finally {
    btn.disabled = false;
  }
}

async function runPeersScan(save) {
  try {
    showToast("Scanning LAN…");
    const result = await api(`/netllm/v1/admin/peers-scan?save=${save}`, { method: "POST" });
    state.lanPeers = result.peers || [];
    if (save) await loadCore();
    showToast(result.warnings?.[0] || `Found ${state.lanPeers.length} peer(s)`);
    if (state.tab === "peers") renderPeersTab();
  } catch (e) {
    showToast("Scan failed: " + e.message);
  }
}

async function refresh() {
  await loadHealth();
  await Promise.all([
    loadCore(),
    loadVersionInfo(),
    loadUpdateCheck(),
    loadConfigSchema(),
  ]);
  render();
}

function startUpdatePolling() {
  stopUpdatePolling();
  if (!checkForUpdatesAutomatically()) return;
  state.updatePollTimer = setInterval(() => {
    if (document.visibilityState !== "visible") return;
    loadUpdateCheck()
      .then(() => {
        if (state.tab === "status") renderStatusTab();
      })
      .catch(() => {});
  }, 3600000);
}

function stopUpdatePolling() {
  if (state.updatePollTimer) clearInterval(state.updatePollTimer);
  state.updatePollTimer = null;
}

function startPolling() {
  stopPolling();
  state.pollTimer = setInterval(() => {
    if (document.visibilityState !== "visible" || state.tab !== "status") return;
    loadHealth()
      .then(() => loadCore())
      .then(() => {
        updateStatusLine();
        if (state.tab === "status") renderStatusTab();
      })
      .catch(() => {});
  }, 5000);
}

function stopPolling() {
  if (state.pollTimer) clearInterval(state.pollTimer);
  state.pollTimer = null;
}

function startLogsPolling() {
  stopLogsPolling();
  state.logsPollTimer = setInterval(() => {
    if (document.visibilityState !== "visible" || state.tab !== "logs") return;
    loadLogs()
      .then(() => {
        if (state.tab === "logs") renderLogsTab();
      })
      .catch(() => {});
  }, 10000);
}

function stopLogsPolling() {
  if (state.logsPollTimer) clearInterval(state.logsPollTimer);
  state.logsPollTimer = null;
}

document.querySelectorAll(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});

document.getElementById("btn-refresh").addEventListener("click", () => {
  refresh().catch((e) => showToast(e.message));
});

document.getElementById("btn-discover").addEventListener("click", runDiscover);

document.getElementById("btn-env").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(state.envText);
    showToast("Copied client env to clipboard");
  } catch {
    showToast("Copy failed — see Doctor tab");
  }
});

document.getElementById("btn-save").addEventListener("click", saveConfig);

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    startPolling();
    startUpdatePolling();
    if (state.tab === "logs") startLogsPolling();
  } else {
    stopPolling();
    stopUpdatePolling();
    stopLogsPolling();
  }
});

refresh()
  .then(() => {
    startPolling();
    startUpdatePolling();
  })
  .catch((e) => {
    setBanner("Agent unreachable — " + e.message, "error");
    showToast(e.message);
    switchTab("status");
  });
