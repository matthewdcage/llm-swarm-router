/* Cloud failover page — design 3b.
 *
 * Carries the whole [cloud] config subtree: the master switch, the fallback
 * direction, and one card per registry provider (enabled / region /
 * api_format / api_key / api_key_env / base_url / models).
 *
 * Two contracts this page must not break:
 *
 *   1. The provider id set is the SERVER's. It comes from
 *      state.configDraft.cloud.providers (config_summary always lists every
 *      registry provider) — CLOUD_PROVIDER_IDS_BOOTSTRAP is the
 *      admin-API-unreachable fallback only. No provider id literal appears
 *      in this file; scripts/check-registry-mirrors.py exists for that.
 *   2. API keys are write-only. A typed key lands on `_pending_api_key` and
 *      an untyped one is simply absent, so buildCloudPatch() omits the field
 *      and the stored key survives the save.
 */

// Only reached when GET /netllm/v1/config/schema is unavailable (older
// agent, or the admin API is gated). These are CloudFallbackMode's members,
// not provider ids.
const CLOUD_FALLBACK_MODES_BOOTSTRAP = ["cloud", "local", "none"];

const CLOUD_FALLBACK_HELP =
  "cloud = local first then cloud · local = cloud first then local · none = no automatic fallback.";

/* ---------------- draft access ---------------- */

// Normalizing shape is not a user edit, so it deliberately does not
// markDirty() — same contract as the old renderCloudTab().
function ensureCloudDraft() {
  if (!state.configDraft) state.configDraft = {};
  if (!state.configDraft.cloud) {
    state.configDraft.cloud = {
      enabled: true,
      fallback: "cloud",
      fallback_enabled: true,
      providers: {},
    };
  }
  const draft = state.configDraft.cloud;
  if (!draft.providers || typeof draft.providers !== "object") {
    draft.providers = {};
  }
  return draft;
}

function cloudProviderIds(draft) {
  const ids = Object.keys(draft.providers);
  return ids.length ? ids : CLOUD_PROVIDER_IDS_BOOTSTRAP;
}

/** Live per-provider display metadata (display_name, regions, api_key_set…). */
function cloudProviderSummary(pid) {
  return asObject(asObject(state.config?.cloud).providers?.[pid]);
}

function cloudProviderItemSchema() {
  return (
    asArray(state.configSchema?.sections?.cloud?.fields).find(
      (f) => asObject(f).name === "providers"
    )?.item_schema || null
  );
}

function cloudProviderFieldSpec(name) {
  return asArray(cloudProviderItemSchema()).find((f) => asObject(f).name === name) || null;
}

// The [cloud.providers.<id>] fields this page renders, in the order the card
// lays them out. Doubles as a drift guard: a writable item_schema field
// missing from this list is a control the server grew and this page would
// otherwise drop in silence, so it gets named in the Providers footer
// instead. (`auth` is knowingly absent — see
// tests/conformance/ledgers/control-parity.toml.)
const CLOUD_PROVIDER_RENDERED_FIELDS = [
  "enabled",
  "region",
  "api_format",
  "api_key",
  "api_key_env",
  "base_url",
  "models",
];

function cloudUnrenderedProviderFields() {
  return asArray(cloudProviderItemSchema())
    .map((f) => asObject(f))
    .filter(
      (f) => f.name && !f.read_only && !CLOUD_PROVIDER_RENDERED_FIELDS.includes(f.name)
    )
    .map((f) => f.name);
}

function ensureCloudProviderEntry(draft, pid) {
  if (!draft.providers[pid]) {
    draft.providers[pid] = {
      enabled: false,
      region: "",
      api_format: null,
      api_key_env: "",
      base_url: "",
      models: [],
    };
  }
  const entry = draft.providers[pid];
  if (!Array.isArray(entry.models)) entry.models = [];
  return entry;
}

function enabledCloudProviders(draft) {
  return cloudProviderIds(draft).filter((pid) => draft.providers[pid]?.enabled);
}

function rerenderCloud() {
  if (state.page === "cloud") render();
}

/* ---------------- request attribution ---------------- */

/**
 * Split the router's per-backend routed counters into cloud vs mesh.
 *
 * Backend.cloud_provider is the server's own marker for a row materialized
 * from [cloud.providers.<id>] — it is the only reliable cloud signal on the
 * wire (`local: false` is also true of every peer agent). Counters a live
 * backend no longer claims (a row dropped from the pool, or a request-scoped
 * legacy cloud inject) are reported separately rather than guessed into one
 * of the two buckets.
 */
