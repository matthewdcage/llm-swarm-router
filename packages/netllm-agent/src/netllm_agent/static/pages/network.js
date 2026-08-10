/* Network page — design 1e.
 *
 * The old Agent, Discovery and Swarm tabs merged behind one jump rail.
 * Presentation is the only thing that changed: every config control those
 * three renderers exposed still exists here, because the Axis D parity kit
 * (tests/conformance/kit_config_surfaces.py) fails by field name when a
 * config field loses its dashboard control. Section ids match the design's
 * rail exactly.
 *
 * Everything in this file is prefixed `network` — page modules share one
 * global scope, so an unprefixed helper here would collide with another
 * page's.
 */

// Wildcard binds, i.e. "other machines can reach this agent". `[::]` is the
// IPv6 form AgentConfig._validate_listen accepts. Aliased to the core set
// rather than restated: Home's masthead asks the same question about the same
// value, and two lists of wildcards would be one list too many.
const NETWORK_LAN_HOSTS = LAN_WILDCARD_HOSTS;
const NETWORK_LOCAL_HOST = "127.0.0.1";

/* ---------------- listen address ---------------- */

// parseListenAddr / localAgentUrl / swarmJoinCommand live in dashboard.js:
// this page edits `agent.listen`, but Home now reads the same derived values,
// and a second copy of the wildcard-bind fallback is exactly the kind of
// duplicate that drifts.
const networkParseListen = parseListenAddr;

function networkSetListen(host, port) {
  state.configDraft.agent.listen = `${host}:${port}`;
  markDirty();
}

function networkListenChanged() {
  const saved = state.config?.agent?.listen;
  const draft = state.configDraft?.agent?.listen;
  return !!saved && !!draft && saved !== draft;
}

/** Best-effort URL another machine would use to reach this agent. */
const networkLocalAgentUrl = localAgentUrl;

/* ---------------- small builders ---------------- */

function networkSegmented(options, value, onPick) {
  const wrap = el("div", "segmented");
  options.forEach((opt) => {
    const b = textEl("button", "", opt.label);
    b.type = "button";
    b.setAttribute("aria-pressed", String(opt.value === value));
    b.onclick = () => onPick(opt.value);
    wrap.appendChild(b);
  });
  return wrap;
}

function networkFieldGroup(label, control, help, helpCls) {
  const group = el("div", "field");
  group.appendChild(textEl("div", "field-label", label));
  group.appendChild(control);
  if (help) group.appendChild(textEl("div", helpCls || "field-help", help));
  return group;
}

function networkMetaItem(label, value) {
  return networkFieldGroup(label, textEl("div", "mono muted", value || "—"));
}

function networkTextInput(value, placeholder, onInput) {
  const input = el("input", "mono");
  input.type = "text";
  input.value = value == null ? "" : String(value);
  if (placeholder) input.placeholder = placeholder;
  input.spellcheck = false;
  input.autocomplete = "off";
  input.oninput = () => onInput(input.value);
  return input;
}

function networkRemoveButton(label, onClick) {
  const b = button("✕", "ghost small", onClick);
  b.setAttribute("aria-label", label);
  b.title = label;
  return b;
}

/* ---------------- schema-driven fields ---------------- */

function networkSchemaFieldSpec(sectionKey, name) {
  // A malformed /config/schema can make `fields` a scalar or fill it with
  // nulls; either breaks .find(). Falling through to null makes every caller
  // use its hand-rolled fallback field, which is the right degraded mode.
  const fields = asArray(state.configSchema?.sections?.[sectionKey]?.fields);
  return fields.filter(Boolean).find((f) => f.name === name) || null;
}

/**
 * Render one config field through schema-form.js, with a hand-rolled
 * fallback.
 *
 * The fallback is not belt-and-braces: `state.configSchema` is null against
 * an agent whose admin API is unreachable or that predates the schema
 * endpoint, and a field silently vanishing there is a real parity loss (the
 * user can no longer edit heartbeat_interval_s at all), not a cosmetic one.
 * Detection is "did the renderer append anything", which also covers a
 * schema whose section simply doesn't carry the field.
 */
