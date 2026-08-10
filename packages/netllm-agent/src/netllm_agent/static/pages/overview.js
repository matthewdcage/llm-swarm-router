/* Overview page — design 1a (dark radar-led) / 1b (light table-led variant of
 * the same data). Everything below is token-driven, so one implementation
 * reads correctly in both themes.
 *
 * This page merges the old Status tab (renderStatusTab) and Serving tab
 * (renderServingTab): role/mesh/backend counts and the router + oMLX counters
 * now live together, because "what is my mesh doing right now" was the one
 * question that needed both tabs open.
 */

/* ---------------- shared derivations ---------------- */

/* Mesh radar geometry.
 *
 * The radar used to be a fixed 454x400 box with node positions written as
 * percentages. Two things broke: in a panel ~800px wide the box left a large
 * dead margin either side, and because the positions scaled with the box
 * while the cards stayed a fixed 158px, any narrowing (or any node count
 * above a handful) put cards on top of each other. So the stage now takes the
 * width it is given and the layout is solved in real pixels against the
 * measured card sizes — see ovMeshLayout(). */
const OV_MESH_ASPECT = 0.64; // height / width
const OV_MESH_MIN_W = 420; // below this the radar cannot hold a card ring
const OV_MESH_MAX_W = 760;
const OV_MESH_PAD = 8; // clearance between a card edge and the stage edge
const OV_MESH_CARD_GAP = 14; // clearance between two cards
// Above this the radar stops being readable however it is arranged; the
// ledger says the same thing in a form that scales.
const OV_MESH_MAX_NODES = 10;
// Fraction of the outermost ring that share is allowed to pull a node inward
// when every node sits on one ring. Distance still encodes share; it just
// cannot pull a card into its neighbour.
const OV_MESH_SHARE_BAND = 0.26;
const OV_SVG_NS = "http://www.w3.org/2000/svg";

/** Torn down and rebuilt on every render(); without this the 5s poll would
 * leave one observer per render alive. */
let ovMeshObserver = null;

/** Reads a design token for the few places that need a real colour string
 * (SVG stroke attributes). Never a literal — see dashboard-tokens.css. */
function ovToken(name) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name);
  return value.trim() || "currentColor";
}

/* Every list this page walks comes off the wire, so it can arrive as the wrong
 * type or with null entries — `?? []` only defends against the field being
 * absent. asArray() normalises the container; .filter(Boolean) drops the null
 * rows so `b.enabled` cannot throw. Overview is the landing page, so a throw
 * here is the whole dashboard. */
function ovEnabledBackends() {
  return asArray(state.status?.backends)
    .filter(Boolean)
    .filter((b) => b.enabled !== false);
}

function ovPeerList() {
  return asArray(state.status?.peers).filter(Boolean);
}

/**
 * Per-backend routed request counts. `routed_requests` is keyed by backend id
 * (pool.route_to -> pool.routed_counts), and peer rows carry the id
 * `peer:<agent_id>` (swarm.peer_agent_backends), which is what makes a real
 * per-peer share of traffic derivable here without inventing anything.
 *
 * These particular counters are cumulative since the agent started. For the
 * design's windowed figures ("last 5 min", "share of traffic") read
 * ovWindowCounts() instead — router.windows carries a real ledger now.
 */
function ovRoutedCounts() {
  const counts = asObject(
    state.status?.routed_requests || state.telemetry?.router?.routed_requests
  );
  const byId = new Map();
  let total = 0;
  Object.entries(counts).forEach(([id, value]) => {
    const n = Number(value);
    if (!Number.isFinite(n) || n <= 0) return;
    byId.set(id, n);
    total += n;
  });
  return { byId, total };
}

/** Backend row that carries a peer's traffic, or undefined when the peer is
 * draining / not LAN-routable (peer_agent_backends drops those). */
function ovBackendForPeer(peer) {
  return ovEnabledBackends().find(
    (b) => b.local === false && b.agent_id && b.agent_id === peer.agent_id
  );
}

function ovPeerLabel(peer) {
  return peer.hostname || peer.agent_id || peer.listen_url || "peer";
}

