/* Peers page — design 2b.
 *
 * One list instead of three. The old tab stacked "connected peers", "scan
 * results" and "static peers in config" as separate cards, so the same
 * machine could appear three times with no way to tell that it was one
 * machine. Here the three sources are merged into a single roster keyed on
 * agent_id (falling back to listen URL) and the provenance column says which
 * sources vouched for each row.
 */

/** Multiples of the heartbeat interval before a live peer reads as stale. */
const PEER_STALE_HEARTBEATS = 3;

/**
 * PeerRecord.discovered_via -> what to call it in front of a person.
 * The agent sets this at the point of discovery (mDNS listener, subnet
 * scanner, static-peer loader) and carries it forward across heartbeats, so
 * this column is provenance rather than "whatever spoke to us most recently".
 * An unrecognised value is printed verbatim: a newer agent may know a
 * mechanism this dashboard does not.
 */
const PEER_DISCOVERY_LABELS = {
  mdns: "mDNS",
  subnet_scan: "subnet scan",
  static: "pinned in config",
  join: "join ticket",
  heartbeat: "heartbeat",
};

/** Index in swarm.peers to focus after the next render ("+ Add by URL"). */
let peersFocusPinIndex = -1;

/**
 * `[{id, provider, model_count}]` from a heartbeat, defensively. Wire data:
 * a peer can send a string, nulls, or negative counts, and every caller below
 * reads fields off each entry.
 */
function peersProviderList(raw) {
  return asArray(raw)
    .filter((entry) => entry && typeof entry === "object")
    .map((entry) => ({
      id: String(entry.id || ""),
      provider: String(entry.provider || ""),
      count: Math.max(0, Number(entry.model_count) || 0),
    }))
    .filter((entry) => entry.id);
}

/** "ollama 12 · lmstudio 3", or "" when the peer reported no providers. */
function peersProviderSummary(providers) {
  if (!providers.length) return "";
  const labels = providers.map((p) => {
    const name = PROVIDER_LABELS[p.provider] || p.provider || p.id;
    return p.count ? `${name} ${p.count}` : name;
  });
  return labels.slice(0, 3).join(" · ") + (labels.length > 3 ? ` +${labels.length - 3}` : "");
}

function peersNormalizeUrl(url) {
  return String(url || "")
    .trim()
    .replace(/\/+$/, "");
}

/** The live swarm.peers draft array, created on demand so a defaults-only
 *  draft (admin API unreachable) can still be edited. */
function peersPinnedList() {
  if (!state.configDraft) return [];
  if (!state.configDraft.swarm) state.configDraft.swarm = {};
  if (!Array.isArray(state.configDraft.swarm.peers)) {
    state.configDraft.swarm.peers = [];
  }
  return state.configDraft.swarm.peers;
}

/**
 * Merge status.peers, state.lanPeers and swarm.peers into one roster.
 * Deduped by agent_id first, then by URL — a multi-homed host answers on
 * several addresses (`also_reachable_at`), and a pinned URL is usually the
 * same machine that is already heartbeating.
 */