function cloudRequestSplit() {
  const routed =
    state.telemetry?.router?.routed_requests || state.status?.routed_requests || null;
  if (!routed || typeof routed !== "object") return null;
  const byId = new Map(
    asArray(state.status?.backends).map((b) => [asObject(b).id, asObject(b)])
  );
  const split = { cloud: 0, mesh: 0, unattributed: 0 };
  Object.entries(asObject(routed)).forEach(([id, count]) => {
    const n = Number(count) || 0;
    const backend = byId.get(id);
    if (!backend) split.unattributed += n;
    else if (backend.cloud_provider) split.cloud += n;
    else split.mesh += n;
  });
  split.total = split.cloud + split.mesh + split.unattributed;
  return split;
}

/* ---------------- section-level scalars ---------------- */

/**
 * Render cloud's plain scalar fields through the shared schema form, showing
 * only `show`. Falls back to hand-built controls when the schema endpoint is
 * unavailable — without that, an offline dashboard would silently lose the
 * master switch rather than degrade.
 */
function cloudScalarFields(root, show) {
  // Array.isArray, not truthiness: a schema whose `fields` is a string is
  // "present" but unusable, and forEach/find on it throws. Falling through to
  // the hand-built controls below is the same degrade path as no schema.
  const fields = asArray(state.configSchema?.sections?.cloud?.fields).map((f) =>
    asObject(f)
  );
  const draft = ensureCloudDraft();
  if (fields.length && typeof renderSchemaForm === "function") {
    const overrides = {
      // providers gets its own cards below: the shape-only schema carries no
      // display name, region list or api_key_set.
      providers: { hidden: true },
      enabled: {
        label: "Cloud enabled (master switch)",
        help: "Off means no request can reach a cloud provider, whatever a policy says.",
      },
      fallback: { help: CLOUD_FALLBACK_HELP },
      fallback_enabled: {
        label: "Fallback enabled",
        help: "Turn off to require an explicit policy match instead of automatic failover.",
      },
    };
    fields.forEach((f) => {
      if (!show.includes(f.name)) overrides[f.name] = { hidden: true };
    });
    renderSchemaForm(root, "cloud", overrides);
    return;
  }

  if (show.includes("enabled")) {
    root.appendChild(
      switchRow("Cloud enabled (master switch)", draft.enabled !== false, (v) => {
        draft.enabled = v;
        markDirty();
        rerenderCloud();
      })
    );
  }
  if (show.includes("fallback_enabled")) {
    root.appendChild(
      switchRow("Fallback enabled", draft.fallback_enabled !== false, (v) => {
        draft.fallback_enabled = v;
        markDirty();
        rerenderCloud();
      })
    );
  }
  if (show.includes("fallback")) {
    const field = el("div", "field");
    const label = textEl("label", "field-label", "Fallback");
    const select = el("select");
    select.id = "cloud-fallback-select";
    label.htmlFor = select.id;
    CLOUD_FALLBACK_MODES_BOOTSTRAP.forEach((mode) => {
      const option = textEl("option", "", mode);
      option.value = mode;
      select.appendChild(option);
    });
    select.value = draft.fallback || "cloud";
    select.onchange = () => {
      draft.fallback = select.value;
      markDirty();
    };
    field.append(label, select, textEl("p", "field-help", CLOUD_FALLBACK_HELP));
    root.appendChild(field);
  }
}

/* ---------------- hero ---------------- */

function renderCloudHero(root, draft) {
  const split = cloudRequestSplit();
  const on = draft.enabled !== false;
  const reached = split ? split.cloud > 0 : false;
  const body = panel(root, null, null, reached ? "accent-warn" : "accent-ok");

  const row = el("div", "row");
  row.appendChild(
    reached
      ? pill("warn", `${formatCompactCount(split.cloud)} cloud`)
      : pill("ok", on ? "armed" : "off")
  );

  const copy = el("div");
  let headline;
  if (!split) {
    headline = "Cloud routing counters are unavailable — the agent is unreachable.";
  } else if (!reached) {
    headline = "No request has left your mesh since this agent started.";
  } else {
    headline = `${split.cloud} request(s) routed to a cloud provider since this agent started.`;
  }
  copy.appendChild(textEl("div", "", headline));

  const detail = textEl("div", "muted", "Cloud fires only when a policy with ");
  detail.appendChild(codeEl("allow_cloud"));
  detail.appendChild(
    document.createTextNode(
      " matches and no local or peer backend can serve the model."
    )
  );
  copy.appendChild(detail);
  if (state.status?.uptime_s != null) {
    copy.appendChild(
      textEl("div", "field-help", `Counted since agent start · uptime ${formatDuration(state.status.uptime_s)}.`)
    );
  }
  row.appendChild(copy);
  row.appendChild(el("div", "spacer"));

  const toggle = el("div");
  cloudScalarFields(toggle, ["enabled"]);
  row.appendChild(toggle);
  body.appendChild(row);
}

