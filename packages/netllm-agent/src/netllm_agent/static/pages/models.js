/* Models & pools page — design 1c (list) and 3a (pool drill-in).
 *
 * Port of the pre-redesign renderModelsTab. Everything it could do still
 * works: the search filter (state.modelsSearchText), collapsible per-node
 * groups (state.modelsCollapsedGroups), favourites (ui.model_favorites) and
 * add/remove-model-to-pool editing against configDraft.routing.model_pools.
 * What is new is the ordering: pools are the primary object and the node
 * grouping became one of three views, because a pool is what a client
 * actually addresses.
 *
 * Mockup 3a treats a pool as a managed thing — membership rules, admission
 * checks, per-member weights, warm residency, per-node drain, request
 * simulation. `ModelPool` is {enabled, hosts[], models[]} and nothing else,
 * and there is no endpoint behind any of those verbs. Those sections render
 * an explicit "not yet available" state rather than controls that silently
 * do nothing; each one is recorded as a phase-A gap.
 */

/** Which of the three list views is showing. Purely local UI state. */
let modelsViewMode = "pools";

/** Buffer for the "add alias" row so typing doesn't force a re-render. */
const modelsAliasDraft = { canonical: "", served: "" };

/* ---------------- config draft accessors ---------------- */

function modelsEnsureDraft() {
  if (!state.configDraft) state.configDraft = emptyConfigDraft();
  if (!state.configDraft.routing) state.configDraft.routing = {};
  return state.configDraft;
}

function modelsEnsurePools() {
  const draft = modelsEnsureDraft();
  if (!draft.routing.model_pools) draft.routing.model_pools = {};
  return draft.routing.model_pools;
}

function modelsEnsureAliases() {
  const draft = modelsEnsureDraft();
  if (!draft.routing.model_aliases) draft.routing.model_aliases = {};
  return draft.routing.model_aliases;
}

/* Wire data can be the wrong type or hold nulls — `?? []` only covers a missing
 * key. These two are the single read points for the two lists this page walks,
 * so every derivation below is working with real arrays of real rows. */
function modelsBackendRows() {
  return asArray(state.status?.backends).filter(Boolean);
}

function modelsCatalogRows() {
  return asArray(state.models).filter(Boolean);
}

/** routing.model_pools as a stable, sorted array of plain objects. */
function modelsPoolSummaries() {
  const pools = asObject(state.configDraft?.routing?.model_pools);
  return Object.entries(pools)
    .map(([name, entry]) => ({
      name,
      enabled: entry?.enabled !== false,
      hosts: asArray(entry?.hosts),
      models: asArray(entry?.models),
    }))
    .sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" }));
}

function modelsPoolsContaining(modelId) {
  return modelsPoolSummaries().filter((p) => p.models.includes(modelId));
}

// Swift mirror: SettingsViewModel.backendMatchesHostRef / pool.py's
// RouterPool._backend_matches_host_ref. Keep in sync across all three.
function modelsBackendMatchesHostRef(backend, ref) {
  const target = (ref || "").trim();
  if (!target) return false;
  const trimSlash = (s) => (s && s.endsWith("/") ? s.slice(0, -1) : s || "");
  if (backend.id === target) return true;
  if (backend.id === `peer:${target}`) return true;
  if (backend.agent_id && backend.agent_id === target) return true;
  return trimSlash(backend.base_url) === trimSlash(target);
}

/** Backends this pool's host refs resolve to, in status order. */
function modelsPoolMembers(pool) {
  const backends = modelsBackendRows();
  return backends.filter((b) => pool.hosts.some((ref) => modelsBackendMatchesHostRef(b, ref)));
}

/** Host refs in the pool that match no backend at all (typo, or host down). */
function modelsUnresolvedHosts(pool) {
  const backends = modelsBackendRows();
  return pool.hosts.filter((ref) => !backends.some((b) => modelsBackendMatchesHostRef(b, ref)));
}

// Client-side pool effectiveness (mirrors SettingsViewModel.poolInactiveReason):
// a pool is "active" iff >=1 host ref resolves to an online backend serving
// >=1 pool model — all derivable from /netllm/v1/status. Returns null when
// active, else a human-readable reason.
function modelsPoolInactiveReason(pool) {
  if (!pool.enabled) return "pool disabled";
  const backends = state.status?.backends;
  if (!backends) return "agent not running";
  if (!pool.hosts.length) return "no hosts configured";
  if (!pool.models.length) return "no models configured";
  const online = modelsPoolMembers(pool).filter((b) => b.health?.status === "online");
  if (!online.length) return "host offline";
  const serving = online.some((b) =>
    asArray(b.health?.models).some((m) => pool.models.includes(m))
  );
  return serving ? null : "no pool model served";
}

/* ---------------- mutations (all staged on configDraft) ---------------- */

// Same pool/pool-2/... naming as the Swift addModelPool and the Routing
// page's generic dict-add button.
function modelsAddPool() {
  const pools = modelsEnsurePools();
  let name = "pool";
  let suffix = 1;
  while (pools[name]) {
    suffix += 1;
    name = `pool-${suffix}`;
  }
  pools[name] = { enabled: true, hosts: [], models: [] };
  markDirty();
  return name;
}

function addModelToPool(poolName, modelId) {
  const entry = modelsEnsurePools()[poolName];
  if (!entry) return;
  if (!Array.isArray(entry.models)) entry.models = [];
  if (!entry.models.includes(modelId)) entry.models.push(modelId);
  markDirty();
  showToast(`Added ${modelId} to pool ${poolName} — Save to persist.`);
  render();
}

