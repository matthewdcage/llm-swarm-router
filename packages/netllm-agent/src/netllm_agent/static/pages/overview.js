/* Home — design 1a (dark radar-led) / 1b (light table-led variant of the same
 * data). Everything below is token-driven, so one implementation reads
 * correctly in both themes.
 *
 * The page key is `overview` and stays that way: it is the same key in
 * `const PAGES`, registerPage(), `data-page`/`#hash`, `id="page-overview"` and
 * DASHBOARD_CONTROLS (tests/conformance/kit_config_surfaces.py), and the
 * conformance kit asserts they agree. "Home" is a label, not a rename.
 *
 * This page merges the old Status tab (renderStatusTab) and Serving tab
 * (renderServingTab): role/mesh/backend counts and the router + oMLX counters
 * now live together, because "what is my mesh doing right now" was the one
 * question that needed both tabs open.
 *
 * Reading order is deliberate, because this is the front door:
 *
 *   masthead   what is this, is it working, how do I point something at it
 *   banner     what is my place in the mesh
 *   mesh/…     what is it doing right now
 *   counters   the long tail
 *
 * The node facts used to be a "This node" panel at the very bottom, below
 * three panels of telemetry — so the answer to "what is this machine" was the
 * last thing on the page. It is now the first, and only the provenance half
 * (agent id, build, host gauges) is folded away.
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
 * Per-backend routed request counts. Prefer the windowed ledger
 * (`router.windows.by_backend`); fall back to cumulative `routed_requests`
 * when talking to an older agent.
 */
function ovRoutedCounts(preferredSpan = 300) {
  const windowed = telemetryWindowCounts("by_backend", preferredSpan);
  if (windowed) return windowed;
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
  return total ? { byId, total, span: null } : { byId, total: 0, span: null };
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
  const { byId, total, span } = ovRoutedCounts();
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
      span,
    };
  });
}