function networkConfigField(root, sectionKey, name, overrides) {
  const before = root.childNodes.length;
  const spec = networkSchemaFieldSpec(sectionKey, name);
  if (spec && typeof renderSchemaField === "function") {
    try {
      // schema-form.js's append form takes the field *spec*, not its name.
      renderSchemaField(root, sectionKey, spec, overrides || {});
    } catch (e) {
      console.warn(`network: schema field ${sectionKey}.${name} failed`, e);
      while (root.childNodes.length > before) root.lastChild.remove();
    }
  }
  if (root.childNodes.length > before) return;
  networkFallbackField(root, sectionKey, name, overrides || {});
}

/** Minimal editor bound straight to the draft, used when the schema is absent. */
function networkFallbackField(root, sectionKey, name, overrides) {
  const draft = state.configDraft[sectionKey];
  if (!draft) return;
  const label = overrides.label || name;
  const current = draft[name];

  if (overrides.bool || typeof current === "boolean") {
    root.appendChild(
      switchRow(label, current, (v) => {
        draft[name] = v;
        markDirty();
      })
    );
    return;
  }

  if (Array.isArray(current) || overrides.list) {
    // Comma-separated is a degraded-mode editor for a list[str]; the real
    // per-row widget lives in schema-form.js.
    const input = networkTextInput(asArray(current).join(", "), overrides.placeholder, (v) => {
      draft[name] = v
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      markDirty();
    });
    root.appendChild(
      networkFieldGroup(label, input, overrides.help || "Comma-separated.")
    );
    return;
  }

  const input = el("input", "mono");
  input.type = overrides.step || typeof current === "number" ? "number" : "text";
  if (overrides.step) input.step = overrides.step;
  input.value = current == null ? "" : String(current);
  input.oninput = () => {
    draft[name] =
      input.type === "number" ? Number(input.value) || 0 : input.value;
    markDirty();
  };
  root.appendChild(networkFieldGroup(label, input, overrides.help));
}

/* ---------------- discovery helpers ---------------- */

function networkProviderSpec(providerId) {
  // The registry global is filled from /local-providers; guard the read as well
  // as the write so a poisoned roster cannot break every later render.
  return asArray(LOCAL_PROVIDER_REGISTRY).filter(Boolean).find((r) => r.id === providerId) || null;
}

function networkProviderLabel(providerId) {
  return PROVIDER_LABELS[providerId] || networkProviderSpec(providerId)?.short_label || providerId;
}

/** Live backends the agent actually has for this provider, from /status. */
function networkBackendsForProvider(providerId) {
  return asArray(state.status?.backends)
    .filter(Boolean)
    .filter((b) => b.provider === providerId);
}

function networkStatusBackendForUrl(url) {
  const norm = normalizeDiscoveryUrl(url);
  if (!norm) return null;
  return (
    asArray(state.status?.backends)
      .filter(Boolean)
      .find((b) => normalizeDiscoveryUrl(b.base_url) === norm) || null
  );
}

/** Detection state node for one provider card. */
function networkProviderDetection(providerId, enabled) {
  const spec = networkProviderSpec(providerId);
  if (!enabled) return textEl("span", "field-help", "off");

  const rows = networkBackendsForProvider(providerId);
  const online = rows.filter((b) => b.enabled !== false && b.health?.status !== "offline");
  if (online.length) {
    const models = online.reduce((sum, b) => sum + (b.health?.model_count || 0), 0);
    return textEl(
      "span",
      "field-help text-ok",
      models ? `found · ${models} model${models === 1 ? "" : "s"}` : "found"
    );
  }
  if (rows.length) return textEl("span", "field-help text-warn", "configured · offline");
  // Registry platform allowlist explains the common "will never be found
  // here" case (oMLX on Linux) without guessing at the host's OS.
  if (spec?.platforms?.length === 1 && spec.platforms[0] === "darwin") {
    return textEl("span", "field-help", "macOS only");
  }
  return textEl("span", "field-help", "not on this host");
}

function networkDefaultUrlHint(providerId) {
  const spec = networkProviderSpec(providerId);
  const port = spec?.default_ports?.[0];
  return port ? `default http://127.0.0.1:${port}/v1` : "no default port";
}

function networkReachability(url) {
  const backend = networkStatusBackendForUrl(url);
  if (!url.trim()) return textEl("span", "field-help", "");
  if (!backend) return textEl("span", "field-help", "not probed");
  const status = backend.health?.status;
  if (status === "offline") return textEl("span", "field-help text-warn", "unreachable");
  if (!status || status === "unknown") return textEl("span", "field-help", "not probed");
  return textEl("span", "field-help text-ok", "reachable");
}

