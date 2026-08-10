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

/* ---------------- credential verification ---------------- */

/**
 * The server's verification verdict for one provider.
 *
 * Every word of it — the status, the blocker sentence, whether the provider
 * may be enabled — is computed by the agent
 * (netllm_core.cloud_verification.verification_state) and carried on the
 * config summary. This page renders it and never re-derives it: the same rule
 * is enforced on the write path, and a second implementation here would be a
 * second answer that drifts from the one that actually decides.
 *
 * A check run in this session wins over the stored record only until the next
 * config refresh, which is what makes pressing Verify feel immediate without
 * inventing state the agent does not have.
 */
function cloudVerification(pid) {
  const live = asObject(state.cloudVerifyResults?.[pid]);
  const stored = asObject(cloudProviderSummary(pid).verification);
  const source = live.status ? live : stored;
  return {
    status: source.status || "",
    ok: !!source.ok,
    blocker: source.blocker || "",
    detail: source.detail || "",
    checkedAt: source.checked_at || "",
    // Absent on an agent too old to send `verification`: no verdict means no
    // gate, and degrading to "you may enable this" is the only honest read of
    // an agent that has never heard of the check.
    canEnable: source.can_enable === undefined ? true : !!source.can_enable,
    known: !!source.status,
  };
}

/** The Enable switch is live only for a provider that has earned it. */
function cloudCanEnable(pid, draft) {
  const entry = asObject(draft.providers?.[pid]);
  // Already on: never take the switch away. Turning a working provider OFF
  // must always be possible, and an upgrade from a build before this feature
  // has `enabled = true` with no record anywhere.
  if (entry.enabled) return true;
  return cloudVerification(pid).canEnable;
}