function peersCollectRows() {
  const rows = [];
  const byId = new Map();
  const byUrl = new Map();

  function upsert(agentId, url) {
    const id = String(agentId || "");
    const norm = peersNormalizeUrl(url);
    let row = (id && byId.get(id)) || (norm && byUrl.get(norm)) || null;
    if (!row) {
      row = {
        agentId: id,
        hostname: "",
        url: norm,
        role: "",
        lastSeen: null,
        probedAt: null,
        sources: new Set(),
        self: false,
        alsoAt: [],
        draining: false,
        version: "",
        strategy: "",
        discoveredVia: "",
        providers: [],
        pinIndex: -1,
      };
      rows.push(row);
    }
    if (id && !row.agentId) row.agentId = id;
    if (id) byId.set(id, row);
    if (norm) {
      if (!row.url) row.url = norm;
      byUrl.set(norm, row);
    }
    return row;
  }

  /** Merge a wire-supplied URL list into row.alsoAt, keeping the index. */
  function addAlternates(row, urls) {
    asArray(urls)
      .map(peersNormalizeUrl)
      .filter((u) => u && u !== row.url)
      .forEach((u) => {
        if (!row.alsoAt.includes(u)) row.alsoAt.push(u);
        if (!byUrl.has(u)) byUrl.set(u, row);
      });
  }

  const status = state.status || null;
  if (status && (status.agent_id || status.listen_url || status.listen)) {
    // The registry does not necessarily hold a record for this agent, so the
    // "you" row is built from /status itself; anything that does mention the
    // same agent_id merges into it below.
    const selfRow = upsert(status.agent_id, status.listen_url || status.listen);
    selfRow.self = true;
    selfRow.hostname = status.hostname || selfRow.hostname;
    selfRow.role = status.role || selfRow.role;
    selfRow.version = status.version || "";
    selfRow.strategy = status.routing_strategy || "";
    selfRow.draining = !!status.draining;
    // status.providers / status.also_reachable_at are this agent's own half
    // of the heartbeat body, so the "you" row is described by exactly the
    // fields every peer sees about it.
    selfRow.providers = peersProviderList(status.providers);
    addAlternates(selfRow, status.also_reachable_at);
  }

  // status.peers / lanPeers are wire data: either can be wrong-typed or hold
  // null rows, and every line below reads fields off the row.
  const livePeers = asArray(status?.peers).filter(Boolean);
  const scannedPeers = asArray(state.lanPeers).filter(Boolean);

  livePeers.forEach((p) => {
    const row = upsert(p.agent_id, p.listen_url);
    row.sources.add("live");
    if (p.hostname) row.hostname = p.hostname;
    if (p.role) row.role = p.role;
    if (p.routing_strategy) row.strategy = p.routing_strategy;
    if (p.version) row.version = p.version;
    // last_seen is epoch seconds from PeerRecord.
    const seen = Number(p.last_seen);
    if (Number.isFinite(seen) && seen > 0) row.lastSeen = seen * 1000;
    // UI-4a: absent on a peer whose agent predates these keys — the row then
    // keeps its "" / [] defaults and the cells below fall back to "—".
    if (p.discovered_via) row.discoveredVia = String(p.discovered_via);
    const providers = peersProviderList(p.providers);
    if (providers.length) row.providers = providers;
    addAlternates(row, p.also_reachable_at);
  });

  scannedPeers.forEach((p) => {
    const row = upsert(p.agent_id, p.listen_url);
    row.sources.add("scan");
    if (p.self) row.self = true;
    if (p.hostname) row.hostname = p.hostname;
    if (p.role) row.role = p.role;
    if (p.routing_strategy) row.strategy = p.routing_strategy;
    if (p.version) row.version = p.version;
    if (p.draining) row.draining = true;
    // probed_at is epoch seconds stamped by the probe itself (UI-3), so a
    // scan-only row can be aged even though it never heartbeats.
    const probed = Number(p.probed_at);
    if (Number.isFinite(probed) && probed > 0) row.probedAt = probed * 1000;
    const providers = peersProviderList(p.providers);
    if (providers.length && !row.providers.length) row.providers = providers;
    addAlternates(row, [p.reported_listen_url, ...asArray(p.also_reachable_at)]);
  });

  peersPinnedList().forEach((url, index) => {
    const row = upsert("", url);
    row.sources.add("pinned");
    row.pinIndex = index;
  });

  // self, then live, then pinned-but-offline, then scan-only sightings.
  const rank = (r) => {
    if (r.self) return 0;
    if (r.sources.has("live")) return 1;
    if (r.sources.has("pinned")) return 2;
    return 3;
  };
  rows.sort((a, b) => {
    const d = rank(a) - rank(b);
    if (d) return d;
    return (a.hostname || a.agentId || a.url).localeCompare(
      b.hostname || b.agentId || b.url
    );
  });
  return rows;
}