/* ---------------- providers ---------------- */

function renderCloudProvidersSection(root, draft) {
  const body = panel(
    root,
    "Providers",
    `${enabledCloudProviders(draft).length} of ${cloudProviderIds(draft).length} enabled`
  );
  const desc = textEl(
    "p",
    "panel-desc",
    "Keys are write-only: a stored key is never shown back. Leave a key field blank to keep the one already saved. Changes apply after Save"
  );
  desc.appendChild(document.createTextNode(" + Restart Agent."));
  body.appendChild(desc);

  if (!Object.keys(draft.providers).length) {
    body.appendChild(
      textEl(
        "p",
        "field-help",
        "Admin config unavailable — showing the built-in provider roster. Values cannot be saved until the agent answers."
      )
    );
  }
  cloudProviderIds(draft).forEach((pid) => {
    body.appendChild(renderCloudProviderCard(pid, draft));
  });

  const unrendered = cloudUnrenderedProviderFields();
  if (unrendered.length) {
    body.appendChild(
      textEl(
        "p",
        "field-help",
        `This agent also accepts per-provider ${unrendered.join(", ")} — edit those in config.toml.`
      )
    );
  }
}

function renderCloudProviderCard(pid, draft) {
  const entry = ensureCloudProviderEntry(draft, pid);
  const summary = cloudProviderSummary(pid);
  const title = summary.display_name || pid;
  const card = el("div", "inset");

  const head = el("div", "row");
  head.appendChild(textEl("strong", "", title));
  if (entry._pending_api_key) {
    head.appendChild(pill("warn", "key typed — unsaved"));
  } else if (summary.api_key_set) {
    head.appendChild(pill("ok", "key set"));
  } else {
    head.appendChild(pill("neutral", "no key"));
  }
  const format = entry.api_format || summary.default_api_format;
  if (format) head.appendChild(textEl("span", "muted", format));
  head.appendChild(el("div", "spacer"));
  head.appendChild(
    switchRow(`Enable ${title}`, !!entry.enabled, (v) => {
      entry.enabled = v;
      markDirty();
      rerenderCloud();
    })
  );
  card.appendChild(head);

  if (summary.notes) card.appendChild(textEl("p", "field-help", summary.notes));

  // api_key — write-only. The input is never seeded from a stored value
  // (there is none on the wire) and an empty box must stay absent from the
  // patch, so it only ever writes `_pending_api_key`.
  const keyField = el("div", "field");
  const keyLabel = textEl(
    "label",
    "field-label",
    summary.api_key_set ? "API key (set — enter to replace)" : "API key"
  );
  const keyInput = el("input");
  keyInput.type = "password";
  keyInput.autocomplete = "off";
  keyInput.id = `cloud-key-${pid}`;
  keyInput.placeholder = summary.api_key_set
    ? "•••••••• (unchanged if left blank)"
    : "paste a provider key";
  keyInput.value = entry._pending_api_key || "";
  keyInput.oninput = () => {
    // No re-render here: it would steal focus mid-typing.
    entry._pending_api_key = keyInput.value;
    markDirty();
  };
  keyLabel.htmlFor = keyInput.id;
  keyField.append(keyLabel, keyInput);
  card.appendChild(keyField);

  const grid = el("div", "grid-2");
  grid.appendChild(renderCloudRegionField(pid, entry, summary));
  grid.appendChild(renderCloudApiFormatField(pid, entry, summary));
  grid.appendChild(
    renderCloudTextField(
      `cloud-keyenv-${pid}`,
      "API key env var",
      entry.api_key_env,
      "leave blank for the provider default env var",
      (v) => {
        entry.api_key_env = v;
        markDirty();
      }
    )
  );
  grid.appendChild(
    renderCloudTextField(
      `cloud-baseurl-${pid}`,
      "Base URL override",
      entry.base_url,
      "leave blank for the registry endpoint",
      (v) => {
        entry.base_url = v;
        markDirty();
      }
    )
  );
  card.appendChild(grid);

  card.appendChild(renderCloudModelsSection(pid, entry));
  return card;
}