function removeModelFromPool(poolName, modelId) {
  const entry = modelsEnsurePools()[poolName];
  if (!entry) return;
  entry.models = asArray(entry.models).filter((m) => m !== modelId);
  markDirty();
  showToast(`Removed ${modelId} from pool ${poolName} — Save to persist.`);
  render();
}

// "New pool…" from a model row: create with the standard naming, seed it
// with the model, then drop the operator into the pool detail so the hosts
// can be set immediately (the old tab sent them to the Routing tab).
function addModelToNewPool(modelId) {
  const name = modelsAddPool();
  const entry = modelsEnsurePools()[name];
  entry.models = [modelId];
  markDirty();
  state.openPoolId = name;
  showToast(`Created pool ${name} with ${modelId} — add its hosts, then Save.`);
  render();
}

function modelsAddHostToPool(poolName, ref) {
  const entry = modelsEnsurePools()[poolName];
  if (!entry || !ref) return;
  if (!Array.isArray(entry.hosts)) entry.hosts = [];
  if (!entry.hosts.includes(ref)) entry.hosts.push(ref);
  markDirty();
  render();
}

function modelsRemoveHostFromPool(poolName, ref) {
  const entry = modelsEnsurePools()[poolName];
  if (!entry) return;
  entry.hosts = asArray(entry.hosts).filter((h) => h !== ref);
  markDirty();
  render();
}

function modelsDeletePool(poolName) {
  const pools = modelsEnsurePools();
  delete pools[poolName];
  markDirty();
  state.openPoolId = null;
  showToast(`Deleted pool ${poolName} — Save to persist.`);
  render();
}

function toggleFavoriteModel(modelId) {
  const draft = modelsEnsureDraft();
  if (!draft.ui) draft.ui = {};
  // A wrong-typed favourites value would make splice/push throw; start over
  // from an empty list rather than losing the click.
  const list = asArray(draft.ui.model_favorites);
  const idx = list.indexOf(modelId);
  if (idx >= 0) list.splice(idx, 1);
  else list.push(modelId);
  draft.ui.model_favorites = list;
  markDirty(true);
  render();
}

function modelsFavorites() {
  return asArray(state.configDraft?.ui?.model_favorites);
}

/* ---------------- aliases ---------------- */

/** routing.model_aliases flattened to (canonical, served id) pairs. */
function modelsAliasPairs() {
  const aliases = asObject(state.configDraft?.routing?.model_aliases);
  const rows = [];
  Object.entries(aliases).forEach(([canonical, ids]) => {
    asArray(ids).forEach((served) => rows.push({ canonical, served }));
  });
  return rows;
}

/** Aliases whose served ids overlap this pool's models. */
function modelsAliasesForPool(pool) {
  return modelsAliasPairs().filter((row) => pool.models.includes(row.served));
}

function modelsAddAlias() {
  const canonical = modelsAliasDraft.canonical.trim();
  const served = modelsAliasDraft.served.trim();
  if (!canonical || !served) {
    showToast("An alias needs both a client-facing name and a served model id.");
    return;
  }
  const aliases = modelsEnsureAliases();
  if (!Array.isArray(aliases[canonical])) aliases[canonical] = [];
  if (!aliases[canonical].includes(served)) aliases[canonical].push(served);
  modelsAliasDraft.canonical = "";
  modelsAliasDraft.served = "";
  markDirty();
  render();
}

function modelsRemoveAlias(canonical, served) {
  const aliases = modelsEnsureAliases();
  const list = asArray(aliases[canonical]).filter((id) => id !== served);
  if (list.length) aliases[canonical] = list;
  else delete aliases[canonical];
  markDirty();
  render();
}

/* ---------------- node identity ---------------- */

/** Display name for a backend's machine, plus whether it is this one. */
function modelsNodeLabel(backend) {
  if (backend.cloud_provider) {
    const display =
      state.config?.cloud?.providers?.[backend.cloud_provider]?.display_name ||
      backend.cloud_provider;
    return { name: `${display} (cloud)`, self: false };
  }
  if (backend.local) {
    return { name: state.status?.hostname || "this machine", self: true };
  }
  const peer = asArray(state.status?.peers)
    .filter(Boolean)
    .find((p) => p.agent_id === backend.agent_id);
  return { name: peer?.hostname || backend.agent_id || backend.base_url, self: false };
}

function modelsNodeCell(backend) {
  const { name, self } = modelsNodeLabel(backend);
  const cell = el("div", "row");
  cell.appendChild(statusDot(modelsHealthKind(backend)));
  cell.appendChild(textEl("span", "", name));
  // Neutral: "you" marks which row is this agent, and says nothing about its
  // health — that is the statusDot immediately to its left.
  if (self) cell.appendChild(pill("neutral", "you"));
  return cell;
}

function modelsHealthKind(backend) {
  if (backend.enabled === false) return "";
  const status = backend.health?.status;
  if (status === "online") return "ok";
  if (status === "offline") return "error";
  return "warn";
}

function modelsStateCell(backend) {
  if (backend.enabled === false) return textEl("div", "muted", "disabled");
  const status = backend.health?.status || "unknown";
  const cls =
    status === "online" ? "text-ok" : status === "offline" ? "text-danger" : "text-warn";
  const cell = textEl("div", cls, status);
  if (backend.health?.detail) cell.title = backend.health.detail;
  return cell;
}

function modelsLatencyCell(backend) {
  const p50 = Number(backend.health?.latency_p50_ms);
  if (!Number.isFinite(p50) || p50 <= 0) return textEl("div", "muted mono", "—");
  return textEl("div", "mono", `${Math.round(p50)}ms`);
}

/**
 * Share of the pool's routed traffic per member.
 *
 * status.routed_requests is a cumulative per-backend counter (pool.py
 * routed_counts) — it is process-lifetime totals, not the mockup's "last
 * 5 min" window, so the column is labelled for what it is.
 */