/**
 * Share of routed requests per row, from status.routed_requests (keyed by
 * backend id — peers are `peer:<agent_id>`, everything else is served by this
 * agent). Cumulative since the agent started, not a live rate.
 */
function peersRoutedShares() {
  const routed = asObject(state.status?.routed_requests);
  let total = 0;
  let local = 0;
  Object.entries(routed).forEach(([id, value]) => {
    const count = Number(value) || 0;
    total += count;
    if (!String(id).startsWith("peer:")) local += count;
  });
  return { routed, total, local };
}

function peersShareCell(row, shares) {
  const cell = el("div");
  if (!shares.total) {
    cell.appendChild(textEl("div", "muted", "—"));
    return cell;
  }
  const count = row.self
    ? shares.local
    : Number(shares.routed[`peer:${row.agentId}`]);
  if (!Number.isFinite(count)) {
    cell.appendChild(textEl("div", "muted", "—"));
    return cell;
  }
  const pct = (count / shares.total) * 100;
  cell.appendChild(textEl("div", "mono", formatPercent(pct, 0)));
  const meter = el("div", "meter");
  const fill = el("span");
  // Width only — the colour comes from .meter > span in the token layer.
  fill.style.width = `${Math.max(0, Math.min(100, pct)).toFixed(1)}%`;
  meter.appendChild(fill);
  cell.appendChild(meter);
  return cell;
}

function peersStaleAfterMs() {
  const interval = Number(state.configDraft?.swarm?.heartbeat_interval_s);
  const base = Number.isFinite(interval) && interval > 0 ? interval : 10;
  return base * PEER_STALE_HEARTBEATS * 1000;
}

/** Dot kind + the one-word reason it is that kind. */
function peersHealth(row) {
  if (row.self) {
    return state.healthy
      ? { kind: "ok", label: "this machine" }
      : { kind: "error", label: "unreachable" };
  }
  if (row.draining) return { kind: "warn", label: "draining" };
  if (row.sources.has("live")) {
    const age = row.lastSeen ? Date.now() - row.lastSeen : null;
    if (age != null && age > peersStaleAfterMs()) {
      return { kind: "warn", label: "stale heartbeat" };
    }
    return { kind: "ok", label: "connected" };
  }
  if (row.sources.has("scan")) return { kind: "", label: "seen, not joined" };
  if (row.sources.has("pinned")) return { kind: "", label: "offline" };
  return { kind: "", label: "unknown" };
}

/**
 * How this agent learned about the row, in order of authority.
 *
 * For a connected peer the answer is `discovered_via` off status.peers[]:
 * the registry stamps it at the point of discovery and carries it forward
 * across re-registration, so a peer found over mDNS still reads "mDNS" after
 * it starts heartbeating. Rows with no live record fall back to what this
 * page itself knows — a URL in swarm.peers, or a sighting in the last scan.
 *
 * An agent that predates `discovered_via` sends nothing, and a live row then
 * reads "heartbeat" — the same honest answer as before, now only for the
 * peers it is actually true of.
 */
function peersProvenance(row) {
  const parts = [];
  if (row.self) parts.push("this machine");
  if (row.sources.has("live")) {
    const via = row.discoveredVia;
    parts.push(via ? PEER_DISCOVERY_LABELS[via] || via : "heartbeat");
  }
  if (row.sources.has("pinned") && row.discoveredVia !== "static") {
    parts.push("pinned");
  }
  if (row.sources.has("scan") && row.discoveredVia !== "subnet_scan") {
    parts.push("in last scan");
  }
  return parts.length ? parts.join(" · ") : "—";
}