/* ---------------- sections ---------------- */

function networkThisNodeSection(root) {
  const agent = state.configDraft.agent;
  const { host, port } = networkParseListen(agent.listen);
  const lan = NETWORK_LAN_HOSTS.has(host);

  const note = textEl(
    "div",
    "panel-note mono",
    `agent_id ${agent.agent_id || "—"} · ${agent.hostname || "unknown host"}`
  );
  const body = panel(root, "This node", note);

  const grid = el("div", "field-grid");

  grid.appendChild(
    networkFieldGroup(
      "Reachable on",
      networkSegmented(
        [
          { value: "0.0.0.0", label: "LAN 0.0.0.0" },
          { value: NETWORK_LOCAL_HOST, label: "Local only" },
        ],
        lan ? "0.0.0.0" : host,
        (v) => {
          networkSetListen(v, port);
          render();
        }
      ),
      lan
        ? "Other machines can reach this agent"
        : "Only this machine can reach this agent",
      lan ? "field-help text-warn" : "field-help"
    )
  );

  const portInput = el("input", "mono");
  portInput.type = "number";
  portInput.min = "1";
  portInput.max = "65535";
  portInput.value = String(port);
  portInput.setAttribute("aria-label", "Listen port");
  portInput.onchange = () => networkSetListen(host, Number(portInput.value) || 11400);
  grid.appendChild(networkFieldGroup("Port", portInput));

  const role = agent.role || "peer";
  grid.appendChild(
    networkFieldGroup(
      "Role in mesh",
      networkSegmented(
        ROLES.map((r) => ({ value: r, label: r })),
        role,
        (v) => {
          state.configDraft.agent.role = v;
          markDirty();
          render();
        }
      ),
      role === "gateway"
        ? "Gateway coordinates and can accept peers' spillover"
        : "Peer serves its own backends and spills over to the mesh"
    )
  );

  grid.appendChild(
    networkFieldGroup(
      "Advertise on LAN",
      switchRow("mDNS", !!agent.advertise, (v) => {
        state.configDraft.agent.advertise = v;
        markDirty();
      })
    )
  );

  // agent.max_concurrency had no web control before the Axis D phase closed
  // that gap (docs/extending/08-control-parity.md) — keep it visible.
  const concurrency = el("div", "field");
  networkConfigField(concurrency, "agent", "max_concurrency", {
    label: "Max concurrency (this machine)",
    help: "Self-declared ceiling on this machine's own concurrent requests, broadcast to peers so least_load/local_spillover selection respects it. 0 = unlimited.",
  });
  grid.appendChild(concurrency);

  body.appendChild(grid);

  const meta = el("div", "field-grid");
  meta.append(
    networkMetaItem("Agent ID", agent.agent_id),
    networkMetaItem("Hostname", agent.hostname),
    networkMetaItem("Listen", agent.listen)
  );
  body.appendChild(meta);
  body.appendChild(
    textEl("p", "field-help", "Listen address and port changes apply after Save + restart agent.")
  );
}

function networkProviderCard(providerId) {
  const discovery = state.configDraft.discovery;
  const enabled = asArray(discovery.providers).includes(providerId);
  const card = el("div", "inset");

  const head = el("div", "row");
  head.appendChild(
    switchRow(networkProviderLabel(providerId), enabled, (v) => {
      const list = new Set(asArray(discovery.providers));
      if (v) list.add(providerId);
      else list.delete(providerId);
      state.configDraft.discovery.providers = [...list];
      markDirty();
      render();
    })
  );
  head.appendChild(el("div", "spacer"));
  head.appendChild(networkProviderDetection(providerId, enabled));
  card.appendChild(head);

  // discovery.provider_urls[<id>] — pinned non-default ports, tried before
  // the automatic port scan.
  if (!discovery.provider_urls || typeof discovery.provider_urls !== "object") {
    state.configDraft.discovery.provider_urls = {};
  }
  // A stored scalar (provider_urls.ollama = "http://x") is not editable as a
  // list; render the empty hint instead of throwing. Nothing is deleted from
  // the draft, so an unsaved page never silently drops the value.
  const urls = asArray(state.configDraft.discovery.provider_urls[providerId]);

  if (!urls.length) {
    card.appendChild(textEl("div", "field-help mono", networkDefaultUrlHint(providerId)));
  }
  urls.forEach((url, index) => {
    const row = el("div", "row");
    row.appendChild(
      networkTextInput(url, "http://127.0.0.1:8080/v1", (v) => {
        state.configDraft.discovery.provider_urls[providerId][index] = v;
        markDirty();
      })
    );
    row.appendChild(networkReachability(url));
    row.appendChild(
      networkRemoveButton(`Remove pinned URL ${url || index + 1}`, () => {
        const next = [...state.configDraft.discovery.provider_urls[providerId]];
        next.splice(index, 1);
        if (next.length) state.configDraft.discovery.provider_urls[providerId] = next;
        else delete state.configDraft.discovery.provider_urls[providerId];
        markDirty();
        render();
      })
    );
    card.appendChild(row);
  });

  const add = button(`+ Pin a URL`, "secondary small", () => {
    const map = state.configDraft.discovery.provider_urls;
    map[providerId] = [...asArray(map[providerId]), ""];
    markDirty();
    render();
  });
  add.setAttribute("aria-label", `Pin a URL for ${networkProviderLabel(providerId)}`);
  card.appendChild(add);
  return card;
}