function modelsShareCell(backend, total) {
  const routed = Number(asObject(state.status?.routed_requests)[backend.id] || 0);
  const cell = el("div");
  if (!total) {
    cell.appendChild(textEl("div", "muted", "no traffic yet"));
    return cell;
  }
  const pct = (routed / total) * 100;
  cell.appendChild(textEl("div", "mono", `${formatPercent(pct)} · ${formatCompactCount(routed)}`));
  const meter = el("div", "meter");
  const fill = el("span", backend.health?.status === "online" ? "" : "warn");
  fill.style.width = `${Math.max(2, Math.min(100, pct))}%`;
  meter.appendChild(fill);
  cell.appendChild(meter);
  return cell;
}

function modelsRoutedTotal(members) {
  const routed = asObject(state.status?.routed_requests);
  return members.reduce((sum, b) => sum + Number(routed[b.id] || 0), 0);
}

/** How many of a pool's models this backend actually advertises. */
function modelsServedCell(backend, pool) {
  const served = asArray(backend.health?.models).filter((m) => pool.models.includes(m));
  if (!pool.models.length) return textEl("div", "muted", "—");
  const cls = served.length ? "mono" : "mono text-warn";
  const cell = textEl("div", cls, `${served.length}/${pool.models.length}`);
  if (served.length) cell.title = served.join(", ");
  else cell.title = "This host serves none of the pool's models.";
  return cell;
}

/* ---------------- node grouping (ported from renderModelsTab) ---------------- */

function modelsGroups() {
  const status = state.status;
  if (!status) return [];
  const locals = [];
  const cloudBuckets = [];
  const peerBuckets = [];
  modelsBackendRows().forEach((b) => {
    if (b.cloud_provider) {
      const bucket = cloudBuckets.find((x) => x.key === b.cloud_provider);
      if (bucket) bucket.backends.push(b);
      else cloudBuckets.push({ key: b.cloud_provider, backends: [b] });
    } else if (b.local) {
      locals.push(b);
    } else {
      const key = b.agent_id || b.base_url;
      const bucket = peerBuckets.find((x) => x.key === key);
      if (bucket) bucket.backends.push(b);
      else peerBuckets.push({ key, backends: [b] });
    }
  });

  function makeGroup(id, title, subtitle, backends) {
    const seen = new Set();
    const rows = [];
    backends.forEach((b) => {
      asArray(b.health?.models).forEach((m) => {
        if (seen.has(m)) return;
        seen.add(m);
        rows.push({ model: String(m), provider: b.provider || "custom" });
      });
    });
    rows.sort((a, b2) => a.model.localeCompare(b2.model, undefined, { sensitivity: "base" }));
    return {
      id,
      title,
      subtitle,
      online: backends.some((b) => b.health?.status === "online"),
      modelCount: rows.length,
      inFlight: backends.reduce((sum, b) => sum + (b.in_flight || 0), 0),
      rows,
    };
  }

  const groups = [];
  if (locals.length) {
    const title = status.hostname || "This machine";
    const providers = [...new Set(locals.map((b) => b.provider || "custom"))].join(" · ");
    groups.push(makeGroup("local", title, providers, locals));
  }
  peerBuckets.forEach((bucket) => {
    const peer = asArray(status.peers)
      .filter(Boolean)
      .find((p) => p.agent_id === bucket.key);
    const title = peer?.hostname ? `${peer.hostname} (${peer.agent_id})` : bucket.key;
    const providers = [...new Set(bucket.backends.map((b) => b.provider || "custom"))].join(" · ");
    groups.push(makeGroup(`peer-${bucket.key}`, title, providers, bucket.backends));
  });
  cloudBuckets.forEach((bucket) => {
    const display = state.config?.cloud?.providers?.[bucket.key]?.display_name || bucket.key;
    groups.push(makeGroup(`cloud-${bucket.key}`, `${display} (cloud)`, "", bucket.backends));
  });
  return groups;
}

/** All model ids reachable anywhere, from the merged /v1/models catalog. */
function modelsCatalogIds() {
  // Ids are compared with localeCompare below, which only exists on strings —
  // a numeric id from a hostile catalog would otherwise throw mid-sort.
  const ids = new Set(modelsCatalogRows().map((m) => String(m.id ?? "")));
  modelsBackendRows().forEach((b) => {
    asArray(b.health?.models).forEach((m) => ids.add(String(m)));
  });
  ids.delete("");
  return [...ids].sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base" }));
}

function modelsCapabilityMap() {
  return new Map(modelsCatalogRows().map((m) => [String(m.id ?? ""), m.capability]));
}

function modelsSearchNeedle() {
  return state.modelsSearchText.trim().toLowerCase();
}

/* ---------------- shared bits ---------------- */

function modelsSearchField() {
  const input = el("input");
  input.type = "search";
  input.id = "models-search";
  input.placeholder = "Filter models, pools or hosts…";
  input.value = state.modelsSearchText;
  input.setAttribute("aria-label", "Filter models, pools or hosts");
  input.oninput = () => {
    state.modelsSearchText = input.value;
    const caret = input.selectionStart;
    render();
    // render() replaces the page subtree, so the live node is gone: put the
    // caret back where it was or the field loses focus every keystroke.
    const next = document.getElementById("models-search");
    if (next) {
      next.focus();
      if (caret != null) next.setSelectionRange(caret, caret);
    }
  };
  return input;
}