function renderCloudTextField(id, label, value, placeholder, onInput) {
  const field = el("div", "field");
  const labelNode = textEl("label", "field-label", label);
  const input = el("input");
  input.type = "text";
  input.id = id;
  input.placeholder = placeholder;
  input.value = value == null ? "" : String(value);
  input.oninput = () => onInput(input.value);
  labelNode.htmlFor = id;
  field.append(labelNode, input);
  return field;
}

function renderCloudSelectField(id, label, options, value, onChange) {
  const field = el("div", "field");
  const labelNode = textEl("label", "field-label", label);
  const select = el("select");
  select.id = id;
  labelNode.htmlFor = id;
  options.forEach(({ value: optValue, label: optLabel }) => {
    const option = textEl("option", "", optLabel);
    option.value = optValue;
    select.appendChild(option);
  });
  select.value = value;
  select.onchange = () => onChange(select.value);
  field.append(labelNode, select);
  return field;
}

function renderCloudRegionField(pid, entry, summary) {
  // Regions are registry facts carried by the config summary, not the schema.
  const regions = asArray(summary.regions);
  const options = [{ value: "", label: "default" }];
  regions.forEach((r) => {
    if (r) options.push({ value: r, label: r });
  });
  const current = entry.region || "";
  if (current && !options.some((o) => o.value === current)) {
    options.push({ value: current, label: `${current} (not in registry)` });
  }
  return renderCloudSelectField(
    `cloud-region-${pid}`,
    "Region / profile",
    options,
    current,
    (v) => {
      entry.region = v;
      markDirty();
    }
  );
}

function renderCloudApiFormatField(pid, entry, summary) {
  // Option set comes from the schema's Literal introspection so no wire
  // format name is spelled out here.
  const spec = cloudProviderFieldSpec("api_format");
  const options = [
    { value: "", label: `default${summary.default_api_format ? ` (${summary.default_api_format})` : ""}` },
  ];
  asArray(spec?.options).forEach((o) => options.push({ value: o, label: o }));
  const current = entry.api_format || "";
  if (current && !options.some((o) => o.value === current)) {
    options.push({ value: current, label: current });
  }
  const field = renderCloudSelectField(
    `cloud-format-${pid}`,
    "API format",
    options,
    current,
    (v) => {
      // null, not "", is the "inherit the registry default" value the
      // ApiFormat | None field expects.
      entry.api_format = v || null;
      markDirty();
    }
  );
  if (!spec) {
    field.appendChild(
      textEl("p", "field-help", "Config schema unavailable — showing the stored value only.")
    );
  }
  return field;
}

/* ---------------- per-provider model allowlist ---------------- */

// cloud.providers.<id>.models — an empty allowlist means "every model this
// provider serves" (server default), so unchecking the first model has to
// materialize the full catalog first. Twin of CloudSettingsView.swift's
// Models section (docs/models-ux-plan.md phase D).
function cloudModelEnabled(pid, modelId) {
  const allowlist = asArray(state.configDraft?.cloud?.providers?.[pid]?.models);
  return allowlist.length === 0 || allowlist.includes(modelId);
}

function toggleCloudModel(pid, modelId, enabled) {
  const entry = state.configDraft.cloud.providers[pid];
  if (!Array.isArray(entry.models)) entry.models = [];
  if (entry.models.length === 0) {
    if (enabled) return; // already enabled (empty = all)
    const catalog = state.cloudCatalogs[pid];
    // Need a *readable* catalog to know what "all" means — materialising the
    // allowlist from a broken one would silently narrow it to nothing.
    if (!catalog || catalog.invalid) return;
    entry.models = asArray(catalog.models).filter((m) => m !== modelId);
  } else if (enabled) {
    if (!entry.models.includes(modelId)) entry.models.push(modelId);
  } else {
    entry.models = entry.models.filter((m) => m !== modelId);
  }
  markDirty();
  rerenderCloud();
}

function resetCloudModels(pid) {
  state.configDraft.cloud.providers[pid].models = [];
  markDirty();
  rerenderCloud();
}