/** Share of routed requests served by this node's own backends. */
function ovLocalShare() {
  const { byId, total, span } = ovRoutedCounts();
  if (!total) return { share: null, span };
  let local = 0;
  ovEnabledBackends().forEach((b) => {
    if (b.local === false) return;
    local += byId.get(b.id) || 0;
  });
  return { share: local / total, span };
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

/* ---------------- 0. masthead ----------------
 *
 * The landing page has to answer three questions before anything is scrolled:
 * what is this, is it working, and how do I point something at it. Everything
 * below serves one of those and nothing else — the mesh, throughput and pools
 * answer "what is it doing", which is a different question and stays below.
 *
 * Every value degrades: no /version, no client-env, an unreachable agent and a
 * peer-less single node all render an em-dash rather than a plausible guess.
 */

/** Version as the user reads it, or null — never a fabricated "v0.0.0". */
function ovVersionText() {
  const v = state.versionInfo?.version || state.updateInfo?.current || state.status?.version;
  return v ? `v${v}` : null;
}

/**
 * What this node is doing right now, as one status the pill colour can carry.
 *
 * "Serving" is the only ok-green state, and it requires a backend that is
 * actually online: an agent answering /health with nothing behind it accepts
 * requests it cannot fulfil, which is a warning, not success.
 */
function ovServingState() {
  if (!state.healthy || !state.status) {
    return { kind: "error", accent: "accent-danger", label: "Unreachable" };
  }
  if (state.status.draining) {
    return { kind: "warn", accent: "accent-warn", label: "Draining" };
  }
  const backends = ovEnabledBackends();
  const online = backends.filter((b) => b.health?.status === "online").length;
  if (!online) {
    return {
      kind: "warn",
      accent: "accent-warn",
      label: backends.length ? "No backend online" : "No backends",
    };
  }
  return { kind: "ok", accent: "accent-ok", label: "Serving" };
}

/**
 * Version state + the action for it, beside the version itself.
 *
 * "Up to date" is deliberately muted rather than a green badge: it is the
 * absence of news, and a status colour on this page means "something needs
 * you". When there *is* an update the button leads to the Preferences update
 * card — which owns the download link, the upgrade command and the checksum —
 * rather than reimplementing any of it here.
 */
function ovUpdateControl() {
  const info = state.updateInfo;
  const row = el("div", "row masthead-update");

  if (info?.update_available) {
    row.appendChild(
      button(`Update to v${info.latest}`, "small", () => navigate("preferences"))
    );
    return row;
  }

  row.appendChild(
    button("Check for updates", "small secondary", (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      loadUpdateCheck(true).then(() => {
        showToast("Update check complete");
        render();
      });
    })
  );
  if (info?.error) {
    // A failed check is a real failure, not "no update": the agent could not
    // reach the release feed, so "up to date" would be unproven.
    row.appendChild(textEl("span", "text-warn", "check failed"));
  } else if (info) {
    row.appendChild(textEl("span", "muted", "up to date"));
  } else {
    row.appendChild(textEl("span", "muted", "not checked"));
  }
  return row;
}

function ovMastheadFact(parent, label, value) {
  const item = el("div", "masthead-fact");
  const shown = value === undefined || value === null || value === "" ? "—" : String(value);
  item.append(textEl("div", "field-label", label), textEl("div", "mono", shown));
  parent.appendChild(item);
}

/**
 * A value the user is meant to take away with them: rendered as code, with a
 * Copy beside it. `value` of null renders the em-dash and no button — there is
 * nothing to copy, and a button that copies "—" is worse than no button.
 */
function ovMastheadCopy(parent, label, value, message, help) {
  const card = el("div", "inset masthead-copy");
  card.appendChild(textEl("div", "field-label", label));
  const row = el("div", "row");
  const code = codeEl(value || "—");
  code.classList.add("masthead-code");
  row.appendChild(code);
  if (value) {
    row.appendChild(button("Copy", "small secondary", () => copyText(value, message)));
  }
  card.appendChild(row);
  if (help) card.appendChild(textEl("div", "field-help", help));
  parent.appendChild(card);
  return card;
}

/**
 * Node details that are real but rarely the reason someone opened the page:
 * identifiers, build provenance and host gauges. Folded, but folded *here* —
 * this used to be a full panel below the fold, which is the placement the user
 * called unintuitive.
 */
function ovMastheadDetails(parent) {
  const status = state.status;
  const version = state.versionInfo;
  const host = state.telemetry?.host;
  const body = collapsiblePanel(parent, "Node details", null, {
    boxClass: "inset",
    storageKey: "overview.nodeDetails",
    defaultOpen: false,
    summary: "agent id · build · host gauges",
  });

  const grid = el("div", "grid-2");
  const left = el("div", "stack");
  const right = el("div", "stack");
  ovInfoRow(left, "Agent ID", status?.agent_id);
  ovInfoRow(left, "Install method", version?.install_method);
  ovInfoRow(left, "Platform", version?.platform);
  ovInfoRow(left, "Build", version?.build);
  ovInfoRow(right, "OpenAI SDK", version?.sdk_versions?.openai);
  ovInfoRow(right, "Anthropic SDK", version?.sdk_versions?.anthropic);
  // telemetry.host is absent whenever /telemetry did not answer; the two rows
  // stay so the block does not change shape, and say "—" rather than "0%".
  ovInfoRow(right, "Host CPU", host ? `${host.cpu_percent}%` : null);
  ovInfoRow(
    right,
    "Host memory",
    host ? `${host.memory_used_gb} / ${host.memory_total_gb} GB` : null
  );
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

/**
 * The page's identity block, and the only `h1` on Home.
 *
 * The product name is the level-1 heading rather than a page title: this page
 * has no name of its own any more (the nav says "Home"), and a page with no h1
 * has no outline for a screen reader to navigate the panels by.
 */
function ovRenderMasthead(root) {
  const status = state.status;
  const serving = ovServingState();
  const body = panel(root, null, null, `masthead ${serving.accent}`);

  const top = el("div", "masthead-top");
  top.appendChild(brandLogoEl(40));

  const identity = el("div", "masthead-identity");
  identity.appendChild(textEl("h1", "masthead-title", "llm-swarm-router"));
  const versionRow = el("div", "masthead-version-row");
  versionRow.append(
    textEl("span", "masthead-version", ovVersionText() || "version unknown"),
    ovUpdateControl()
  );
  identity.appendChild(versionRow);
  top.appendChild(identity);

  const statusSide = el("div", "masthead-status");
  statusSide.appendChild(pill(serving.kind, serving.label));
  statusSide.appendChild(
    textEl("div", "panel-note", `updated ${timeAgo(state.lastUpdatedAt)} · polling 5s`)
  );
  top.appendChild(statusSide);
  body.appendChild(top);

  const backends = ovEnabledBackends();
  const online = backends.filter((b) => b.health?.status === "online").length;
  const facts = el("div", "masthead-facts");
  ovMastheadFact(facts, "Host", status?.hostname);
  ovMastheadFact(facts, "Role", status?.role);
  ovMastheadFact(facts, "Listen", status?.listen_url || status?.listen);
  const uptime = agentUptimeSeconds();
  ovMastheadFact(facts, "Uptime", uptime == null ? null : formatDuration(uptime));
  ovMastheadFact(facts, "Backends", backends.length ? `${online}/${backends.length}` : null);
  ovMastheadFact(facts, "Peers", status ? String(ovPeerList().length) : null);
  body.appendChild(facts);

  const copies = el("div", "masthead-copy-grid");
  ovMastheadCopy(
    copies,
    "Serving on",
    clientEndpointUrl(),
    "Client endpoint copied",
    "Point an OpenAI-compatible client at this base URL."
  );
  ovMastheadCopy(
    copies,
    "Join this swarm",
    swarmJoinCommand(),
    "Join command copied",
    clusterTokenSet()
      ? "Run on the machine you are adding, with your cluster token substituted — netllm never displays the stored value."
      : "Run on the machine you are adding. This swarm has no cluster token: any agent on the LAN can join."
  );
  body.appendChild(copies);

  ovMastheadDetails(body);
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

  // Role, the backend count and the peer count moved into the masthead facts,
  // which is now the block that answers "what is this node". Repeating them
  // one panel later gave the same numbers two homes that could disagree
  // mid-poll; Strategy stays because it is about routing, not identity.
  const stats = el("div", "stat-row");
  const strategyStat = statBlock("Strategy", status?.routing_strategy || "—");
  strategyStat.querySelector(".stat-value").classList.add("mono");
  stats.appendChild(strategyStat);
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
  const local = ovLocalShare();
  node.appendChild(textEl("div", "mesh-self-role", status?.role || "this node"));
  node.appendChild(textEl("div", "mesh-node-name", status?.hostname || "this node"));
  node.appendChild(textEl("div", "mesh-node-share", ovShareText(local.share)));
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
  const selfShare = ovLocalShare();
  t.addRow([
    selfName,
    textEl("div", "muted", `this node · ${state.status?.role || "unknown"}`),
    textEl("div", "mono", ovShareText(selfShare.share)),
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

function ovRenderTrafficByBackend(root) {
  const counts = telemetryWindowCounts("by_backend", 300);
  if (!counts || !counts.total) return;
  const note = counts.span ? telemetrySpanLabel(counts.span) : "";
  const body = panel(root, "Traffic by backend", note);
  const { table, addRow } = dataTable(
    ["Backend", "Requests", "Share", "p50", "Prefill", "Generation"],
    "1.4fr 0.6fr 0.55fr 0.55fr 0.7fr 0.7fr"
  );

  const rows = [...counts.byId.entries()].sort((a, b) => b[1] - a[1]);
  rows.forEach(([backendId, requestCount]) => {
    const traffic = telemetryWindowRow("by_backend", backendId, counts.span);
    const latency = telemetryBackendLatency(backendId);
    const label =
      backendId.startsWith("peer:") ? backendId.slice(5) : backendId;
    const share = requestCount / counts.total;
    addRow([
      textEl("div", "mono", label),
      textEl("div", "mono", formatCompactCount(requestCount)),
      textEl("div", "mono", formatPercent(share * 100)),
      textEl(
        "div",
        "mono",
        latency?.p50Ms == null ? "—" : `${Math.round(latency.p50Ms)} ms`
      ),
      textEl("div", "mono", telemetryRateText(traffic?.avgPrefillTps ?? null)),
      textEl(
        "div",
        "mono",
        telemetryRateText(traffic?.avgGenerationTps ?? null)
      ),
    ]);
  });
  body.appendChild(table);
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
    ["Avg prefill (tok/s)", "avg_prefill_tps", telemetryRateText],
    ["Avg generation (tok/s)", "avg_generation_tps", telemetryRateText],
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
  const windowed = telemetryWindowCounts(300);
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
    ? `${telemetrySpanLabel(windowed.span)} window · live over ${
        Number.isFinite(liveWindow) ? liveWindow : 10
      }s`
    : counts.span
      ? `${telemetrySpanLabel(counts.span)} window`
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
    statBlock("gen tok/s", telemetryRateText(live.generation_tps)),
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
  const body = panel(root, "Request counters", "cumulative since agent start · windowed figures above");
  body.appendChild(holder);
}

/* ---------------- page ---------------- */

/*
 * The page key stays `overview` — `const PAGES`, registerPage(),
 * data-page/#hash, `id="page-overview"` and DASHBOARD_CONTROLS in
 * tests/conformance/kit_config_surfaces.py all agree on it, and the kit
 * asserts that agreement. "Home" is the label the user reads (sidebar +
 * aria-label); renaming the key would mean editing five places to produce the
 * same visible result.
 *
 * There is no `pageHeader()` here on purpose. An "Overview" h1 above a
 * masthead that already names the product said the product's name twice and
 * the page's name once, for a page that is now the front door and has no name
 * of its own — so the masthead carries the h1 instead. Home therefore has
 * exactly one h1, like every other page.
 */
function renderOverviewPage(root) {
  const peerRows = ovPeerRows();
  ovRenderMasthead(root);
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
  ovRenderTrafficByBackend(right);
  ovRenderPools(right);
  ovRenderWarnings(right);

  ovRenderOmlx(root);
  ovRenderCounters(root);
}

registerPage("overview", renderOverviewPage);