function peersAgentCell(row, health) {
  const cell = el("div");
  const line = el("div", "row");
  line.appendChild(statusDot(health.kind));
  const name = row.hostname || row.agentId || (row.url ? row.url : "unnamed agent");
  line.appendChild(textEl("span", "", name));
  if (row.self) {
    line.appendChild(pill("ok", `you · ${row.role || "peer"}`));
  }
  if (row.draining) line.appendChild(pill("warn", "draining"));
  cell.appendChild(line);
  const sub = row.agentId && row.agentId !== row.hostname ? row.agentId : health.label;
  cell.appendChild(textEl("div", "muted", sub));
  // What the peer says it actually serves. Every peer is materialised locally
  // as one Backend with provider="custom", so before the heartbeat carried
  // this there was no way to tell an Ollama box from an oMLX one.
  const summary = peersProviderSummary(row.providers);
  if (summary) cell.appendChild(textEl("div", "field-help", summary));
  return cell;
}

function peersAddressCell(row) {
  const cell = el("div");
  if (row.pinIndex >= 0) {
    // Pinned rows stay editable in place — this is the swarm.peers editor the
    // old tab exposed as a separate string-list card.
    const input = el("input", "mono");
    input.type = "text";
    input.value = peersPinnedList()[row.pinIndex] || "";
    input.placeholder = "http://10.0.0.32:11400";
    input.setAttribute("aria-label", "Pinned peer URL");
    input.oninput = () => {
      peersPinnedList()[row.pinIndex] = input.value.trim();
      markDirty();
    };
    cell.appendChild(input);
    if (peersFocusPinIndex === row.pinIndex) {
      peersFocusPinIndex = -1;
      // Focus after the row is in the document.
      setTimeout(() => input.focus(), 0);
    }
  } else {
    cell.appendChild(textEl("div", "mono", row.url || "—"));
  }
  row.alsoAt.forEach((extra) => {
    cell.appendChild(textEl("div", "muted mono", `also at ${extra}`));
  });
  return cell;
}

function peersActionsCell(row) {
  const cell = el("div", "row");
  if (row.self) {
    // The design offers "Hand over role…" here; there is no such endpoint.
    cell.appendChild(textEl("span", "muted", "—"));
    return cell;
  }
  if (row.pinIndex >= 0) {
    cell.appendChild(
      button("Forget", "small secondary", () => {
        peersPinnedList().splice(row.pinIndex, 1);
        markDirty();
        render();
      })
    );
    return cell;
  }
  if (row.url) {
    cell.appendChild(
      button("Pin", "small secondary", () => {
        peersPinnedList().push(row.url);
        markDirty();
        render();
      })
    );
  }
  return cell;
}

function peersWarningsPanel(root) {
  // peer_warnings is what other agents said about themselves — the most
  // reachable wrong-typed field on this page.
  const warnings = asArray(state.status?.peer_warnings).filter(Boolean);
  if (!warnings.length) return;
  const body = panel(
    root,
    "Peer warnings",
    `${warnings.length} drift issue${warnings.length === 1 ? "" : "s"}`,
    "accent-warn"
  );
  warnings.forEach((text) => {
    const finding = el("div", "finding");
    finding.appendChild(statusDot("warn"));
    const detail = el("div", "finding-body");
    detail.appendChild(textEl("div", "finding-detail", text));
    finding.appendChild(detail);
    body.appendChild(finding);
  });
}

function peersMeshBar(root, rows) {
  const swarm = asObject(state.configDraft?.swarm);
  const interval = Number(swarm.heartbeat_interval_s);
  const bar = el("div", "inset row");
  const facts = [
    `Heartbeat every ${Number.isFinite(interval) && interval > 0 ? interval : "—"}s`,
    `mDNS ${swarm.mdns === false ? "off" : "on"}`,
    `subnet scan ${swarm.subnet_scan ? "on" : "off"}`,
    `cluster token ${state.status?.cluster_token_set ? "set" : "not set"}`,
  ];
  const summary = textEl("div", "muted", facts.join(" · "));
  bar.appendChild(summary);
  bar.appendChild(el("div", "spacer"));
  const scanned = rows.filter((r) => r.sources.has("scan")).length;
  // status.discovery.last_peer_scan_at is stamped by the scan itself, not by
  // whichever route triggered it, so a scan run by the rediscovery timer or
  // at startup counts here exactly like one run from the button above. It is
  // also process-wide, so it survives a reload of this page.
  const lastScan = Number(state.status?.discovery?.last_peer_scan_at);
  bar.appendChild(
    textEl(
      "div",
      "muted",
      Number.isFinite(lastScan) && lastScan > 0
        ? `LAN scanned ${timeAgo(lastScan * 1000)}, ${scanned} agent(s) in results`
        : "No LAN scan since this agent started"
    )
  );
  bar.appendChild(
    button("Network settings", "small secondary", () => navigate("network"))
  );
  root.appendChild(bar);
}