/**
 * Coerce a /cloud/providers/{id}/models body into the one shape this page
 * renders: `{ models: [string], … }`, or an explicit invalid marker.
 *
 * The catalog is cached in state.cloudCatalogs and re-read on every render
 * (including the poll re-render), so an unvalidated body is not a one-shot
 * render failure — it poisons the page until the tab is reloaded. Normalising
 * *before* the cache write is what keeps a bad response to a message.
 */
function normalizeCloudCatalog(payload) {
  const raw = asObject(payload);
  if (!Array.isArray(raw.models)) {
    return {
      invalid: true,
      models: [],
      detail:
        "The agent returned a model catalog this page cannot read (no `models` list).",
    };
  }
  return {
    ...raw,
    // Entries are provider-supplied ids; anything that is not a usable string
    // cannot be rendered as a checkbox label or stored in the allowlist.
    models: raw.models.filter((m) => typeof m === "string" && m),
  };
}

async function fetchCloudCatalog(pid) {
  if (state.cloudCatalogFetching.has(pid)) return;
  state.cloudCatalogFetching.add(pid);
  rerenderCloud();
  try {
    const catalog = normalizeCloudCatalog(
      await api(`/netllm/v1/cloud/providers/${pid}/models`)
    );
    state.cloudCatalogs[pid] = catalog;
    if (catalog.invalid) showToast(catalog.detail);
  } catch (e) {
    showToast(`Could not fetch model catalog: ${e.message}`);
  } finally {
    state.cloudCatalogFetching.delete(pid);
    rerenderCloud();
  }
}

function cloudStaticCatalogNote(catalog) {
  if (catalog.status === "no_api_key") {
    return `No API key yet — showing the built-in catalog. ${catalog.detail || ""}`.trim();
  }
  if (catalog.status === "static_catalog") {
    return "This provider has no live model-list API — showing the built-in catalog.";
  }
  return `Live catalog unavailable (${catalog.status}) — showing the built-in catalog.`;
}

function renderCloudModelsSection(pid, entry) {
  const wrap = el("div", "field");
  const header = el("div", "row");
  header.appendChild(textEl("div", "field-label", "Models"));
  if (state.cloudCatalogFetching.has(pid)) {
    header.appendChild(textEl("span", "muted", "Fetching…"));
  }
  header.appendChild(el("div", "spacer"));
  const fetchBtn = button(
    state.cloudCatalogs[pid] ? "Refresh model list" : "Fetch model list",
    "small secondary",
    () => fetchCloudCatalog(pid)
  );
  fetchBtn.disabled = !state.healthy || state.cloudCatalogFetching.has(pid);
  header.appendChild(fetchBtn);
  wrap.appendChild(header);

  const catalog = state.cloudCatalogs[pid];
  const allowlist = asArray(entry.models);
  if (!catalog) {
    wrap.appendChild(
      textEl(
        "p",
        "field-help",
        allowlist.length
          ? `Restricted to: ${allowlist.join(", ")}. Fetch the model list to edit.`
          : "All models this provider serves are allowed (default). Fetch the list to restrict it."
      )
    );
    return wrap;
  }

  if (catalog.invalid) {
    // Degrade to a message, and leave the stored allowlist alone: an
    // unreadable catalog is no reason to touch the user's config.
    wrap.appendChild(
      textEl(
        "p",
        "field-help",
        `${catalog.detail} ${
          allowlist.length
            ? `Currently restricted to: ${allowlist.join(", ")}.`
            : "All models this provider serves stay allowed (default)."
        }`
      )
    );
    return wrap;
  }

  if (catalog.source === "static") {
    wrap.appendChild(textEl("p", "field-help", cloudStaticCatalogNote(catalog)));
  }
  const summaryRow = el("div", "row");
  if (allowlist.length === 0) {
    summaryRow.appendChild(
      textEl(
        "span",
        "field-help",
        `All ${catalog.models.length} models enabled (default). Uncheck any to restrict.`
      )
    );
  } else {
    summaryRow.appendChild(
      textEl(
        "span",
        "field-help",
        `${allowlist.length} of ${catalog.models.length} models enabled.`
      )
    );
    summaryRow.appendChild(el("div", "spacer"));
    summaryRow.appendChild(
      button("Enable all", "small secondary", () => resetCloudModels(pid))
    );
  }
  wrap.appendChild(summaryRow);

  // Configured models the fetched catalog doesn't list (renamed/deprecated
  // upstream) stay visible so they can be unchecked.
  const extras = allowlist.filter((m) => !catalog.models.includes(m));
  const list = el("div", "grid-2");
  [...catalog.models, ...extras].forEach((modelId) => {
    list.appendChild(
      switchRow(modelId, cloudModelEnabled(pid, modelId), (v) =>
        toggleCloudModel(pid, modelId, v)
      )
    );
  });
  wrap.appendChild(list);
  wrap.appendChild(
    textEl(
      "p",
      "field-help",
      "Model changes apply after Save + Restart Agent. Enabled models appear on the Models page for pool assignment."
    )
  );
  return wrap;
}