function modelsViewSwitch() {
  const wrap = el("div", "segmented");
  wrap.setAttribute("role", "group");
  wrap.setAttribute("aria-label", "Model view");
  [
    ["pools", "Pools"],
    ["nodes", "By node"],
    ["aliases", "Aliases"],
  ].forEach(([id, label]) => {
    // Bare <button>: .segmented styles its own children, and button() would
    // default an empty class to "secondary".
    const b = textEl("button", "", label);
    b.type = "button";
    b.onclick = () => {
      modelsViewMode = id;
      render();
    };
    b.setAttribute("aria-pressed", String(modelsViewMode === id));
    wrap.appendChild(b);
  });
  return wrap;
}

function modelsHeaderAside() {
  const wrap = el("div", "row");
  wrap.appendChild(modelsSearchField());
  wrap.appendChild(modelsViewSwitch());
  wrap.appendChild(button("Rescan", "secondary", () => runDiscover()));
  wrap.appendChild(button("Scan LAN", "secondary", () => runPeersScan(false)));
  return wrap;
}

function modelsSubtitle(pools) {
  const catalog = modelsCatalogIds().length;
  const nodes = new Set();
  modelsBackendRows().forEach((b) => {
    const { name } = modelsNodeLabel(b);
    nodes.add(name);
  });
  const aliases = Object.keys(asObject(state.configDraft?.routing?.model_aliases)).length;
  return (
    `${catalog} routed model${catalog === 1 ? "" : "s"} across ` +
    `${nodes.size} node${nodes.size === 1 ? "" : "s"} · ` +
    `${pools.length} pool${pools.length === 1 ? "" : "s"} · ` +
    `${aliases} alias${aliases === 1 ? "" : "es"}`
  );
}

function modelsEmptyCatalogNote(root) {
  root.appendChild(
    textEl(
      "p",
      "empty",
      state.healthy
        ? "No backends yet — start oMLX, Ollama, LM Studio or vLLM. The agent finds them automatically."
        : "Agent not running — start it to load the model catalog."
    )
  );
}

/* ---------------- list view (design 1c) ---------------- */

function modelsPoolCard(root, pool) {
  const members = modelsPoolMembers(pool);
  const reason = modelsPoolInactiveReason(pool);
  const note = el("div", "panel-note");
  note.appendChild(
    document.createTextNode(
      `${pool.hosts.length} host${pool.hosts.length === 1 ? "" : "s"} · ` +
        `${pool.models.length} model${pool.models.length === 1 ? "" : "s"}`
    )
  );
  const body = panel(root, pool.name, note, reason ? "accent-warn" : "accent-ok");

  const head = el("div", "row-between");
  const left = el("div", "row");
  left.appendChild(reason ? pill("warn", reason) : pill("ok", "active"));
  pool.models.slice(0, 3).forEach((m) => left.appendChild(textEl("span", "mono", m)));
  if (pool.models.length > 3) {
    left.appendChild(textEl("span", "muted", `+${pool.models.length - 3} more`));
  }
  head.appendChild(left);

  const right = el("div", "row");
  modelsAliasesForPool(pool)
    .slice(0, 3)
    .forEach((row) => {
      const chip = textEl("span", "chip", `${row.canonical} →`);
      chip.title = `Clients asking for ${row.canonical} reach ${row.served}.`;
      right.appendChild(chip);
    });
  right.appendChild(
    button("Pool settings", "secondary", () => {
      state.openPoolId = pool.name;
      render();
    })
  );
  head.appendChild(right);
  body.appendChild(head);

  if (!members.length) {
    body.appendChild(
      textEl(
        "p",
        "empty",
        pool.hosts.length
          ? "None of this pool's hosts resolve to a known backend right now."
          : "No hosts yet — open Pool settings to add the machines that serve it."
      )
    );
  } else {
    const total = modelsRoutedTotal(members);
    const template = "1.3fr .9fr .8fr 1.2fr .8fr .9fr";
    const t = dataTable(
      ["Node", "Backend", "Pool models", "Share of routed", "p50", "State"],
      template
    );
    members.forEach((b) => {
      t.addRow([
        modelsNodeCell(b),
        textEl("div", "mono muted", PROVIDER_LABELS[b.provider] || b.provider || "custom"),
        modelsServedCell(b, pool),
        modelsShareCell(b, total),
        modelsLatencyCell(b),
        modelsStateCell(b),
      ]);
    });
    const unresolved = modelsUnresolvedHosts(pool);
    if (unresolved.length) {
      const foot = el("div", "row");
      foot.appendChild(pill("warn", "unresolved"));
      foot.appendChild(
        textEl(
          "span",
          "muted",
          `${unresolved.join(", ")} — no backend matches this host ref, so the pool never uses it.`
        )
      );
      t.addFoot(foot);
    }
    body.appendChild(t.table);
  }
  return body;
}

function modelsUnpooledPanel(root, pools) {
  const pooled = new Set();
  pools.forEach((p) => p.models.forEach((m) => pooled.add(m)));
  const unpooled = modelsCatalogIds().filter((id) => !pooled.has(id));
  const body = panel(root, "Unpooled models", `${unpooled.length} model(s)`);
  if (!unpooled.length) {
    body.appendChild(textEl("p", "empty", "Every reachable model belongs to a pool."));
    return;
  }
  body.appendChild(
    textEl(
      "p",
      "panel-desc",
      "Reachable but not in a pool — served only when a client names them exactly."
    )
  );
  // .chip-list, not .row: a wrapped list of labels with one identical gap.
  // .row is a single-line flex row, so these ids were stretched across the
  // panel with ragged spacing and long ones broke mid-token.
  const chips = el("div", "chip-list");
  unpooled.slice(0, 12).forEach((id) => {
    const tag = textEl("span", "tag", id);
    // The tag ellipsises at the panel width, so the full id has to stay
    // reachable — model ids are the thing a client has to name exactly.
    tag.title = id;
    chips.appendChild(tag);
  });
  if (unpooled.length > 12) {
    chips.appendChild(textEl("span", "tag muted", `+${unpooled.length - 12} more`));
  }
  body.appendChild(chips);
  body.appendChild(
    button("New pool", "secondary", () => {
      state.openPoolId = modelsAddPool();
      render();
    })
  );
}