function networkLocalProvidersSection(root) {
  const body = panel(root, "Local providers", "scanned on start");
  body.appendChild(
    textEl(
      "p",
      "panel-desc",
      "Which providers to look for on this machine, and any non-default ports to try first."
    )
  );
  const grid = el("div", "grid-2");
  asArray(PROVIDERS).filter(Boolean).forEach((p) => grid.appendChild(networkProviderCard(p)));
  if (!grid.childNodes.length) {
    body.appendChild(textEl("p", "empty", "No local provider registry available."));
  } else {
    body.appendChild(grid);
  }
}

function networkCustomServersSection(root) {
  const discovery = state.configDraft.discovery;
  const addBtn = button("+ Add server", "secondary small", () => {
    state.configDraft.discovery.custom_endpoints = [
      ...asArray(state.configDraft.discovery.custom_endpoints),
      "",
    ];
    markDirty();
    render();
  });
  const body = panel(root, "Custom OpenAI-compatible servers", addBtn);
  body.appendChild(
    textEl(
      "p",
      "panel-desc",
      "Any base URL the agent should route to — vLLM, custom gateways, a server on another host."
    )
  );

  const endpoints = asArray(discovery.custom_endpoints);
  if (!endpoints.length) {
    body.appendChild(textEl("p", "empty", "No custom servers — everything comes from provider discovery."));
    return;
  }
  endpoints.forEach((url, index) => {
    const row = el("div", "row");
    row.appendChild(
      networkTextInput(url, "http://10.0.0.44:8000/v1", (v) => {
        state.configDraft.discovery.custom_endpoints[index] = v;
        markDirty();
      })
    );
    row.appendChild(networkReachability(url));
    row.appendChild(
      networkRemoveButton(`Remove server ${url || index + 1}`, () => {
        const next = [...state.configDraft.discovery.custom_endpoints];
        next.splice(index, 1);
        state.configDraft.discovery.custom_endpoints = next;
        markDirty();
        render();
      })
    );
    body.appendChild(row);
  });
}

/**
 * discovery.ignored_urls — the denylist, and the only place an entry can be
 * inspected and removed without hand-editing config.toml.
 *
 * It exists because the alternative was worse: the only way to silence a
 * discovered-but-unwanted endpoint used to be pinning it as a
 * [[routing.backends]] override and switching that off, which converts a
 * *discovered* endpoint into a hand-authored config row — the design error
 * docs/ui-redesign-feature-spec.md §5 calls out by name.
 *
 * A row whose URL is also in routing.backends is rendered as overruled
 * rather than hidden: the entry really is stored, it really does nothing,
 * and the precedence rule (explicit configuration wins) is easier to trust
 * when the surface says it out loud. Mirrors
 * netllm_core.backend_credentials.ignored_url_keys.
 */
