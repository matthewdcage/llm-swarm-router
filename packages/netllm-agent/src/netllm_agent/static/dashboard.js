/* llm-swarm-router web dashboard — native Settings parity */

const PROVIDERS = ["omlx", "ollama", "lmstudio", "vllm"];
const STRATEGIES = [
  "local_first",
  "failover",
  "round_robin",
  "least_load",
  "latency_weighted",
  "batch_shard",
];
const ROLES = ["peer", "gateway"];

const state = {
  tab: "status",
  dirty: false,
  config: null,
  configDraft: null,
  status: null,
  models: [],
  doctor: null,
  envText: "",
  lanPeers: [],
  healthy: false,
  pollTimer: null,
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
    throw new Error(text || `${res.status} ${res.statusText}`);
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

async function loadCore() {
  const status = await api("/netllm/v1/status");
  state.status = status;
  const [models, doctor, env] = await Promise.all([
    api("/v1/models"),
    api("/netllm/v1/doctor"),
    api("/netllm/v1/client-env"),
  ]);
  state.models = models.data || [];
  state.doctor = doctor;
  const vars = env.vars || env;
  state.envText = Object.entries(vars)
    .map(([k, v]) => `export ${k}=${v}`)
    .join("\n");
  try {
    const config = await api("/netllm/v1/config");
    if (!state.dirty) {
      state.config = config;
      state.configDraft = cloneConfig(config);
    }
  } catch (e) {
    setBanner(
      "Config editor unavailable (admin API). Editable tabs need loopback access.",
      "warn"
    );
    if (!state.configDraft) {
      state.configDraft = emptyConfigDraft();
    }
  }
  updateStatusLine();
}

function emptyConfigDraft() {
  return {
    agent: { listen: "127.0.0.1:11400", role: "peer", advertise: true, agent_id: "", hostname: "" },
    discovery: { providers: [...PROVIDERS], provider_urls: {}, custom_endpoints: [] },
    swarm: { mdns: true, subnet_scan: false, subnet_cidrs: [], heartbeat_interval_s: 10, peers: [], cluster_token_set: false },
    routing: { default_strategy: "local_first", allow_remote: true, require_same_model_for_shard: true, backends: [], backend_count: 0 },
    ui: { auto_start_on_launch: true, log_dir: "" },
  };
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
  const sub1 = el("p");
  sub1.append("Mesh router for local LLM backends · CLI: ", codeEl("netllm"));
  meta.appendChild(sub1);
  meta.appendChild(textEl("p", "", state.status?.listen_url || "—"));
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
  appendInfoRow(sys, "Agent ID", state.status?.agent_id);
  appendInfoRow(sys, "Hostname", state.status?.hostname);
  appendInfoRow(sys, "Role", state.status?.role);
  appendInfoRow(sys, "Strategy", state.status?.routing_strategy);
  appendInfoRow(sys, "Listen", state.status?.listen_url);
  root.appendChild(sys);

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
  body.appendChild(textEl("strong", "", p.hostname || p.agent_id || "peer"));
  body.appendChild(textEl("div", "muted-sm", p.listen_url || ""));
  body.appendChild(textEl("div", "muted-sm", `role: ${p.role || "peer"}`));
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

  root.appendChild(textEl("div", "section-label", "Custom servers (OpenAI-compatible)"));
  root.appendChild(
    textEl("p", "empty", "Any base URL the agent should route to — vLLM, custom gateways, etc.")
  );
  root.appendChild(
    renderStringListEditor("discovery.custom_endpoints", "http://127.0.0.1:8000/v1")
  );

  root.appendChild(textEl("div", "section-label", "Known provider URLs"));
  root.appendChild(
    textEl("p", "empty", "Pin non-default ports (e.g. oMLX on :8088). Tried before automatic port scan.")
  );
  PROVIDERS.forEach((p) => {
    root.appendChild(textEl("div", "section-label", `${p}`));
    root.appendChild(renderProviderUrlsEditor(p));
  });

  root.appendChild(textEl("div", "section-label", "Known swarm agents (static peers)"));
  root.appendChild(
    textEl("p", "empty", "Other netllm agents on your LAN — used when mDNS is blocked.")
  );
  root.appendChild(renderStringListEditor("swarm.peers", "http://10.0.0.32:11400"));
}

function renderProviderUrlsEditor(provider) {
  const wrap = el("div", "card string-list");
  if (!state.configDraft.discovery.provider_urls) state.configDraft.discovery.provider_urls = {};
  const urls = state.configDraft.discovery.provider_urls[provider] || [];
  const list = el("div", "string-list");

  function sync() {
    const inputs = list.querySelectorAll("input");
    state.configDraft.discovery.provider_urls[provider] = [...inputs]
      .map((i) => i.value.trim())
      .filter(Boolean);
    markDirty();
  }

  function addRow(value = "") {
    const item = el("div", "string-list-item");
    const input = document.createElement("input");
    input.type = "text";
    input.value = value;
    input.placeholder = "http://127.0.0.1:8080/v1";
    input.oninput = sync;
    const rm = textEl("button", "secondary", "−");
    rm.onclick = () => {
      item.remove();
      sync();
    };
    item.append(input, rm);
    list.appendChild(item);
  }

  urls.forEach(addRow);
  const add = textEl("button", "secondary", "+ Add URL");
  add.onclick = () => addRow();
  wrap.append(list, add);
  return wrap;
}

function renderSwarmTab() {
  const root = document.getElementById("tab-swarm");
  root.replaceChildren();
  root.appendChild(textEl("h1", "page-title", "Swarm"));
  const card = el("div", "card");

  card.appendChild(
    checkboxRow("mDNS discovery", !!state.configDraft.swarm.mdns, (v) => {
      state.configDraft.swarm.mdns = v;
      markDirty();
    })
  );
  card.appendChild(
    checkboxRow("Subnet scan at startup", !!state.configDraft.swarm.subnet_scan, (v) => {
      state.configDraft.swarm.subnet_scan = v;
      markDirty();
    })
  );

  const hb = el("div", "form-group");
  hb.appendChild(textEl("label", "", "Heartbeat interval (seconds)"));
  const hbInput = document.createElement("input");
  hbInput.type = "number";
  hbInput.step = "0.5";
  hbInput.value = state.configDraft.swarm.heartbeat_interval_s ?? 10;
  hbInput.onchange = () => {
    state.configDraft.swarm.heartbeat_interval_s = parseFloat(hbInput.value) || 10;
    markDirty();
  };
  hb.appendChild(hbInput);
  card.appendChild(hb);

  const token = el("div", "form-group");
  const tokenLabel = state.configDraft.swarm.cluster_token_set ? "(set)" : "(not set)";
  token.appendChild(textEl("label", "", `Cluster token ${tokenLabel}`));
  const tokenInput = document.createElement("input");
  tokenInput.type = "password";
  tokenInput.placeholder = "Leave blank to keep existing";
  tokenInput.oninput = () => {
    state.configDraft._cluster_token = tokenInput.value;
    markDirty();
  };
  token.appendChild(tokenInput);
  card.appendChild(token);
  root.appendChild(card);

  root.appendChild(textEl("div", "section-label", "Subnet CIDRs"));
  root.appendChild(renderStringListEditor("swarm.subnet_cidrs", "10.0.0.0/24"));
  root.appendChild(textEl("div", "section-label", "Static peers"));
  root.appendChild(renderStringListEditor("swarm.peers", "http://10.0.0.32:11400"));
}

function renderRoutingTab() {
  const root = document.getElementById("tab-routing");
  root.replaceChildren();
  root.appendChild(textEl("h1", "page-title", "Routing"));
  const card = el("div", "card");

  const strat = el("div", "form-group");
  strat.appendChild(textEl("label", "", "Default strategy"));
  const sel = document.createElement("select");
  STRATEGIES.forEach((s) => {
    const opt = document.createElement("option");
    opt.value = s;
    opt.textContent = s;
    if (state.configDraft.routing.default_strategy === s) opt.selected = true;
    sel.appendChild(opt);
  });
  sel.onchange = () => {
    state.configDraft.routing.default_strategy = sel.value;
    markDirty();
  };
  strat.appendChild(sel);
  card.appendChild(strat);

  card.appendChild(
    checkboxRow("Allow remote backends", !!state.configDraft.routing.allow_remote, (v) => {
      state.configDraft.routing.allow_remote = v;
      markDirty();
    })
  );
  card.appendChild(
    checkboxRow("Require same model for shard", !!state.configDraft.routing.require_same_model_for_shard, (v) => {
      state.configDraft.routing.require_same_model_for_shard = v;
      markDirty();
    })
  );
  root.appendChild(card);

  root.appendChild(textEl("div", "section-label", "Backend overrides"));
  root.appendChild(
    textEl("p", "empty", "Manual routing entries for specific upstream URLs (optional).")
  );
  root.appendChild(renderBackendOverridesEditor());
}

function renderBackendOverridesEditor() {
  const wrap = el("div", "card");
  if (!state.configDraft.routing.backends) state.configDraft.routing.backends = [];
  const list = el("div", "string-list");

  function sync() {
    const rows = list.querySelectorAll(".backend-override-row");
    state.configDraft.routing.backends = [...rows].map((row) => {
      const url = row.querySelector(".bo-url");
      const provider = row.querySelector(".bo-provider");
      const enabled = row.querySelector(".bo-enabled");
      return {
        base_url: url.value.trim(),
        provider: provider.value,
        enabled: enabled.checked,
      };
    }).filter((b) => b.base_url);
    markDirty();
  }

  function addRow(entry = {}) {
    const item = el("div", "backend-override-row string-list-item");
    const url = document.createElement("input");
    url.type = "text";
    url.className = "bo-url";
    url.placeholder = "http://127.0.0.1:11434/v1";
    url.value = entry.base_url || "";
    url.oninput = sync;
    const provider = document.createElement("select");
    provider.className = "bo-provider";
    [...PROVIDERS, "custom"].forEach((p) => {
      const opt = document.createElement("option");
      opt.value = p;
      opt.textContent = p;
      if ((entry.provider || "custom") === p) opt.selected = true;
      provider.appendChild(opt);
    });
    provider.onchange = sync;
    const enabled = document.createElement("input");
    enabled.type = "checkbox";
    enabled.className = "bo-enabled";
    enabled.checked = entry.enabled !== false;
    enabled.title = "Enabled";
    enabled.onchange = sync;
    const rm = textEl("button", "secondary", "−");
    rm.onclick = () => { item.remove(); sync(); };
    item.append(url, provider, enabled, rm);
    list.appendChild(item);
  }

  (state.configDraft.routing.backends || []).forEach(addRow);
  const add = textEl("button", "secondary", "+ Add backend");
  add.onclick = () => addRow();
  wrap.append(list, add);
  return wrap;
}

function renderUiTab() {
  const root = document.getElementById("tab-ui");
  root.replaceChildren();
  root.appendChild(textEl("h1", "page-title", "UI"));
  const card = el("div", "card");
  card.appendChild(
    checkboxRow("Auto-start on launch (macOS menubar)", !!state.configDraft.ui.auto_start_on_launch, (v) => {
      state.configDraft.ui.auto_start_on_launch = v;
      markDirty();
    })
  );
  const logGroup = el("div", "form-group");
  logGroup.appendChild(textEl("label", "", "Log directory"));
  const logInput = document.createElement("input");
  logInput.type = "text";
  logInput.value = state.configDraft.ui.log_dir || "";
  logInput.placeholder = "Default platform log dir";
  logInput.oninput = () => {
    state.configDraft.ui.log_dir = logInput.value;
    markDirty();
  };
  logGroup.appendChild(logInput);
  card.appendChild(logGroup);
  root.appendChild(card);
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

function getByPath(obj, path) {
  return path.split(".").reduce((o, k) => (o ? o[k] : undefined), obj);
}

function setByPath(obj, path, value) {
  const parts = path.split(".");
  let cur = obj;
  for (let i = 0; i < parts.length - 1; i++) {
    if (!cur[parts[i]]) cur[parts[i]] = {};
    cur = cur[parts[i]];
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
  ui: renderUiTab,
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
  render();
}

function buildConfigPatch() {
  const d = state.configDraft;
  const patch = {
    agent: {
      listen: d.agent.listen,
      role: d.agent.role,
      advertise: d.agent.advertise,
    },
    discovery: {
      providers: d.discovery.providers,
      provider_urls: d.discovery.provider_urls,
      custom_endpoints: d.discovery.custom_endpoints,
    },
    swarm: {
      mdns: d.swarm.mdns,
      subnet_scan: d.swarm.subnet_scan,
      subnet_cidrs: d.swarm.subnet_cidrs,
      heartbeat_interval_s: d.swarm.heartbeat_interval_s,
      peers: d.swarm.peers,
    },
    routing: {
      default_strategy: d.routing.default_strategy,
      allow_remote: d.routing.allow_remote,
      require_same_model_for_shard: d.routing.require_same_model_for_shard,
      backends: d.routing.backends || [],
    },
    ui: {
      auto_start_on_launch: d.ui.auto_start_on_launch,
      log_dir: d.ui.log_dir,
    },
  };
  if (d._cluster_token) patch.swarm.cluster_token = d._cluster_token;
  return patch;
}

async function saveConfig() {
  document.getElementById("btn-save").disabled = true;
  try {
    const result = await api("/netllm/v1/admin/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildConfigPatch()),
    });
    state.config = cloneConfig(state.configDraft);
    markDirty(false);
    if (result.needs_restart) {
      setBanner("Saved — restart agent to apply listen/port changes.", "warn");
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
  await loadCore();
  render();
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
  if (document.visibilityState === "visible") startPolling();
  else stopPolling();
});

refresh()
  .then(() => startPolling())
  .catch((e) => {
    setBanner("Agent unreachable — " + e.message, "error");
    showToast(e.message);
    switchTab("status");
  });