function modelsPoolsView(root, pools) {
  const needle = modelsSearchNeedle();
  const visible = !needle
    ? pools
    : pools.filter(
        (p) =>
          p.name.toLowerCase().includes(needle) ||
          p.models.some((m) => m.toLowerCase().includes(needle)) ||
          p.hosts.some((h) => h.toLowerCase().includes(needle))
      );

  if (!pools.length) {
    const body = panel(root, "No pools yet", "routing.model_pools is empty");
    body.appendChild(
      textEl(
        "p",
        "panel-desc",
        "A pool lets a set of machines answer for a set of models, whatever a client " +
          "asks for. Create one here, or add a model to a new pool from the By node view."
      )
    );
    body.appendChild(
      button("New pool", "", () => {
        state.openPoolId = modelsAddPool();
        render();
      })
    );
  } else if (!visible.length) {
    root.appendChild(textEl("p", "empty", `No pool matches "${state.modelsSearchText}".`));
  } else {
    visible.forEach((pool) => modelsPoolCard(root, pool));
  }
  modelsUnpooledPanel(root, pools);
}

function modelsPoolMenu(modelId) {
  const menu = el("select");
  menu.setAttribute("aria-label", `Add ${modelId} to a pool`);
  const placeholder = el("option");
  placeholder.value = "";
  placeholder.textContent = "Add to pool…";
  menu.appendChild(placeholder);
  modelsPoolSummaries()
    .filter((p) => !p.models.includes(modelId))
    .forEach((pool) => {
      const opt = el("option");
      opt.value = pool.name;
      opt.textContent = pool.name;
      menu.appendChild(opt);
    });
  const newOpt = el("option");
  newOpt.value = "__new__";
  newOpt.textContent = "New pool…";
  menu.appendChild(newOpt);
  menu.onchange = () => {
    const choice = menu.value;
    menu.value = "";
    if (choice === "__new__") addModelToNewPool(modelId);
    else if (choice) addModelToPool(choice, modelId);
  };
  return menu;
}

function modelsModelRow(row, capabilities) {
  const favorites = modelsFavorites();
  const isFav = favorites.includes(row.model);
  const wrap = el("div", "inset row-between");

  const left = el("div", "row");
  const star = button(isFav ? "★" : "☆", "chip", () => toggleFavoriteModel(row.model));
  star.setAttribute("aria-pressed", String(isFav));
  star.setAttribute("aria-label", `${isFav ? "Unfavourite" : "Favourite"} ${row.model}`);
  star.title = "Favourites drive the macOS menu bar model list (ui.model_favorites).";
  left.appendChild(star);
  left.appendChild(textEl("span", "mono", row.model));
  const capability = capabilities.get(row.model);
  if (capability) left.appendChild(textEl("span", "muted", capability));
  left.appendChild(
    textEl("span", "muted", PROVIDER_LABELS[row.provider] || row.provider)
  );
  wrap.appendChild(left);

  const right = el("div", "row");
  modelsPoolsContaining(row.model).forEach((pool) => {
    const reason = modelsPoolInactiveReason(pool);
    const badge = pill(reason ? "warn" : "ok", pool.name);
    badge.title = reason ? `Pool ${pool.name} is inactive: ${reason}.` : `Pool ${pool.name} is active.`;
    right.appendChild(badge);
    const rm = button("×", "ghost", () => removeModelFromPool(pool.name, row.model));
    rm.setAttribute("aria-label", `Remove ${row.model} from pool ${pool.name}`);
    right.appendChild(rm);
  });
  right.appendChild(modelsPoolMenu(row.model));
  wrap.appendChild(right);
  return wrap;
}

function modelsNodeGroup(root, group, filterActive, capabilities) {
  const collapsed = !filterActive && state.modelsCollapsedGroups.has(group.id);
  const summary = [`${group.modelCount} model${group.modelCount === 1 ? "" : "s"}`];
  if (group.inFlight > 0) summary.push(`${group.inFlight} in flight`);

  const note = el("div", "panel-note");
  note.appendChild(document.createTextNode(summary.join(" · ")));
  const body = panel(root, group.title, note);

  const head = el("div", "row-between");
  const left = el("div", "row");
  left.appendChild(statusDot(group.online ? "ok" : "error"));
  if (group.subtitle) left.appendChild(textEl("span", "muted", group.subtitle));
  head.appendChild(left);
  const toggle = button(collapsed ? "Expand" : "Collapse", "secondary", () => {
    if (state.modelsCollapsedGroups.has(group.id)) state.modelsCollapsedGroups.delete(group.id);
    else state.modelsCollapsedGroups.add(group.id);
    render();
  });
  toggle.setAttribute("aria-expanded", String(!collapsed));
  head.appendChild(toggle);
  body.appendChild(head);

  if (collapsed) return;
  if (!group.rows.length) {
    body.appendChild(textEl("p", "empty", "No models advertised by this node."));
    return;
  }
  const list = el("div", "stack");
  group.rows.forEach((row) => list.appendChild(modelsModelRow(row, capabilities)));
  body.appendChild(list);
}