function networkIgnoredEndpointsSection(root) {
  const addBtn = button("+ Ignore a URL", "secondary small", () => {
    state.configDraft.discovery.ignored_urls = [
      ...asArray(state.configDraft.discovery.ignored_urls),
      "",
    ];
    markDirty();
    render();
  });
  const body = panel(root, "Ignored endpoints", addBtn);
  body.appendChild(
    textEl(
      "p",
      "panel-desc",
      "Base URLs discovery must never register — something else on a provider's default port, a server that is not yours. Nothing here is probed, routed to, or listed under Backends."
    )
  );

  const entries = asArray(state.configDraft.discovery.ignored_urls);
  if (!entries.length) {
    body.appendChild(
      textEl("p", "empty", "Nothing ignored — every discovered endpoint is offered.")
    );
    return;
  }
  entries.forEach((url, index) => {
    const row = el("div", "row");
    row.appendChild(
      networkTextInput(url, "http://127.0.0.1:8000", (v) => {
        state.configDraft.discovery.ignored_urls[index] = v;
        markDirty();
      })
    );
    row.appendChild(
      ignoreOverruledByBackend(url)
        ? textEl("span", "field-help text-warn", "overruled by a pinned backend")
        : textEl("span", "field-help", url.trim() ? "ignored" : "")
    );
    row.appendChild(
      networkRemoveButton(`Stop ignoring ${url || index + 1}`, () => {
        const next = [...state.configDraft.discovery.ignored_urls];
        next.splice(index, 1);
        state.configDraft.discovery.ignored_urls = next;
        markDirty();
        render();
      })
    );
    body.appendChild(row);
  });
  body.appendChild(
    textEl(
      "p",
      "field-help",
      "Matched after normalisation, so http://host:8000, http://host:8000/ and http://host:8000/v1 are one entry. A URL pinned under Backends → Manual overrides wins: the override keeps working and the entry above does nothing."
    )
  );
}

function networkSwarmDiscoverySection(root) {
  const swarm = state.configDraft.swarm;
  // runPeersScan only re-renders when the Peers page is showing, so the
  // known-agents table below needs its own repaint once results land.
  const scan = button("Scan LAN now", "secondary small", () => {
    Promise.resolve(runPeersScan(false)).then(() => {
      if (state.page === "network") render();
    });
  });
  const body = panel(root, "Swarm discovery", scan);
  body.appendChild(
    textEl(
      "p",
      "panel-desc",
      "How this agent finds other netllm agents on the LAN, and how quickly it forgets one that goes quiet."
    )
  );

  const grid = el("div", "field-grid");
  grid.appendChild(
    switchRow("mDNS discovery", swarm.mdns !== false, (v) => {
      state.configDraft.swarm.mdns = v;
      markDirty();
    })
  );
  grid.appendChild(
    switchRow("Subnet scan at startup", !!swarm.subnet_scan, (v) => {
      state.configDraft.swarm.subnet_scan = v;
      markDirty();
    })
  );
  body.appendChild(grid);

  const fields = el("div", "field-grid");
  networkConfigField(fields, "swarm", "heartbeat_interval_s", {
    label: "Heartbeat (s)",
    step: "0.5",
  });
  networkConfigField(fields, "swarm", "peer_stale_after_s", {
    label: "Drop peer after (s)",
    step: "0.5",
  });
  networkConfigField(fields, "swarm", "rediscover_interval_s", {
    label: "Re-discover every (s)",
    step: "0.5",
  });
  body.appendChild(fields);

  networkConfigField(body, "swarm", "subnet_cidrs", {
    label: "Subnet CIDRs",
    placeholder: "10.0.0.0/24",
    itemLabel: "CIDR",
    list: true,
    help: "Left empty, the scan derives the CIDRs from this machine's interfaces.",
  });
}

/**
 * Config static peers merged with whatever the mesh currently sees.
 *
 * Two different things share the design's one table: `swarm.peers` is
 * durable config (editable, removable), while /status peers and a LAN scan
 * are live observations with no config row to delete.
 */
function networkPeerRows() {
  const keyOf = (u) => String(u || "").trim().replace(/\/+$/, "");
  const rows = new Map();
  asArray(state.configDraft.swarm.peers).forEach((url, index) => {
    // Blank rows are "+ Add agent by URL" waiting to be typed into — key them
    // by index so two of them don't collapse into one.
    rows.set(keyOf(url) || `#${index}`, { url, index, pinned: true, live: null });
  });
  [...asArray(state.status?.peers), ...asArray(state.lanPeers)].forEach((p) => {
    if (p?.self) return;
    const url = p?.listen_url || p?.url || "";
    const key = keyOf(url);
    if (!key) return;
    const existing = rows.get(key);
    if (existing) {
      existing.live = existing.live || p;
      return;
    }
    rows.set(key, { url, index: -1, pinned: false, live: p });
  });
  return [...rows.values()];
}