async function peersRunScan(save) {
  await runPeersScan(save);
  // No local timestamp to keep: the scan clock now comes back on /status.
  if (state.page === "peers") render();
}

function renderPeersPage(root) {
  const rows = peersCollectRows();
  const live = rows.filter((r) => !r.self && r.sources.has("live")).length;
  const seenOnly = rows.filter(
    (r) => !r.self && !r.sources.has("live") && r.sources.has("scan")
  ).length;
  const pinnedOffline = rows.filter(
    (r) => !r.self && !r.sources.has("live") && r.pinIndex >= 0
  ).length;

  const subParts = [`${live} connected`];
  if (seenOnly) subParts.push(`${seenOnly} seen but not joined`);
  if (pinnedOffline) subParts.push(`${pinnedOffline} pinned offline`);
  if (state.status?.role) subParts.push(`you are the ${state.status.role}`);

  const actions = el("div", "row");
  actions.appendChild(button("Scan LAN", "", () => peersRunScan(false)));
  actions.appendChild(
    button("Scan & save to config", "secondary", () => peersRunScan(true))
  );
  actions.appendChild(
    button("+ Add by URL", "secondary", () => {
      const list = peersPinnedList();
      list.push("");
      peersFocusPinIndex = list.length - 1;
      markDirty();
      render();
    })
  );

  pageHeader(root, "Peers", subParts.join(" · "), actions);
  peersWarningsPanel(root);

  const body = panel(
    root,
    "Mesh roster",
    "Connected agents, LAN scan results and pinned peers"
  );
  peersMeshBar(body, rows);

  if (!rows.length) {
    body.appendChild(
      textEl(
        "p",
        "empty",
        "No peers yet — scan the LAN, or add a peer URL to pin it."
      )
    );
    return;
  }

  const shares = peersRoutedShares();
  const template = "1.6fr 1.5fr .8fr 1.1fr .9fr .9fr auto";
  const table = dataTable(
    ["Agent", "Address", "Role", "Provenance", "Heartbeat", "Share", ""],
    template
  );
  rows.forEach((row) => {
    const health = peersHealth(row);
    let heartbeat;
    if (row.lastSeen) {
      heartbeat = textEl(
        "div",
        `mono ${health.kind === "warn" ? "text-warn" : "text-ok"}`,
        timeAgo(row.lastSeen)
      );
    } else if (row.probedAt) {
      // Scan-only row: it never heartbeats, but the probe that found it
      // stamped a wall clock, so the age is real rather than blank.
      heartbeat = el("div");
      heartbeat.appendChild(textEl("div", "mono muted", timeAgo(row.probedAt)));
      heartbeat.appendChild(textEl("div", "field-help", "probed, not joined"));
    } else {
      heartbeat = textEl("div", "muted", "—");
    }
    table.addRow([
      peersAgentCell(row, health),
      peersAddressCell(row),
      textEl("div", row.self ? "" : "muted", row.role || "peer"),
      textEl("div", "muted", peersProvenance(row)),
      heartbeat,
      peersShareCell(row, shares),
      peersActionsCell(row),
    ]);
  });
  table.addFoot(
    "Pinned peers stay in this list even when offline, so a machine that is " +
      "asleep does not silently disappear from the mesh. Provenance is how " +
      "this agent first learned of the peer, kept across heartbeats. Share " +
      "is the cumulative split of requests this agent has routed since it " +
      "started."
  );
  body.appendChild(table.table);
}

registerPage("peers", renderPeersPage);