function modelsNodesView(root) {
  const groups = modelsGroups();
  const needle = modelsSearchNeedle();
  const filterActive = needle.length > 0;
  const capabilities = modelsCapabilityMap();

  let visible = groups;
  if (filterActive) {
    visible = groups
      .map((g) => {
        const titleMatch =
          g.title.toLowerCase().includes(needle) || g.subtitle.toLowerCase().includes(needle);
        const rows = titleMatch
          ? g.rows
          : g.rows.filter(
              (r) =>
                r.model.toLowerCase().includes(needle) ||
                r.provider.toLowerCase().includes(needle)
            );
        return { ...g, rows };
      })
      .filter((g) => g.rows.length > 0);
  }

  if (!groups.length) {
    modelsEmptyCatalogNote(root);
    return;
  }
  if (!visible.length) {
    root.appendChild(textEl("p", "empty", `No models match "${state.modelsSearchText}".`));
    return;
  }
  visible.forEach((g) => modelsNodeGroup(root, g, filterActive, capabilities));
  root.appendChild(
    textEl(
      "p",
      "empty",
      "Pool edits write routing.model_pools — press Save in the toolbar to persist. " +
        "Full LAN model merge: netllm models --lan."
    )
  );
}

function modelsAliasesView(root) {
  const needle = modelsSearchNeedle();
  const pools = modelsPoolSummaries();
  let rows = modelsAliasPairs();
  if (needle) {
    rows = rows.filter(
      (r) =>
        r.canonical.toLowerCase().includes(needle) || r.served.toLowerCase().includes(needle)
    );
  }

  const body = panel(root, "Aliases", "what clients can ask for");
  body.appendChild(
    textEl(
      "p",
      "panel-desc",
      "routing.model_aliases maps a client-facing name to the provider-specific ids " +
        "that serve it, so a mixed fleet answers one requested name."
    )
  );

  if (!rows.length) {
    body.appendChild(
      textEl(
        "p",
        "empty",
        needle ? `No alias matches "${state.modelsSearchText}".` : "No aliases configured."
      )
    );
  } else {
    const list = el("div", "stack");
    rows.forEach((row) => {
      const line = el("div", "inset row-between");
      const left = el("div", "row");
      left.appendChild(textEl("span", "mono", row.canonical));
      left.appendChild(textEl("span", "muted", "→"));
      left.appendChild(textEl("span", "mono", row.served));
      const pool = pools.find((p) => p.models.includes(row.served));
      if (pool) left.appendChild(textEl("span", "muted", `${pool.name} pool`));
      line.appendChild(left);
      const rm = button("Remove", "ghost", () => modelsRemoveAlias(row.canonical, row.served));
      rm.setAttribute("aria-label", `Remove alias ${row.canonical} → ${row.served}`);
      line.appendChild(rm);
      list.appendChild(line);
    });
    body.appendChild(list);
  }

  const add = el("div", "inset row");
  const canonical = el("input");
  canonical.type = "text";
  canonical.placeholder = "client name (e.g. gpt-4o-mini)";
  canonical.value = modelsAliasDraft.canonical;
  canonical.setAttribute("aria-label", "Alias — client-facing model name");
  canonical.oninput = () => {
    modelsAliasDraft.canonical = canonical.value;
  };
  const served = el("select");
  served.setAttribute("aria-label", "Alias — served model id");
  const blank = el("option");
  blank.value = "";
  blank.textContent = "served model id…";
  served.appendChild(blank);
  modelsCatalogIds().forEach((id) => {
    const opt = el("option");
    opt.value = id;
    opt.textContent = id;
    if (id === modelsAliasDraft.served) opt.selected = true;
    served.appendChild(opt);
  });
  served.onchange = () => {
    modelsAliasDraft.served = served.value;
  };
  add.append(canonical, textEl("span", "muted", "→"), served, button("Add alias", "", modelsAddAlias));
  body.appendChild(add);
}

/* ---------------- pool detail (design 3a) ---------------- */

function modelsBreadcrumb(root, pool) {
  const crumb = el("div", "row");
  const back = button("Models & pools", "ghost", () => {
    state.openPoolId = null;
    render();
  });
  back.setAttribute("aria-label", "Back to models and pools");
  crumb.appendChild(back);
  crumb.appendChild(textEl("span", "muted", "›"));
  crumb.appendChild(textEl("span", "muted", pool.name));
  root.appendChild(crumb);
}