function networkKnownAgentsSection(root) {
  const rows = networkPeerRows();
  const body = panel(root, "Known agents", `${rows.length} known`);
  body.appendChild(
    textEl(
      "p",
      "panel-desc",
      "Pinned agents are saved in config and dialled directly — used when mDNS is blocked. Discovered agents come from mDNS or a LAN scan and are not stored."
    )
  );

  const template = "1.2fr 1.5fr .7fr .9fr .7fr";
  const table = dataTable(["Agent", "Address", "Role", "Source", ""], template);

  rows.forEach((row) => {
    const live = row.live;
    const name = el("div", "row");
    name.appendChild(statusDot(live ? "ok" : row.pinned ? "warn" : ""));
    name.appendChild(
      textEl("span", "", live?.hostname || live?.agent_id || (row.pinned ? "not seen" : "unknown"))
    );

    // A pinned row is the config list editor: the address stays editable so
    // a moved agent can be corrected in place, as the old list editor allowed.
    const address = row.pinned
      ? networkTextInput(row.url, "http://10.0.0.32:11400", (v) => {
          state.configDraft.swarm.peers[row.index] = v;
          markDirty();
        })
      : textEl("div", "mono muted", row.url);

    const action = el("div", "row");
    if (row.pinned) {
      action.appendChild(
        button("Forget", "danger small", () => {
          const next = [...asArray(state.configDraft.swarm.peers)];
          next.splice(row.index, 1);
          state.configDraft.swarm.peers = next;
          markDirty();
          render();
        })
      );
    } else {
      action.appendChild(
        button("Pin", "secondary small", () => {
          state.configDraft.swarm.peers = [
            ...asArray(state.configDraft.swarm.peers),
            row.url,
          ];
          markDirty();
          render();
        })
      );
    }

    table.addRow([
      name,
      address,
      live?.role || (row.pinned ? "—" : "peer"),
      row.pinned ? (live ? "pinned · live" : "pinned") : "discovered",
      action,
    ]);
  });

  if (!rows.length) {
    table.addFoot("No agents yet — add one by URL, or run a LAN scan above.");
  }

  const addRow = el("div", "row");
  addRow.appendChild(
    button("+ Add agent by URL", "secondary small", () => {
      state.configDraft.swarm.peers = [...asArray(state.configDraft.swarm.peers), ""];
      markDirty();
      render();
    })
  );
  addRow.appendChild(textEl("span", "field-help", "Used when mDNS is blocked."));
  table.addFoot(addRow);

  body.appendChild(table.table);
}

function networkSecuritySection(root) {
  const tokenSet = clusterTokenSet();
  const body = panel(root, "Security", tokenSet ? "cluster token set" : "open LAN");

  const statusLine = textEl(
    "p",
    tokenSet ? "field-help text-ok" : "field-help text-warn",
    tokenSet
      ? "Secured — peers must present the cluster token to join."
      : "No cluster token — any agent on this LAN can join and be routed to."
  );
  body.appendChild(statusLine);

  // Write-only, exactly as buildConfigPatch expects: the draft key is
  // `_cluster_token` (buildConfigPatch passes that mapping explicitly for
  // swarm.cluster_token), NOT the `_pending_<name>` default. Renaming it
  // would silently stop the token from ever being saved.
  const tokenInput = el("input");
  tokenInput.type = "password";
  tokenInput.autocomplete = "off";
  tokenInput.spellcheck = false;
  tokenInput.placeholder = tokenSet
    ? "•••••••• (unchanged if left blank)"
    : "Set a cluster token";
  tokenInput.oninput = () => {
    state.configDraft.swarm._cluster_token = tokenInput.value;
    markDirty();
  };
  const tokenId = "network-cluster-token";
  tokenInput.id = tokenId;
  const tokenField = el("div", "field");
  const tokenLabel = textEl(
    "label",
    "field-label",
    tokenSet ? "Cluster token (secured)" : "Cluster token (open trusted LAN)"
  );
  tokenLabel.htmlFor = tokenId;
  tokenField.append(
    tokenLabel,
    tokenInput,
    textEl(
      "div",
      "field-help",
      "Write-only — the agent never returns the stored value. Every machine in the swarm must use the same token, so change it everywhere in one pass."
    )
  );
  body.appendChild(tokenField);

  const actions = el("div", "row");
  actions.appendChild(button("Copy join command", "secondary small", networkCopyJoinCommand));
  body.appendChild(actions);

  networkConfigField(body, "swarm", "require_token_for_inference", {
    label: "Require token for inference",
    bool: true,
    help: "When set (and a cluster token exists), /v1/* requires the Bearer token from non-local clients.",
  });

  // Per-endpoint API keys for discovered/pinned servers — the old Discovery
  // tab's credentials block, unchanged (dashboard.js owns the draft shape).
  renderDiscoveryCredentialsSection(body);
}