/* ---------------- policies that may reach cloud ---------------- */

function cloudPolicyMatchSummary(policy) {
  const parts = [policy.model_prefix ? `${policy.model_prefix}*` : "any model"];
  if (policy.source) parts.push(`source ${policy.source}`);
  if (policy.api_format) parts.push(policy.api_format);
  if (policy.prefer_provider) parts.push(`prefer ${policy.prefer_provider}`);
  if (policy.strategy) parts.push(policy.strategy);
  return parts.join(" · ");
}

function renderCloudPoliciesSection(root) {
  const body = panel(root, "Which policies may reach cloud", "read-only mirror of Routing");
  body.appendChild(
    textEl(
      "p",
      "panel-desc",
      "Cloud can never be switched on from this page alone — a request also has to match a policy that allows it."
    )
  );

  // policies/sources come from the config summary; a null entry or a
  // wrong-typed list must not take the whole page down.
  const policies = asArray(state.configDraft?.routing?.policies);
  const allowing = policies
    .map((policy, index) => ({ policy: asObject(policy), index }))
    .filter(({ policy }) => policy.allow_cloud);

  if (!allowing.length) {
    body.appendChild(
      textEl(
        "p",
        "empty",
        policies.length
          ? `No policy allows cloud. All ${policies.length} policy rule(s) are local-only.`
          : "No routing policies are configured, so nothing grants cloud access."
      )
    );
  } else {
    allowing.forEach(({ policy, index }) => {
      const row = el("div", "inset");
      const line = el("div", "row");
      line.appendChild(textEl("span", "mono muted", String(index + 1)));
      const copy = el("div");
      copy.appendChild(textEl("div", "", policy.name || `policy ${index + 1}`));
      copy.appendChild(textEl("div", "field-help", cloudPolicyMatchSummary(policy)));
      line.appendChild(copy);
      line.appendChild(el("div", "spacer"));
      line.appendChild(pill("warn", "allow_cloud"));
      line.appendChild(
        policy.enabled === false
          ? pill("neutral", "policy disabled")
          : pill("ok", "policy enabled")
      );
      line.appendChild(
        button("Edit in Routing", "small secondary", () => navigate("routing"))
      );
      row.appendChild(line);
      body.appendChild(row);
    });
  }

  // routing.sources carry their own allow_cloud, and a source-level grant is
  // just as real as a policy one — showing only policies would understate
  // what can reach cloud.
  const sources = asArray(state.configDraft?.routing?.sources)
    .map((s) => asObject(s))
    .filter((s) => s.allow_cloud || asArray(s.cloud_providers).length);
  if (sources.length) {
    body.appendChild(
      textEl(
        "p",
        "field-help",
        `Client sources that also allow cloud: ${sources.map((s) => s.id).join(", ")}.`
      )
    );
  }

  const trailer = textEl(
    "p",
    "field-help",
    "Every other rule is local-only. Requests matching nothing fall through to "
  );
  trailer.appendChild(codeEl("local_spillover"));
  trailer.appendChild(
    document.createTextNode(" and fail loudly rather than silently billing you.")
  );
  body.appendChild(trailer);
}

/* ---------------- guardrails + usage ---------------- */

function renderCloudGuardrailsSection(root) {
  const body = panel(root, "Guardrails", "when failover is allowed to fire");
  cloudScalarFields(body, ["fallback_enabled", "fallback"]);
  // Design 3b also shows a monthly spend ceiling, a confirm-before-first-call
  // prompt, and a mesh-down-only gate. None of the three exists in CloudConfig
  // (see scratchpad gaps/cloud.md) — an empty state, not invented switches.
  body.appendChild(
    textEl(
      "p",
      "empty",
      "A monthly spend ceiling, first-call confirmation, and a mesh-down-only gate are not in the agent config yet — only the fallback controls above are enforced."
    )
  );
}