function modelsMembershipPanel(root, pool) {
  const body = panel(root, "Membership", "re-evaluated on every discovery pass");
  body.appendChild(
    textEl(
      "p",
      "panel-desc",
      "A pool is an explicit list of hosts and the models they may answer for. " +
        "Any backend below becomes a candidate for any request the pool can serve."
    )
  );

  body.appendChild(switchRow("Pool enabled", pool.enabled, (checked) => {
    modelsEnsurePools()[pool.name].enabled = checked;
    markDirty();
    render();
  }));

  // Hosts.
  body.appendChild(textEl("div", "field-label", "Hosts"));
  if (!pool.hosts.length) {
    body.appendChild(textEl("p", "empty", "No hosts — this pool is inert until one is added."));
  } else {
    const list = el("div", "stack");
    const backends = modelsBackendRows();
    pool.hosts.forEach((ref) => {
      const line = el("div", "inset row-between");
      const left = el("div", "row");
      const match = backends.find((b) => modelsBackendMatchesHostRef(b, ref));
      left.appendChild(statusDot(match ? modelsHealthKind(match) : "warn"));
      left.appendChild(textEl("span", "mono", ref));
      left.appendChild(
        match
          ? textEl("span", "muted", modelsNodeLabel(match).name)
          : textEl("span", "text-warn", "no matching backend")
      );
      line.appendChild(left);
      const rm = button("Remove", "ghost", () => modelsRemoveHostFromPool(pool.name, ref));
      rm.setAttribute("aria-label", `Remove host ${ref} from pool ${pool.name}`);
      line.appendChild(rm);
      list.appendChild(line);
    });
    body.appendChild(list);
  }

  const addHost = el("div", "row");
  const hostSelect = el("select");
  hostSelect.setAttribute("aria-label", `Add a host to pool ${pool.name}`);
  const hostBlank = el("option");
  hostBlank.value = "";
  hostBlank.textContent = "Add host…";
  hostSelect.appendChild(hostBlank);
  modelsBackendRows()
    .filter((b) => !pool.hosts.some((ref) => modelsBackendMatchesHostRef(b, ref)))
    .forEach((b) => {
      const opt = el("option");
      opt.value = b.id;
      opt.textContent = `${modelsNodeLabel(b).name} — ${b.id}`;
      hostSelect.appendChild(opt);
    });
  hostSelect.onchange = () => {
    const ref = hostSelect.value;
    hostSelect.value = "";
    if (ref) modelsAddHostToPool(pool.name, ref);
  };
  addHost.appendChild(hostSelect);
  body.appendChild(addHost);

  // Models.
  body.appendChild(textEl("div", "field-label", "Models"));
  if (!pool.models.length) {
    body.appendChild(textEl("p", "empty", "No models — the pool answers for nothing."));
  } else {
    const list = el("div", "stack");
    pool.models.forEach((modelId) => {
      const line = el("div", "inset row-between");
      line.appendChild(textEl("span", "mono", modelId));
      const rm = button("Remove", "ghost", () => removeModelFromPool(pool.name, modelId));
      rm.setAttribute("aria-label", `Remove ${modelId} from pool ${pool.name}`);
      line.appendChild(rm);
      list.appendChild(line);
    });
    body.appendChild(list);
  }

  const addModel = el("div", "row");
  const modelSelect = el("select");
  modelSelect.setAttribute("aria-label", `Add a model to pool ${pool.name}`);
  const modelBlank = el("option");
  modelBlank.value = "";
  modelBlank.textContent = "Add model…";
  modelSelect.appendChild(modelBlank);
  modelsCatalogIds()
    .filter((id) => !pool.models.includes(id))
    .forEach((id) => {
      const opt = el("option");
      opt.value = id;
      opt.textContent = id;
      modelSelect.appendChild(opt);
    });
  modelSelect.onchange = () => {
    const id = modelSelect.value;
    modelSelect.value = "";
    if (id) addModelToPool(pool.name, id);
  };
  addModel.appendChild(modelSelect);
  body.appendChild(addModel);

  // Mockup 3a offers "any node serving this model id" and "match a pattern"
  // as alternatives to the explicit list. ModelPool has no membership mode.
  body.appendChild(textEl("div", "field-label", "Membership rules"));
  body.appendChild(
    textEl(
      "p",
      "empty",
      "Auto-join rules (any node serving this model id, or a glob pattern) are not " +
        "yet available — membership is the explicit host list above."
    )
  );
}

function modelsAdmissionPanel(root) {
  const body = panel(root, "Admission checks", "a node must pass these to receive traffic");
  body.appendChild(
    textEl(
      "p",
      "empty",
      "Per-pool admission checks — minimum context window, p50 latency ceiling, " +
        "uniform quantisation, eject/re-admit after N probes — are not yet available. " +
        "A host either resolves and serves a pool model, or it does not."
    )
  );
  // The nearest real knobs are agent-wide, not per pool. Shown read-only so
  // nobody reads the empty state as "nothing governs failures at all".
  const routing = asObject(state.configDraft?.routing);
  const line = el("div", "inset stack");
  line.appendChild(textEl("div", "field-label", "Agent-wide equivalents (Routing page)"));
  line.appendChild(
    textEl(
      "div",
      "muted",
      `max_backend_failures ${routing.max_backend_failures ?? "—"} · ` +
        `offline_retry_s ${routing.offline_retry_s ?? "—"} · ` +
        `health_ttl_s ${routing.health_ttl_s ?? "—"}`
    )
  );
  line.appendChild(button("Open Routing", "secondary", () => navigate("routing")));
  body.appendChild(line);
}

function modelsMembersPanel(root, pool) {
  const members = modelsPoolMembers(pool);
  const body = panel(root, "Members", `${members.length} resolved host(s)`);
  if (!members.length) {
    body.appendChild(
      textEl("p", "empty", "No host ref resolves to a live backend, so nothing can be routed here.")
    );
    return;
  }
  const total = modelsRoutedTotal(members);
  const template = "1.4fr .9fr .7fr 1.2fr .8fr auto";
  const t = dataTable(
    ["Node", "Backend", "Pool models", "Share of routed", "State", ""],
    template
  );
  members.forEach((b) => {
    const rm = button("Remove", "ghost", () => {
      // Remove the host ref that matched this backend, not the backend id —
      // the pool may name it by agent id or base URL.
      const ref = pool.hosts.find((h) => modelsBackendMatchesHostRef(b, h));
      if (ref) modelsRemoveHostFromPool(pool.name, ref);
    });
    rm.setAttribute("aria-label", `Remove ${modelsNodeLabel(b).name} from pool ${pool.name}`);
    t.addRow([
      modelsNodeCell(b),
      textEl("div", "mono muted", PROVIDER_LABELS[b.provider] || b.provider || "custom"),
      modelsServedCell(b, pool),
      modelsShareCell(b, total),
      modelsStateCell(b),
      rm,
    ]);
  });
  t.addFoot(
    textEl(
      "span",
      "muted",
      "Share is the cumulative routed-request count per backend since the agent " +
        "started. Per-member weights and pinning are not yet available."
    )
  );
  body.appendChild(t.table);
}