/* ---------------- join sheet ---------------- */

// Built in dashboard.js (quoting, and the "never render the token" rule, are
// the same wherever the command is offered). Home's masthead copies the very
// same string.
const networkJoinCommand = swarmJoinCommand;

function networkCopyJoinCommand() {
  copyText(networkJoinCommand(), "Join command copied");
}

function networkOpenJoinSheet() {
  openSheet("Invite a machine", (sheet, close) => {
    sheet.appendChild(
      textEl(
        "p",
        "field-help",
        "Run this on the machine you want to add. It needs to reach this agent over the LAN."
      )
    );

    const cmd = el("div", "inset");
    cmd.appendChild(codeEl(networkJoinCommand()));
    sheet.appendChild(cmd);

    const { host } = networkParseListen(state.configDraft?.agent?.listen);
    if (!NETWORK_LAN_HOSTS.has(host)) {
      sheet.appendChild(
        textEl(
          "p",
          "field-help text-warn",
          "This agent is bound to a local-only address — switch “Reachable on” to LAN and restart before another machine can join."
        )
      );
    }
    const tokenSet = clusterTokenSet();
    sheet.appendChild(
      textEl(
        "p",
        "field-help",
        tokenSet
          ? "Substitute your cluster token — netllm never displays the stored value."
          : "This swarm has no cluster token: any agent on the LAN can join. Set one under Security to restrict it."
      )
    );

    const actions = el("div", "sheet-actions");
    actions.appendChild(button("Copy command", "", networkCopyJoinCommand));
    actions.appendChild(button("Close", "secondary", close));
    sheet.appendChild(actions);
  });
}

function networkInviteAside() {
  const card = el("div", "panel");
  const head = el("div", "panel-head");
  head.appendChild(textEl("div", "panel-title", "Invite a machine"));
  card.appendChild(head);
  const body = el("div", "panel-body");
  body.appendChild(
    textEl("p", "field-help", "Share a one-line join command with the other machine.")
  );
  body.appendChild(button("Join swarm…", "", networkOpenJoinSheet));
  card.appendChild(body);
  return card;
}

/* ---------------- page ---------------- */

function renderNetworkPage(root) {
  // The header aside is where the design puts the restart warning. Save and
  // Revert stay in the top bar chrome, which owns the dirty state for every
  // page — duplicating Save here would give it two disabled-states to track.
  const aside = networkListenChanged()
    ? textEl("span", "text-warn", "Listen address changed — restart needed after save")
    : null;
  pageHeader(
    root,
    "Network",
    "Everything that was on Agent, Discovery and Swarm — one page, three sections",
    aside
  );

  if (!state.configDraft) {
    root.appendChild(
      textEl("p", "empty", "Config unavailable — the agent's admin API is not reachable.")
    );
    return;
  }
  // A defaults-only draft (admin API down) can still be missing a section.
  ["agent", "discovery", "swarm"].forEach((k) => {
    if (!state.configDraft[k] || typeof state.configDraft[k] !== "object") {
      state.configDraft[k] = {};
    }
  });

  railLayout(
    root,
    [
      { id: "this-node", label: "This node", render: networkThisNodeSection },
      { id: "local-providers", label: "Local providers", render: networkLocalProvidersSection },
      { id: "custom-servers", label: "Custom servers", render: networkCustomServersSection },
      { id: "ignored-endpoints", label: "Ignored endpoints", render: networkIgnoredEndpointsSection },
      { id: "swarm-discovery", label: "Swarm discovery", render: networkSwarmDiscoverySection },
      { id: "known-agents", label: "Known agents", render: networkKnownAgentsSection },
      { id: "security", label: "Security", render: networkSecuritySection },
    ],
    networkInviteAside()
  );
}

registerPage("network", renderNetworkPage);