function renderCloudUsageSection(root) {
  const split = cloudRequestSplit();
  const body = panel(root, "Cloud usage", "since this agent started");
  if (!split) {
    body.appendChild(
      textEl("p", "empty", "No router counters yet — the agent is unreachable.")
    );
    return;
  }

  const stats = el("div", "stat-row");
  stats.appendChild(
    statBlock("Cloud requests", formatCompactCount(split.cloud), null, split.cloud ? "accent" : "")
  );
  stats.appendChild(statBlock("Served on your mesh", formatCompactCount(split.mesh)));
  if (split.unattributed) {
    stats.appendChild(
      statBlock("Unattributed", formatCompactCount(split.unattributed))
    );
  }
  body.appendChild(stats);

  const share = split.total ? (split.cloud / split.total) * 100 : 0;
  const meter = el("div", "meter");
  const fill = el("span", split.cloud ? "warn" : "");
  fill.style.width = `${share.toFixed(1)}%`;
  meter.appendChild(fill);
  body.appendChild(meter);
  body.appendChild(
    textEl(
      "p",
      "field-help",
      `${formatPercent(share, 1)} of routed requests went to a cloud provider.`
    )
  );
  if (split.unattributed) {
    body.appendChild(
      textEl(
        "p",
        "field-help",
        "Unattributed counters belong to backends the pool no longer lists, so they cannot be labelled cloud or local."
      )
    );
  }
  // No cost figure is shown: the agent has no price table and no spend
  // accounting, and a plausible-looking dollar number would be a lie.
  body.appendChild(
    textEl(
      "p",
      "field-help",
      "Request counts come from the agent and reset when it restarts. Cost to date needs a price table the agent does not have."
    )
  );
}

/* ---------------- page ---------------- */

function testCloudKeys(draft) {
  // The model-catalog endpoint is the only key probe the agent exposes: it
  // answers `no_api_key` / an upstream error / a live list, per provider.
  const targets = cloudProviderIds(draft).filter((pid) => draft.providers[pid]?.enabled);
  if (!targets.length) {
    showToast("Enable a provider first — keys are tested by listing its models");
    return;
  }
  showToast(`Testing ${targets.length} provider(s) — stored keys only`);
  targets.forEach((pid) => fetchCloudCatalog(pid));
}

function cloudHeaderActions(draft) {
  const wrap = el("div", "row");
  const test = button("Test keys", "secondary", () => testCloudKeys(draft));
  test.title =
    "Fetches each enabled provider's model list — the only key check the agent exposes. Tests the saved key, not one you just typed.";
  wrap.appendChild(test);
  // Primary styling is the unclassed <button>, and button() coerces a falsy
  // class to "secondary" — so this one is built directly.
  const save = textEl("button", "", "Save keys");
  save.type = "button";
  save.onclick = () => saveConfig();
  wrap.appendChild(save);
  return wrap;
}

function cloudRailAside(draft) {
  const aside = el("div", "stack");
  const enabled = enabledCloudProviders(draft);
  const line = el("div", "field");
  line.appendChild(textEl("div", "field-label", "Enabled providers"));
  line.appendChild(
    textEl(
      "div",
      "field-help",
      enabled.length
        ? enabled.map((pid) => cloudProviderSummary(pid).display_name || pid).join(", ")
        : "none"
    )
  );
  aside.appendChild(line);
  return aside;
}

function renderCloudPage(root) {
  const draft = ensureCloudDraft();
  pageHeader(
    root,
    "Cloud failover",
    "Off by default. Cloud is reachable only through a policy that explicitly allows it.",
    cloudHeaderActions(draft)
  );
  renderCloudHero(root, draft);
  railLayout(
    root,
    [
      { id: "cloud-providers", label: "Providers", render: (b) => renderCloudProvidersSection(b, draft) },
      { id: "cloud-policies", label: "Policies", render: (b) => renderCloudPoliciesSection(b) },
      { id: "cloud-guardrails", label: "Guardrails", render: (b) => renderCloudGuardrailsSection(b) },
      { id: "cloud-usage", label: "Cloud usage", render: (b) => renderCloudUsageSection(b) },
    ],
    cloudRailAside(draft)
  );
}

registerPage("cloud", renderCloudPage);