function modelsBalancingPanel(root) {
  const strategy = state.status?.routing_strategy || state.configDraft?.routing?.default_strategy;
  const body = panel(root, "Balancing", "agent-wide");
  const line = el("div", "inset row-between");
  line.appendChild(textEl("span", "", "Strategy in force"));
  line.appendChild(textEl("span", "mono", strategy || "—"));
  body.appendChild(line);
  body.appendChild(
    textEl(
      "p",
      "panel-desc",
      "Every pool balances with the agent's routing.default_strategy. Per-pool " +
        "strategies and sticky sessions are not yet available."
    )
  );
  body.appendChild(button("Change on Routing", "secondary", () => navigate("routing")));
}

function modelsKeepWarmPanel(root) {
  const body = panel(root, "Keep warm", "cold starts cost seconds");
  body.appendChild(
    textEl(
      "p",
      "empty",
      "Warm residency is not yet available — the agent does not track which models " +
        "a backend currently holds in memory, and cannot pin one there."
    )
  );
}

function modelsMaintenancePanel(root, pool) {
  const draining = !!state.status?.draining;
  // The accent was a constant, so every pool detail page carried a permanent
  // orange border for a section where nothing was wrong. A destructive *button*
  // is not a warning state; actually being drained is.
  const body = panel(
    root,
    "Maintenance",
    draining ? pill("warn", "draining") : null,
    draining ? "accent-warn" : ""
  );
  body.appendChild(
    textEl(
      "p",
      "panel-desc",
      "Draining lets in-flight requests finish, then stops accepting new ones. " +
        "It applies to this agent as a whole — draining one member of the pool " +
        "remotely is not yet available."
    )
  );
  const actions = el("div", "stack");
  actions.appendChild(
    button(draining ? "Resume this agent" : "Drain this agent", "secondary", async () => {
      try {
        await api("/netllm/v1/admin/drain", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ draining: !draining }),
        });
        showToast(draining ? "Resumed" : "Draining — in-flight requests finish");
        await refresh();
      } catch (e) {
        showToast("Drain failed: " + e.message);
      }
    })
  );
  actions.appendChild(button("Re-scan providers", "secondary", () => runDiscover()));
  actions.appendChild(
    button("Delete pool", "danger", () => {
      openSheet(`Delete pool ${pool.name}?`, (sheet, close) => {
        sheet.appendChild(
          textEl(
            "p",
            "panel-desc",
            "The pool is removed from the staged config. Press Save in the toolbar " +
              "to persist the deletion."
          )
        );
        const row = el("div", "sheet-actions");
        row.appendChild(button("Cancel", "secondary", close));
        row.appendChild(
          button("Delete", "danger", () => {
            close();
            modelsDeletePool(pool.name);
          })
        );
        sheet.appendChild(row);
      });
    })
  );
  body.appendChild(actions);
  body.appendChild(
    textEl(
      "p",
      "panel-desc",
      "Rebalance-now and request simulation are not yet available — there is no " +
        "endpoint that replays or previews routing decisions."
    )
  );
}

function renderPoolDetail(root, pool) {
  modelsBreadcrumb(root, pool);
  const members = modelsPoolMembers(pool);
  const online = members.filter((b) => b.health?.status === "online").length;
  const reason = modelsPoolInactiveReason(pool);

  const aside = el("div", "row");
  aside.appendChild(reason ? pill("warn", reason) : pill("ok", "active"));
  const save = button("Save pool", "", () => {
    saveConfig().then(() => render());
  });
  save.disabled = !state.dirty;
  aside.appendChild(save);
  pageHeader(
    root,
    pool.name,
    `${members.length} member${members.length === 1 ? "" : "s"} · ${online} online · ` +
      `${pool.models.length} model${pool.models.length === 1 ? "" : "s"} · ` +
      `balancing by ${state.status?.routing_strategy || "the agent default"}`,
    aside
  );

  const cols = el("div", "grid-2");
  // Design 3a is a wide main column with a 340px sidebar; .grid-2's auto-fit
  // template would split it evenly.
  cols.style.gridTemplateColumns = "minmax(0, 1fr) minmax(280px, 340px)";
  // .stack, not a bare div: spacing between stacked boxes is owned by the
  // container (see the spacing scale in dashboard.css). A grid cell built as
  // an unclassed <div> matches neither `.page.active` nor `.stack`, so the
  // three panels in each column butted together with a 0px gap.
  const main = el("div", "stack");
  const side = el("div", "stack");
  cols.append(main, side);
  root.appendChild(cols);

  modelsMembershipPanel(main, pool);
  modelsAdmissionPanel(main);
  modelsMembersPanel(main, pool);
  modelsBalancingPanel(side);
  modelsKeepWarmPanel(side);
  modelsMaintenancePanel(side, pool);
}

/* ---------------- entry point ---------------- */

function renderModelsPage(root) {
  const pools = modelsPoolSummaries();
  const open = state.openPoolId ? pools.find((p) => p.name === state.openPoolId) : null;
  if (state.openPoolId && !open) {
    // The pool was deleted (or the draft reloaded) while it was open.
    state.openPoolId = null;
  }
  if (open) {
    renderPoolDetail(root, open);
    return;
  }

  pageHeader(root, "Models & pools", modelsSubtitle(pools), modelsHeaderAside());

  if (modelsViewMode === "nodes") {
    modelsNodesView(root);
    return;
  }
  if (modelsViewMode === "aliases") {
    modelsAliasesView(root);
    return;
  }
  if (!modelsBackendRows().length && !pools.length) {
    modelsEmptyCatalogNote(root);
    return;
  }
  modelsPoolsView(root, pools);
}

registerPage("models", renderModelsPage);