/** Host:port of a URL, falling back to the raw string for unparseable input. */
function ovHostOf(url) {
  if (!url) return "";
  try {
    return new URL(url).host;
  } catch {
    return String(url).replace(/^https?:\/\//, "").replace(/\/.*$/, "");
  }
}

/**
 * One row per discovered peer, joined against the backend rows so health and
 * model counts are real rather than assumed. `share` is null when nothing has
 * been routed yet (rendered as an em-dash, never as 0% — those differ).
 */
function ovPeerRows() {
  const { byId, total } = ovRoutedCounts();
  return ovPeerList().map((peer) => {
    const backend = ovBackendForPeer(peer);
    const status = backend?.health?.status;
    const requests = backend ? byId.get(backend.id) || 0 : 0;
    let kind = "warn";
    let statusText = "not routable";
    if (backend) {
      if (status === "online") {
        kind = "ok";
        statusText = "online";
      } else if (status === "offline") {
        kind = "error";
        statusText = "offline";
      } else {
        statusText = status || "unknown";
      }
    }
    return {
      peer,
      backend,
      name: ovPeerLabel(peer),
      role: peer.role || "peer",
      models: backend?.health?.model_count ?? asArray(backend?.health?.models).length,
      kind,
      statusText,
      requests,
      share: total > 0 ? requests / total : null,
    };
  });
}

/** Share of routed requests served by this node's own backends. */
function ovLocalShare() {
  const { byId, total } = ovRoutedCounts();
  if (!total) return null;
  let local = 0;
  ovEnabledBackends().forEach((b) => {
    if (b.local === false) return;
    local += byId.get(b.id) || 0;
  });
  return local / total;
}

function ovShareText(share) {
  return share == null ? "—" : formatPercent(share * 100);
}

function ovInfoRow(parent, label, value) {
  const row = el("div", "row-between");
  const shown = value === undefined || value === null || value === "" ? "—" : String(value);
  row.append(textEl("span", "muted", label), textEl("span", "mono", shown));
  parent.appendChild(row);
  return row;
}

function ovLegend(items) {
  const legend = el("div", "legend");
  items.forEach(({ swatch, text }) => {
    const entry = el("span");
    entry.append(el("span", swatch), document.createTextNode(text));
    legend.appendChild(entry);
  });
  return legend;
}

/* ---------------- 1. role banner ---------------- */

function ovRoleSentence(peerRows) {
  const status = state.status;
  if (!state.healthy || !status) {
    return "The dashboard cannot reach the local agent. Start it (netllm serve) and press Refresh.";
  }
  const n = peerRows.length;
  const coordinator = status.role === "gateway";
  if (!n) {
    return coordinator
      ? "No peers discovered yet — every request is served by this node's own backends."
      : "No peers discovered yet — this node serves only the clients pointed at it.";
  }
  const online = peerRows.filter((r) => r.kind === "ok").map((r) => r.name);
  const idle = peerRows.filter((r) => r.kind !== "ok").map((r) => r.name);
  const parts = [
    coordinator
      ? `${n} peer${n === 1 ? "" : "s"} can route through this node.`
      : `This node advertises to ${n} peer${n === 1 ? "" : "s"} in the mesh.`,
  ];
  if (online.length) parts.push(`${online.join(", ")} reachable.`);
  if (idle.length) parts.push(`${idle.join(", ")} not taking work.`);
  if (status.draining) parts.push("Draining — no new requests are accepted here.");
  return parts.join(" ");
}

function ovRenderRoleBanner(root, peerRows) {
  const status = state.status;
  const unreachable = !state.healthy || !status;
  const modifier = unreachable
    ? "accent-danger"
    : status.draining
      ? "accent-warn"
      : "accent-ok";
  const body = panel(root, null, null, modifier);

  const row = el("div", "row");
  const text = el("div");
  text.style.flex = "1";
  text.style.minWidth = "0";
  const headline = unreachable
    ? "Agent unreachable"
    : status.role === "gateway"
      ? "You are the coordinator"
      : "You are a follower";
  text.append(
    textEl("div", "panel-title", headline),
    textEl("div", "panel-desc", ovRoleSentence(peerRows))
  );
  row.appendChild(text);

  const stats = el("div", "stat-row");
  const roleStat = statBlock("Role", status?.role || "—");
  const strategyStat = statBlock("Strategy", status?.routing_strategy || "—");
  strategyStat.querySelector(".stat-value").classList.add("mono");
  const backends = ovEnabledBackends();
  const online = backends.filter((b) => b.health?.status === "online").length;
  stats.append(
    roleStat,
    strategyStat,
    statBlock("Backends", backends.length ? `${online}/${backends.length}` : "—")
  );
  row.appendChild(stats);

  // The role itself is a config field owned by the Network page (agent.role);
  // the design's "Hand over role…" affordance is a jump there, not a new API.
  row.appendChild(button("Change role…", "secondary", () => navigate("network")));
  body.appendChild(row);
}

/* ---------------- 2. mesh radar ---------------- */

/**
 * Node cards carry a name, a status dot and a share — nothing else. Every
 * word that used to be repeated on every card (`peer ·`, `online`, "of
 * requests served here") said the same thing N times; it lives in the key
 * below the stage now, once.
 */
function ovMeshNodeSelf() {
  const node = el("div", "mesh-node self");
  const status = state.status;
  node.appendChild(textEl("div", "mesh-self-role", status?.role || "this node"));
  node.appendChild(textEl("div", "mesh-node-name", status?.hostname || "this node"));
  node.appendChild(textEl("div", "mesh-node-share", ovShareText(ovLocalShare())));
  return node;
}

function ovMeshNodePeer(row) {
  const node = el("div", row.kind === "ok" ? "mesh-node" : "mesh-node idle");
  const head = el("div", "row");
  // The dot is the status. Naming it as well ("online") on every card was the
  // bulk of the text on the diagram; the key says what the dot means.
  const dot = statusDot(row.kind);
  dot.setAttribute("title", row.statusText);
  head.append(textEl("div", "mesh-node-name", row.name), el("div", "spacer"), dot);
  node.appendChild(head);
  node.appendChild(textEl("div", "mesh-node-share", ovShareText(row.share)));
  // An empty meter is noise. It appears only once there is a share to draw.
  if (row.share != null && row.share > 0) {
    const meter = el("div", "meter");
    const fill = el("span", row.kind === "ok" ? "" : "warn");
    fill.style.width = `${Math.max(row.share * 100, 4).toFixed(1)}%`;
    meter.appendChild(fill);
    node.appendChild(meter);
  }
  return node;
}

/**
 * Solves node placement for a stage of `geom.w` x `geom.h` pixels.
 *
 * Cards are laid out on concentric elliptical rings centred on the self card.
 * Ring radii run from the smallest that clears the self card out to the
 * largest that keeps a card inside the stage, so distance still encodes
 * share: the busiest nodes are on the innermost ring, and on a single ring
 * share pulls a node inward within OV_MESH_SHARE_BAND of the radius.
 *
 * Candidate arrangements (ring count x how the ranked nodes split across the
 * rings x a rotation offset) are generated in a fixed order and the first one
 * where no two cards — and no card and the self card — overlap wins. Returns
 * null when nothing fits, which is the caller's signal to show the ledger
 * instead of drawing a tangle.
 *
 * `shares` is one entry per node, already ranked busiest-first by the caller;
 * a null share means "nothing routed yet", not zero.
 */
function ovMeshLayout(shares, geom) {
  const n = shares.length;
  if (n > OV_MESH_MAX_NODES) return null;

  const halfW = geom.cardW / 2;
  const halfH = geom.cardH / 2;
  // Largest ellipse that still keeps a whole card inside the stage.
  const axMax = geom.w / 2 - halfW - OV_MESH_PAD;
  const ayMax = geom.h / 2 - halfH - OV_MESH_PAD;
  // Smallest one that clears the self card. Cards are rectangles, so this is
  // a keep-out box, not a circle — a point on an ellipse through its corners
  // still lands on top of the self card at intermediate angles, which is why
  // every position is pushed out of the box below.
  const axMin = halfW + geom.selfW / 2 + OV_MESH_CARD_GAP;
  const ayMin = halfH + geom.selfH / 2 + OV_MESH_CARD_GAP;
  if (axMax < axMin || ayMax < ayMin) return null;
  const band = { axMin, ayMin, axMax, ayMax };
  if (!n) return { points: [], band };

  const clearOfSelf = (p) => {
    const norm = Math.max(Math.abs(p.x) / axMin, Math.abs(p.y) / ayMin);
    if (norm >= 1 || norm <= 0) return p;
    return { x: p.x / norm, y: p.y / norm };
  };
  const clear = (a, b, minDX, minDY) =>
    Math.abs(a.x - b.x) >= minDX - 0.5 || Math.abs(a.y - b.y) >= minDY - 0.5;

  const maxShare = shares.reduce((m, s) => Math.max(m, s || 0), 0);
  // Ring sizes: outer rings hold more because they have more circumference.
  const splits = (rings) => {
    if (rings === 1) return [[n]];
    const out = [];
    for (let inner = 1; inner < n; inner += 1) {
      if (rings === 2) {
        out.push([inner, n - inner]);
      } else {
        for (let mid = 1; mid < n - inner; mid += 1) {
          out.push([inner, mid, n - inner - mid]);
        }
      }
    }
    // Prefer arrangements whose rings grow outward.
    return out.filter((s) => s.every((c, i) => i === 0 || c >= s[i - 1]));
  };

  for (const rings of [1, 2, 3]) {
    if (rings > n) break;
    for (const split of splits(rings)) {
      const geometry = split.map((count, ring) => {
        const t = rings === 1 ? 1 : ring / (rings - 1);
        return {
          count,
          ax: axMin + t * (axMax - axMin),
          ay: ayMin + t * (ayMax - ayMin),
        };
      });
      for (const rotation of [0, 0.5, 0.25]) {
        const points = [];
        let cursor = 0;
        geometry.forEach(({ count, ax, ay }, ring) => {
          const step = (2 * Math.PI) / count;
          // Start on the horizontal so N=2 and N=3 use the stage's width
          // rather than stacking above and below the centre card.
          const offset = ring * step * rotation;
          for (let i = 0; i < count; i += 1) {
            const share = shares[cursor];
            // On a single ring, share still moves the card: busiest inward.
            const pull =
              rings === 1 && maxShare > 0 && share != null
                ? 1 - (share / maxShare) * OV_MESH_SHARE_BAND
                : 1;
            const angle = offset + i * step;
            points.push(
              clearOfSelf({
                x: ax * pull * Math.cos(angle),
                y: ay * pull * Math.sin(angle),
              })
            );
            cursor += 1;
          }
        });

        const minDX = geom.cardW + OV_MESH_CARD_GAP;
        const minDY = geom.cardH + OV_MESH_CARD_GAP;
        const inside = points.every(
          (p) => Math.abs(p.x) <= axMax + 0.5 && Math.abs(p.y) <= ayMax + 0.5
        );
        if (!inside) continue;
        let ok = true;
        for (let i = 0; ok && i < points.length; i += 1) {
          for (let j = i + 1; j < points.length; j += 1) {
            if (!clear(points[i], points[j], minDX, minDY)) {
              ok = false;
              break;
            }
          }
        }
        if (ok) return { points, band };
      }
    }
  }
  return null;
}

/**
 * Rings and links. Both are drawn in the SVG so they scale with the stage:
 * the rings used to be fixed-pixel bordered divs that a resized stage clipped
 * mid-arc. Colour comes from the class (currentColor) so both themes follow.
 */
function ovMeshCanvas(w, h, band, rows, points) {
  const svg = document.createElementNS(OV_SVG_NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svg.setAttribute("aria-hidden", "true");
  const cx = w / 2;
  const cy = h / 2;

  // Three rings spanning the band a node can occupy, so the backdrop is the
  // scale the cards are placed against and always sits wholly inside the
  // stage — the old fixed-pixel bordered divs were clipped mid-arc whenever
  // the stage was narrower than they were.
  const rings = [0, 0.5, 1].map((t) => ({
    ax: band.axMin + t * (band.axMax - band.axMin),
    ay: band.ayMin + t * (band.ayMax - band.ayMin),
  }));
  rings.forEach(({ ax, ay }) => {
    const ring = document.createElementNS(OV_SVG_NS, "ellipse");
    ring.setAttribute("cx", cx.toFixed(1));
    ring.setAttribute("cy", cy.toFixed(1));
    ring.setAttribute("rx", ax.toFixed(1));
    ring.setAttribute("ry", ay.toFixed(1));
    ring.setAttribute("class", "mesh-ring-line");
    svg.appendChild(ring);
  });

  const maxShare = rows.reduce((m, r) => Math.max(m, r.share || 0), 0);
  rows.forEach((row, i) => {
    const p = points[i];
    if (!p) return;
    const line = document.createElementNS(OV_SVG_NS, "line");
    line.setAttribute("x1", cx.toFixed(1));
    line.setAttribute("y1", cy.toFixed(1));
    line.setAttribute("x2", (cx + p.x).toFixed(1));
    line.setAttribute("y2", (cy + p.y).toFixed(1));
    const cls =
      row.kind === "ok" ? "text-ok" : row.kind === "error" ? "muted" : "text-warn";
    line.setAttribute("class", `mesh-link ${cls}`);
    const weight =
      maxShare > 0 && row.share != null ? 1.5 + (row.share / maxShare) * 4 : 1.5;
    line.setAttribute("stroke-width", weight.toFixed(1));
    svg.appendChild(line);
  });
  return svg;
}

/** One key for the whole diagram, instead of the same words on every card. */
function ovMeshKey(routed) {
  const key = ovLegend([
    { swatch: "swatch dot", text: "reachable" },
    { swatch: "swatch live", text: "link = peer route, thicker = larger share" },
    {
      swatch: "swatch ring",
      text: routed
        ? "closer to the centre = larger share of routed requests"
        : "nothing routed yet — nodes are placed evenly",
    },
  ]);
  key.classList.add("mesh-key");
  return key;
}

/**
 * The table-led variant of the same data (design 1b). Used whenever the radar
 * cannot be drawn without cards colliding — a narrow panel, or more nodes
 * than a readable diagram holds.
 */
function ovMeshLedger(rows) {
  const wrap = el("div", "mesh-ledger");
  const t = dataTable(["Node", "Status", "Share"], "minmax(0, 1.6fr) minmax(0, 1fr) 68px");
  const selfName = el("div", "row");
  selfName.append(
    statusDot("ok"),
    textEl("span", "", state.status?.hostname || "this node")
  );
  t.addRow([
    selfName,
    textEl("div", "muted", `this node · ${state.status?.role || "unknown"}`),
    textEl("div", "mono", ovShareText(ovLocalShare())),
  ]);
  rows.forEach((row) => {
    const name = el("div", "row");
    name.append(statusDot(row.kind), textEl("span", "", row.name));
    const statusCls =
      row.kind === "ok" ? "text-ok" : row.kind === "error" ? "text-danger" : "text-warn";
    t.addRow([
      name,
      textEl("div", statusCls, row.statusText),
      textEl("div", "mono", ovShareText(row.share)),
    ]);
  });
  wrap.appendChild(t.table);
  return wrap;
}

/**
 * Sizes the stage to the panel it was given, solves the layout against the
 * cards' measured sizes and shows either the radar or the ledger. Re-run on
 * resize, which is also how it recovers when the page was hidden (and every
 * measurement therefore zero) at first render.
 */
function ovMeshApply(ctx) {
  const { stage, mesh, ledger, rows, nodes, self } = ctx;
  const available = stage.clientWidth;
  if (available <= 0) return; // page hidden; the observer fires when shown

  mesh.hidden = false;
  ledger.hidden = true;

  const w = Math.min(Math.max(available, 0), OV_MESH_MAX_W);
  const h = Math.round(w * OV_MESH_ASPECT);
  mesh.style.width = `${w}px`;
  mesh.style.height = `${h}px`;

  const selfRect = self.getBoundingClientRect();
  const cardRect = nodes.length ? nodes[0].getBoundingClientRect() : selfRect;
  const geom = {
    w,
    h,
    cardW: cardRect.width,
    cardH: cardRect.height,
    selfW: selfRect.width,
    selfH: selfRect.height,
  };

  const solved =
    w < OV_MESH_MIN_W || !geom.cardW || !geom.selfW
      ? null
      : ovMeshLayout(rows.map((r) => r.share), geom);

  if (!solved) {
    mesh.hidden = true;
    ledger.hidden = false;
    return;
  }

  const { points, band } = solved;
  nodes.forEach((node, i) => {
    node.style.left = `${(w / 2 + points[i].x).toFixed(1)}px`;
    node.style.top = `${(h / 2 + points[i].y).toFixed(1)}px`;
  });
  self.style.left = `${(w / 2).toFixed(1)}px`;
  self.style.top = `${(h / 2).toFixed(1)}px`;

  const oldCanvas = mesh.querySelector("svg");
  const canvas = ovMeshCanvas(w, h, band, rows, points);
  if (oldCanvas) {
    mesh.replaceChild(canvas, oldCanvas);
  } else {
    mesh.insertBefore(canvas, mesh.firstChild);
  }
}

function ovRenderMesh(root, peerRows) {
  const body = panel(root, "Mesh");

  if (!state.status) {
    body.appendChild(textEl("p", "empty", "Agent unreachable — mesh unknown."));
    return;
  }

  // Busiest first, so the ring split puts the heaviest traffic innermost.
  const rows = peerRows
    .slice()
    .sort((a, b) => (b.share || 0) - (a.share || 0));
  const routed = rows.some((r) => r.share != null && r.share > 0);

  const stage = el("div", "mesh-stage");
  const mesh = el("div", "mesh");
  const self = ovMeshNodeSelf();
  const nodes = rows.map((row) => ovMeshNodePeer(row));
  mesh.appendChild(self);
  nodes.forEach((n) => mesh.appendChild(n));
  const ledger = ovMeshLedger(rows);
  ledger.hidden = true;
  stage.append(mesh, ledger);
  body.appendChild(stage);

  if (!rows.length) {
    body.appendChild(
      textEl("p", "empty", "No peers discovered — this node is the whole mesh.")
    );
  } else {
    body.appendChild(ovMeshKey(routed));
  }

  const ctx = { stage, mesh, ledger, rows, nodes, self };
  let lastWidth = -1;
  const apply = () => {
    const w = stage.clientWidth;
    if (w === lastWidth) return;
    lastWidth = w;
    ovMeshApply(ctx);
  };
  apply();
  if (ovMeshObserver) ovMeshObserver.disconnect();
  if ("ResizeObserver" in window) {
    ovMeshObserver = new ResizeObserver(apply);
    ovMeshObserver.observe(stage);
  }
}

/* ---------------- 3. throughput ---------------- */

/**
 * Windowed per-backend request counts from `router.windows` (UI-1).
 *
 * Spans are *server-declared*: read the keys that are present, never assume
 * 60/300/86400, and never sum buckets client-side (same rule as
 * `total_tokens`). Returns null when the agent predates the ledger, so the
 * caller can fall back or render "—" rather than a fabricated zero.
 */
function ovWindowCounts(preferredSpan) {
  const windows = asObject(state.telemetry?.router?.windows);
  const spans = asArray(windows.spans_s)
    .map(Number)
    .filter((n) => Number.isFinite(n) && n > 0);
  if (!spans.length) return null;
  const span = spans.includes(preferredSpan) ? preferredSpan : spans[0];
  const byId = new Map();
  let total = 0;
  Object.entries(asObject(windows.by_backend)).forEach(([id, buckets]) => {
    const n = Number(asObject(buckets)[String(span)]);
    if (!Number.isFinite(n) || n <= 0) return;
    byId.set(id, n);
    total += n;
  });
  return { byId, total, span };
}

/** "5 min" / "24 h" / "60 s" for a span the server declared. */
function ovSpanLabel(span) {
  if (span >= 86400) return `${Math.round(span / 86400)} d`;
  if (span >= 3600) return `${Math.round(span / 3600)} h`;
  if (span >= 60) return `${Math.round(span / 60)} min`;
  return `${span} s`;
}

/** Scope-block rates are nullable now: null means "never measured", which is
 * a different statement from 0 tok/s and must not render as one. */
function ovRateText(value) {
  return value == null ? "—" : formatTps(value);
}

function ovRouterScopeTable(router) {
  const session = router.session;
  const alltime = router.alltime;
  if (!session && !alltime) return null;
  const { table, addRow, addFoot } = dataTable(
    ["Router counter", "Session", "All-time"],
    "1.6fr 0.7fr 0.7fr"
  );
  // docs/telemetry-api.md is normative: the router emits every key of a scope
  // block, total_tokens included — do not re-derive one client-side, a missing
  // key is a server bug the telemetry contract test exists to catch.
  const rows = [
    ["Requests", "requests", formatCompactCount],
    ["Prompt tokens", "prompt_tokens", formatCompactCount],
    ["Completion tokens", "completion_tokens", formatCompactCount],
    ["Total tokens", "total_tokens", formatCompactCount],
    // Nullable since UI-2: these are prompt/completion tokens over *measured*
    // prefill and generation seconds, and a deployment that never streams has
    // no measurement to report. They used to be total latency multiplied by a
    // hardcoded 0.3 / 0.7.
    ["Avg prefill (tok/s)", "avg_prefill_tps", ovRateText],
    ["Avg generation (tok/s)", "avg_generation_tps", ovRateText],
  ];
  rows.forEach(([label, key, format]) => {
    addRow([
      label,
      textEl("div", "mono", session ? format(session[key]) : "—"),
      textEl("div", "mono", alltime ? format(alltime[key]) : "—"),
    ]);
  });
  addFoot(
    `In-flight ${router.in_flight_total ?? 0} · shardless fallbacks ${
      router.shardless_fallbacks ?? 0
    }`
  );
  return table;
}

function ovRenderThroughput(root) {
  const telemetry = state.telemetry;
  const router = asObject(telemetry?.router);
  const history = asObject(telemetry?.history);
  const rps = asArray(history.router_rps).filter((v) => Number.isFinite(Number(v)));
  const live = asObject(router.live);
  const latency = asObject(router.latency);

  // Spill share comes off the windowed ledger, so this answers "what fraction
  // of recent traffic left this node" rather than "…since the agent started".
  // Falls back to the cumulative counters when talking to an older agent.
  const windowed = ovWindowCounts(300);
  const counts = windowed || ovRoutedCounts();
  let spilled = null;
  if (counts.total > 0) {
    let remote = 0;
    ovEnabledBackends().forEach((b) => {
      if (b.local === false) remote += counts.byId.get(b.id) || 0;
    });
    spilled = (remote / counts.total) * 100;
  }

  const liveWindow = Number(live.window_s);
  const note = windowed
    ? `${ovSpanLabel(windowed.span)} window · live over ${
        Number.isFinite(liveWindow) ? liveWindow : 10
      }s`
    : "since agent start";
  const body = panel(root, "Throughput", note);
  if (!telemetry) {
    body.appendChild(textEl("p", "empty", "Telemetry unavailable — agent not reachable."));
    return;
  }

  // Every stat below is null-aware on purpose: "not measured" and "zero" are
  // different facts, and the whole point of UI-2 is to stop rendering the
  // first one as the second.
  const requestsPerS = Number(live.requests_per_s);
  const ttftP50 = latency.ttft_p50_ms;
  const ttftSamples = Number(latency.ttft_samples) || 0;
  const stats = el("div", "stat-row");
  stats.append(
    statBlock(
      "req/s",
      Number.isFinite(requestsPerS)
        ? formatTps(requestsPerS)
        : rps.length
          ? formatTps(rps[rps.length - 1])
          : "—"
    ),
    statBlock("gen tok/s", ovRateText(live.generation_tps)),
    statBlock("p50 ttft", ttftP50 == null ? "—" : `${Math.round(ttftP50)}`, ttftP50 == null ? null : "ms"),
    statBlock("spilled", spilled == null ? "—" : formatPercent(spilled), null, "accent")
  );
  body.appendChild(stats);
  if (ttftP50 == null) {
    // Not a failure state: non-streaming requests have no observable TTFT, so
    // say why the cell is empty instead of leaving an unexplained dash.
    body.appendChild(
      textEl("p", "panel-note", "No streamed requests yet — TTFT is only observable on a stream.")
    );
  } else {
    body.appendChild(
      textEl(
        "p",
        "panel-note",
        `p50 TTFT over ${ttftSamples} streamed request${ttftSamples === 1 ? "" : "s"}${
          latency.ttft_p95_ms == null ? "" : ` · p95 ${Math.round(latency.ttft_p95_ms)} ms`
        }`
      )
    );
  }

  if (rps.length > 1) {
    body.appendChild(sparklineSvg(rps, ovToken("--accent")));
    body.appendChild(
      ovLegend([{ swatch: "swatch live", text: "requests routed by this node" }])
    );
  } else {
    body.appendChild(
      textEl("p", "empty", "No request-rate history yet — send a chat request.")
    );
  }

  const table = ovRouterScopeTable(router);
  if (table) {
    body.appendChild(table);
  } else {
    body.appendChild(
      textEl(
        "p",
        "empty",
        "No data yet — routed chat/embeddings increment these counters."
      )
    );
  }
}

/* ---------------- 4. pools ---------------- */

const OV_POOL_LABELS = {
  chat: "Chat",
  embedding: "Embeddings",
  audio: "Audio",
  rerank: "Rerank",
  other: "Other",
};

/**
 * There is no server-side notion of a "pool": models are classified by
 * capability (netllm_core.capabilities.model_capability, surfaced on
 * /v1/models). Grouping by capability is the closest honest equivalent, and
 * the per-node split is by how many of that pool's models each node serves —
 * not by traffic, which is only counted per backend, not per model.
 */
function ovBuildPools() {
  // state.models is /v1/models `data` verbatim; a proxy can make that a string
  // or fill it with nulls.
  const models = asArray(state.models).filter(Boolean);
  const capById = new Map(models.map((m) => [m.id, m.capability || "chat"]));
  const pools = new Map();
  const peerNames = new Map();
  ovPeerList().forEach((p) => peerNames.set(p.agent_id, ovPeerLabel(p)));
  const selfName = state.status?.hostname || "this node";

  ovEnabledBackends().forEach((backend) => {
    const isSelf = backend.local !== false;
    const label = isSelf
      ? selfName
      : peerNames.get(backend.agent_id) ||
        backend.cloud_provider ||
        ovHostOf(backend.base_url) ||
        backend.id;
    asArray(backend.health?.models).forEach((mid) => {
      const cap = capById.get(mid) || "chat";
      if (!pools.has(cap)) pools.set(cap, { ids: new Set(), nodes: new Map() });
      const pool = pools.get(cap);
      pool.ids.add(mid);
      const node = pool.nodes.get(label) || { count: 0, isSelf, ok: backend.health?.status === "online" };
      node.count += 1;
      pool.nodes.set(label, node);
    });
  });

  // Catalog-only entries (aliases, models on a backend that is momentarily
  // unhealthy) still belong to a pool — they just have no node behind them.
  models.forEach((m) => {
    const cap = m.capability || "chat";
    if (!pools.has(cap)) pools.set(cap, { ids: new Set(), nodes: new Map() });
    pools.get(cap).ids.add(m.id);
  });

  return [...pools.entries()]
    .map(([cap, pool]) => ({
      cap,
      label: OV_POOL_LABELS[cap] || cap,
      models: pool.ids.size,
      nodes: [...pool.nodes.entries()]
        .map(([name, v]) => ({ name, ...v }))
        .sort((a, b) => b.count - a.count),
    }))
    .sort((a, b) => b.models - a.models);
}

function ovRenderPools(root) {
  const pools = ovBuildPools();
  const totalModels = pools.reduce((n, p) => n + p.models, 0);
  const note = pools.length
    ? `${pools.length} pool${pools.length === 1 ? "" : "s"} · ${totalModels} routed model${
        totalModels === 1 ? "" : "s"
      }`
    : "";
  const body = panel(root, "Pools", note);
  if (!pools.length) {
    body.appendChild(
      textEl("p", "empty", "No models routed yet — run Discover to find backends.")
    );
    return;
  }

  pools.forEach((pool) => {
    const card = el("div", "inset");
    const head = el("div", "row-between");
    const title = el("div", "mesh-node-name");
    title.append(
      document.createTextNode(pool.label),
      textEl(
        "span",
        "muted mono",
        ` · ${pool.models} model${pool.models === 1 ? "" : "s"}`
      )
    );
    const ready = pool.nodes.filter((n) => n.ok).length;
    head.append(
      title,
      textEl(
        "div",
        ready ? "text-ok" : "muted",
        `${ready} node${ready === 1 ? "" : "s"} ready`
      )
    );
    card.appendChild(head);

    const served = pool.nodes.reduce((n, node) => n + node.count, 0);
    if (!served) {
      card.appendChild(textEl("div", "muted", "no node currently serves this pool"));
      body.appendChild(card);
      return;
    }
    const bar = el("div", "share-bar");
    pool.nodes.forEach((node) => {
      const seg = el("span", node.isSelf ? "ok" : node.ok ? "" : "dim");
      seg.style.flex = String(node.count);
      bar.appendChild(seg);
    });
    card.appendChild(bar);
    card.appendChild(
      textEl(
        "div",
        "muted",
        pool.nodes
          .map((n) => `${n.name} ${formatPercent((n.count / served) * 100)}`)
          .join(" · ")
      )
    );
    body.appendChild(card);
  });
}

/* ---------------- 5. warnings / doctor strip ---------------- */

function ovRenderWarnings(root) {
  // peer_warnings is assembled from what *other* agents report about themselves,
  // so it is the most reachable wrong-typed field in production.
  const warnings = asArray(state.status?.peer_warnings).filter(Boolean);
  const issues = asArray(state.doctor?.issues).filter(Boolean);
  const doctorError = state.doctor?.error;
  const kind = warnings.length || issues.length || doctorError ? "accent-warn" : "accent-ok";
  const body = panel(root, null, null, kind);

  const row = el("div", "row");
  const list = el("div");
  list.style.flex = "1";
  list.style.minWidth = "0";

  if (!warnings.length && !issues.length && !doctorError) {
    list.append(
      textEl("div", "finding-title", "Mesh healthy"),
      textEl("div", "finding-detail", "No peer warnings, and doctor reports no issues.")
    );
  } else {
    // Peer config/version drift first — it is the one thing that silently
    // makes two machines disagree about routing.
    warnings.slice(0, 3).forEach((line) => {
      const finding = el("div", "finding");
      const bodyEl = el("div", "finding-body");
      bodyEl.append(
        textEl("div", "finding-title", "Peer configuration drift"),
        textEl("div", "finding-detail", line)
      );
      finding.append(statusDot("warn"), bodyEl);
      list.appendChild(finding);
    });
    if (warnings.length > 3) {
      list.appendChild(
        textEl("div", "finding-detail", `+${warnings.length - 3} more peer warnings`)
      );
    }
    if (issues.length) {
      // Doctor issues are {title, fix} objects (admin.doctor_payload); tolerate
      // a bare string from an older agent rather than rendering "undefined".
      const first = issues[0];
      const detail =
        typeof first === "string" ? first : first?.title || first?.fix || "";
      const finding = el("div", "finding");
      const bodyEl = el("div", "finding-body");
      bodyEl.append(
        textEl(
          "div",
          "finding-title",
          `Doctor has ${issues.length} open suggestion${issues.length === 1 ? "" : "s"}`
        ),
        textEl("div", "finding-detail", detail)
      );
      finding.append(statusDot("warn"), bodyEl);
      list.appendChild(finding);
    }
    if (doctorError) {
      const finding = el("div", "finding");
      const bodyEl = el("div", "finding-body");
      bodyEl.append(
        textEl("div", "finding-title", "Doctor unavailable"),
        textEl("div", "finding-detail", doctorError)
      );
      finding.append(statusDot("error"), bodyEl);
      list.appendChild(finding);
    }
  }

  row.append(list, button("Open doctor", "secondary", () => navigate("doctor")));
  body.appendChild(row);
}

/* ---------------- ported counters (old Serving tab) ---------------- */

function ovKeyValueBlock(parent, label, entries) {
  const keys = Object.keys(asObject(entries));
  if (!keys.length) return false;
  const card = el("div", "inset");
  card.appendChild(textEl("div", "field-label", label));
  keys
    .sort((a, b) => Number(entries[b]) - Number(entries[a]))
    .slice(0, 16)
    .forEach((key) => ovInfoRow(card, key, String(entries[key])));
  parent.appendChild(card);
  return true;
}

function ovRenderOmlx(root) {
  const omlx = state.telemetry?.omlx;
  if (!omlx?.available) return;
  const history = asObject(state.telemetry?.history);
  const live = asObject(omlx.live);
  const body = panel(root, "oMLX serving", omlx.primary_model || null);

  const stats = el("div", "stat-row");
  stats.append(
    statBlock("PP tok/s", formatTps(live.prefill_tps)),
    statBlock("TG tok/s", formatTps(live.generation_tps))
  );
  body.appendChild(stats);
  const ppSeries = asArray(history.omlx_pp_tps);
  const tgSeries = asArray(history.omlx_tg_tps);
  if (ppSeries.length > 1) {
    body.appendChild(sparklineSvg(ppSeries, ovToken("--pp-color"), 400, 48));
  }
  if (tgSeries.length > 1) {
    body.appendChild(sparklineSvg(tgSeries, ovToken("--tg-color"), 400, 48));
  }
  body.appendChild(
    ovLegend([
      { swatch: "swatch live", text: "prefill" },
      { swatch: "swatch", text: "generation" },
    ])
  );

  const { table, addRow } = dataTable(
    ["oMLX counter", "Session", "All-time"],
    "1.6fr 0.7fr 0.7fr"
  );
  const scope = (s, key, format) => (s ? format(s[key]) : "—");
  [
    ["Total tokens", "total_tokens", formatCompactCount],
    ["Cached tokens", "total_cached_tokens", formatCompactCount],
    ["Avg PP speed", "avg_prefill_tps", formatTps],
    ["Avg TG speed", "avg_generation_tps", formatTps],
    ["Total requests", "total_requests", formatCompactCount],
  ].forEach(([label, key, format]) => {
    addRow([
      label,
      textEl("div", "mono", scope(omlx.session, key, format)),
      textEl("div", "mono", scope(omlx.alltime, key, format)),
    ]);
  });
  addRow([
    "Cache efficiency",
    textEl(
      "div",
      "mono",
      omlx.session?.cache_efficiency_pct != null
        ? formatPercent(omlx.session.cache_efficiency_pct)
        : "—"
    ),
    textEl(
      "div",
      "mono",
      omlx.alltime?.cache_efficiency_pct != null
        ? formatPercent(omlx.alltime.cache_efficiency_pct)
        : "—"
    ),
  ]);
  body.appendChild(table);

  const loaded = asArray(omlx.loaded_models);
  if (loaded.length) {
    body.appendChild(textEl("p", "panel-note", `Loaded: ${loaded.join(", ")}`));
  }
}

function ovRenderCounters(root) {
  const router = asObject(state.telemetry?.router);
  const holder = el("div");
  // /status carries the same two counters as /telemetry; either surface may be
  // the one that answered (telemetry needs no admin token), so prefer whichever
  // is populated rather than dropping the block when only one is available.
  // An empty {} from one surface must not shadow a populated one, so test for
  // keys rather than truthiness.
  const pick = (a, b) => (Object.keys(asObject(a)).length ? a : b);
  const routed = pick(router.routed_requests, state.status?.routed_requests);
  const rejections = pick(router.capacity_rejections, state.status?.capacity_rejections);
  let any = false;
  any = ovKeyValueBlock(holder, "Routed requests (by backend id)", routed) || any;
  any = ovKeyValueBlock(holder, "Capacity rejections", rejections) || any;
  any = ovKeyValueBlock(holder, "Requests by source (harness)", state.status?.source_requests) || any;
  any = ovKeyValueBlock(holder, "Requests by scenario", state.status?.scenario_requests) || any;
  if (!any) return;
  const body = panel(root, "Request counters", "cumulative since agent start");
  body.appendChild(holder);
}

function ovRenderThisNode(root) {
  const status = state.status;
  const version = state.versionInfo;
  const host = state.telemetry?.host;
  const body = panel(root, "This node", null);
  const grid = el("div", "grid-2");
  const left = el("div", "stack");
  const right = el("div", "stack");

  ovInfoRow(left, "Version", version?.version ? `v${version.version}` : status?.version);
  ovInfoRow(left, "Install method", version?.install_method);
  ovInfoRow(left, "Platform", version?.platform);
  ovInfoRow(left, "OpenAI SDK", version?.sdk_versions?.openai);
  ovInfoRow(left, "Anthropic SDK", version?.sdk_versions?.anthropic);
  ovInfoRow(left, "Agent ID", status?.agent_id);

  ovInfoRow(right, "Hostname", status?.hostname);
  ovInfoRow(right, "Role", status?.role);
  ovInfoRow(right, "Strategy", status?.routing_strategy);
  ovInfoRow(right, "Listen", status?.listen_url || status?.listen);
  if (host) {
    ovInfoRow(right, "Host CPU", `${host.cpu_percent}%`);
    ovInfoRow(right, "Host memory", `${host.memory_used_gb} / ${host.memory_total_gb} GB`);
  } else {
    ovInfoRow(right, "Host CPU", null);
    ovInfoRow(right, "Host memory", null);
  }

  grid.append(left, right);
  body.appendChild(grid);
  body.appendChild(
    textEl(
      "p",
      "panel-note",
      "After changing listen address or port, restart the agent: netllm restart (packaged install) or menubar Settings → Restart Agent."
    )
  );
}

/* ---------------- page ---------------- */

function renderOverviewPage(root) {
  pageHeader(
    root,
    "Overview",
    null,
    `updated ${timeAgo(state.lastUpdatedAt)} · polling 5s`
  );

  const peerRows = ovPeerRows();
  ovRenderRoleBanner(root, peerRows);

  // Mesh on the left, the reading column (throughput / pools / events) on the
  // right — .grid-2 collapses to one column on narrow viewports.
  const columns = el("div", "grid-2");
  // .stack, not a bare div: the column owns the gap between the panels it
  // holds. Relying on a `.panel + .panel` margin meant any non-panel node
  // appended between them silently removed the gap.
  const left = el("div", "stack");
  const right = el("div", "stack");
  columns.append(left, right);
  root.appendChild(columns);

  ovRenderMesh(left, peerRows);
  ovRenderThroughput(right);
  ovRenderPools(right);
  ovRenderWarnings(right);

  ovRenderOmlx(root);
  ovRenderCounters(root);
  ovRenderThisNode(root);
}

registerPage("overview", renderOverviewPage);