async function verifyCloudProvider(pid, draft) {
  if (state.cloudVerifying.has(pid)) return;
  const entry = ensureCloudProviderEntry(draft, pid);
  state.cloudVerifying.add(pid);
  rerenderCloud();
  try {
    // A key typed but not saved is sent in the body. That is the whole answer
    // to the unsaved-key problem: the agent checks it, records the outcome
    // against its fingerprint, and never stores or logs the key itself — so
    // nobody has to save a broken key to find out that it is broken.
    const body = entry._pending_api_key
      ? JSON.stringify({ api_key: entry._pending_api_key })
      : "{}";
    const result = await api(`/netllm/v1/cloud/providers/${pid}/verify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    });
    state.cloudVerifyResults[pid] = asObject(result);
    const verdict = asObject(result);
    showToast(
      verdict.ok
        ? `${cloudProviderSummary(pid).display_name || pid}: key verified`
        : `${cloudProviderSummary(pid).display_name || pid}: ${verdict.blocker || "not verified"}`
    );
    if (verdict.persisted === false) {
      showToast(
        "This agent has no config file, so the check will not survive a restart."
      );
    }
  } catch (e) {
    showToast(`Could not verify: ${e.message}`);
  } finally {
    state.cloudVerifying.delete(pid);
    rerenderCloud();
  }
}

/**
 * True when at least one *enabled* provider could actually authenticate.
 *
 * "Has a key" is no longer enough, and that is the point of UI-7a: a key that
 * has never been checked, or that the provider rejected, is exactly the state
 * where the page used to claim failover was armed. A key typed into this page
 * but not yet saved does not count either — it counts once it has been
 * verified, which the Verify button can do before Save.
 */
function cloudProviderUsable(pid, draft) {
  const entry = asObject(draft.providers?.[pid]);
  if (!entry.enabled) return false;
  const verification = cloudVerification(pid);
  if (!verification.known) {
    // Older agent: fall back to the pre-UI-7a signal rather than reporting
    // every provider as broken.
    return !!(cloudProviderSummary(pid).api_key_set || entry._pending_api_key);
  }
  return verification.ok;
}

/**
 * The hero's honest state, as a pill kind + label.
 *
 * Green is reserved for "actively good". Three states matter here and the page
 * could previously only tell two of them apart — it painted the panel green and
 * put a green dot next to the word "off":
 *
 *   off        — cloud.enabled is false. Nothing is wrong and nothing is
 *                armed: neutral, no accent.
 *   unverified — enabled, but no enabled provider has a credential this
 *                agent has checked. This is the dangerous one: the user
 *                believes they have failover and they do not, so it is a
 *                warning, not a green tick.
 *   armed      — enabled, and at least one provider's key has been verified
 *                against the provider. Green.
 */
function cloudArmedState(draft) {
  if (draft.enabled === false) return { kind: "neutral", label: "off" };
  const usable = cloudProviderIds(draft).filter((pid) => cloudProviderUsable(pid, draft));
  if (!usable.length) return { kind: "warn", label: "unverified" };
  return { kind: "ok", label: "armed" };
}

function renderCloudHero(root, draft) {
  const split = cloudRequestSplit();
  const armed = cloudArmedState(draft);
  const reached = split ? split.cloud > 0 : false;
  // The accent tracks the state the pill names. "off" is not an achievement
  // and not a fault, so it gets no accent at all.
  const accent = reached
    ? "accent-warn"
    : armed.kind === "ok"
      ? "accent-ok"
      : armed.kind === "warn"
        ? "accent-warn"
        : "";
  const body = panel(root, null, null, accent);

  const row = el("div", "row");
  row.appendChild(
    reached
      ? pill("warn", `${formatCompactCount(split.cloud)} cloud`)
      : pill(armed.kind, armed.label)
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
  // The armed-but-keyless case, said out loud. Without this the page shows
  // failover as configured and never mentions that it cannot fire.
  if (armed.kind === "warn") {
    copy.appendChild(
      textEl(
        "div",
        "field-help text-warn",
        "Cloud failover is on, but no enabled provider has a credential this agent has verified — it cannot be relied on to fire. Add a key below and press Verify key."
      )
    );
  }
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
    renderCloudProviderCard(body, pid, draft);
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

/**
 * The credential's state as a pill kind + short label.
 *
 * The status vocabulary is the server's, so a provider that is unreachable
 * does not read the same as one whose key was refused — "not working" is not
 * a diagnosis, and the Backends page has never settled for one.
 */
function cloudKeyPill(pid, entry, summary) {
  if (entry._pending_api_key) return { kind: "warn", label: "key typed — unsaved" };
  const verification = cloudVerification(pid);
  if (!verification.known) {
    return summary.api_key_set
      ? { kind: "ok", label: "key set" }
      : { kind: "neutral", label: "no key" };
  }
  const LABELS = {
    ok: "verified",
    no_key: "no key set",
    unauthorized: "key rejected",
    no_endpoint: "no endpoint",
    unreachable: "unreachable",
    timeout: "no answer",
    never_checked: "never checked",
    key_changed: "key changed",
    inconclusive: "unconfirmed",
    error: "check failed",
  };
  const label = LABELS[verification.status] || verification.status;
  if (verification.ok) return { kind: "ok", label };
  // Never-checked is a to-do, not a fault; a rejected key is a fault.
  const kind = verification.status === "never_checked" ? "neutral" : "warn";
  return { kind, label };
}

/** "verified · enabled" / "key rejected · disabled" — readable when folded. */
function cloudProviderCardSummary(pid, entry, summary) {
  const parts = [cloudKeyPill(pid, entry, summary).label];
  parts.push(entry.enabled ? "enabled" : "disabled");
  const models = asArray(entry.models);
  if (models.length) parts.push(`${models.length} models`);
  return parts.join(" · ");
}

/**
 * One provider, folded.
 *
 * Six providers × five fields each is what made this page three screens tall,
 * for a user who typically has one enabled. Open by default only where there
 * is something to look at — the provider is enabled, or a key is already
 * stored — and force-open whenever the card holds an unsaved edit, so a typed
 * key can never be hidden behind a closed triangle.
 *
 * The Enable switch stays in the body rather than the header: an <input>
 * inside <summary> is toggled by the same click that folds the section, and
 * there is no way to have one without the other. The header says "enabled" or
 * "disabled" in words instead.
 */
function renderCloudProviderCard(root, pid, draft) {
  const entry = ensureCloudProviderEntry(draft, pid);
  const summary = cloudProviderSummary(pid);
  const title = summary.display_name || pid;
  const format = entry.api_format || summary.default_api_format;
  const edited = !!entry._pending_api_key || draftDiffers(`cloud.providers.${pid}`);
  // collapsiblePanel appends the box itself and hands back the body, so the
  // card is built into `root` rather than returned.
  const card = collapsiblePanel(root, title, format || null, {
    boxClass: "inset",
    storageKey: `cloud.provider.${pid}`,
    defaultOpen: !!entry.enabled || !!summary.api_key_set,
    forceOpen: edited,
    forceReason: "unsaved edits",
    summary: cloudProviderCardSummary(pid, entry, summary),
  });

  const head = el("div", "row");
  const keyPill = cloudKeyPill(pid, entry, summary);
  head.appendChild(pill(keyPill.kind, keyPill.label));
  head.appendChild(el("div", "spacer"));
  const canEnable = cloudCanEnable(pid, draft);
  const enableSwitch = switchRow(`Enable ${title}`, !!entry.enabled, (v) => {
    entry.enabled = v;
    markDirty();
    rerenderCloud();
  });
  if (!canEnable) {
    // Disabled, not hidden: a control that vanishes reads as a missing
    // feature, while a disabled one with the blocker printed underneath
    // reads as a step not yet done. The server refuses this save either
    // way (config_guards), so the switch would only be a lie.
    enableSwitch.querySelectorAll("input").forEach((input) => {
      input.disabled = true;
    });
    enableSwitch.title = cloudVerification(pid).blocker;
  }
  head.appendChild(enableSwitch);
  card.appendChild(head);

  card.appendChild(renderCloudVerificationRow(pid, entry, draft));

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
}

/**
 * "on 10 Aug 2026, 14:02" from the record's ISO `checked_at`.
 *
 * When the check ran matters as much as its outcome — a pass from three
 * months ago against a key that has since expired upstream is not the same
 * claim as one from this morning. Unparseable input degrades to the raw
 * string rather than to "Invalid Date".
 */
function cloudCheckedAtLabel(iso) {
  if (!iso) return "";
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return `at ${iso}`;
  return `on ${when.toLocaleString()}`;
}

/**
 * The blocker line and the Verify button — the whole feature, in one row.
 *
 * The sentence is the agent's (`verification.blocker`), never composed here,
 * for the same reason the Backends page prints the probe's own explanation:
 * the surface that ran the check is the only one that knows what happened.
 */
function renderCloudVerificationRow(pid, entry, draft) {
  const wrap = el("div", "field");
  const verification = cloudVerification(pid);
  const row = el("div", "row");

  const copy = el("div");
  if (!verification.known) {
    copy.appendChild(
      textEl(
        "div",
        "field-help",
        "This agent does not report credential checks — it predates them."
      )
    );
  } else if (verification.ok) {
    copy.appendChild(
      textEl(
        "div",
        "field-help",
        `Key verified ${cloudCheckedAtLabel(verification.checkedAt)}. ${verification.detail}`.trim()
      )
    );
  } else {
    copy.appendChild(textEl("div", "field-help text-warn", verification.blocker));
    if (verification.detail && !verification.blocker.includes(verification.detail)) {
      copy.appendChild(textEl("div", "field-help mono", verification.detail));
    }
  }
  if (entry._pending_api_key) {
    copy.appendChild(
      textEl(
        "div",
        "field-help",
        "Verify checks the key typed above without saving it — only the result is stored."
      )
    );
  }
  row.appendChild(copy);
  row.appendChild(el("div", "spacer"));

  const verifying = state.cloudVerifying.has(pid);
  const btn = button(
    verifying ? "Verifying…" : "Verify key",
    "small secondary",
    () => verifyCloudProvider(pid, draft)
  );
  btn.id = `cloud-verify-${pid}`;
  btn.disabled = !state.healthy || verifying;
  btn.title =
    "Checks this provider's credential against the provider itself. A provider cannot be enabled until this passes.";
  row.appendChild(btn);
  wrap.appendChild(row);
  return wrap;
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

/**
 * Re-check every provider that has something to check.
 *
 * Enabled providers *and* keyed-but-disabled ones: the second set is exactly
 * where a user is mid-setup, and it is the set whose check unlocks the Enable
 * switch. Restricting this to enabled providers — as the old "Test keys" did
 * — checked only the providers that no longer needed checking.
 */
function verifyCloudKeys(draft) {
  const targets = cloudProviderIds(draft).filter((pid) => {
    const entry = asObject(draft.providers?.[pid]);
    return (
      entry.enabled || entry._pending_api_key || cloudProviderSummary(pid).api_key_set
    );
  });
  if (!targets.length) {
    showToast("Nothing to verify yet — add a provider key first");
    return;
  }
  showToast(`Verifying ${targets.length} provider(s)`);
  targets.forEach((pid) => verifyCloudProvider(pid, draft));
}

function cloudHeaderActions(draft) {
  const wrap = el("div", "row");
  const test = button("Verify keys", "secondary", () => verifyCloudKeys(draft));
  test.title =
    "Checks every configured credential against its provider and records the result. A key typed but not saved is checked without being stored.";
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
