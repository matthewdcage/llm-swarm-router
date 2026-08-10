/* Routing page — design 1d.
 *
 * The policy list is the page: every routing.policies entry renders as an
 * ordered card that states its match conditions in prose, with reorder /
 * edit / delete. Order is semantic (first match wins), so moving a card is
 * a config edit, not a view preference — hence real ↑/↓ buttons on the
 * draft array rather than the mockup's pointer-only drag handle.
 *
 * Scope: routing.model_aliases and routing.model_pools belong to the
 * Models & pools page and routing.sources to Integrations — same config
 * section, different pages. Every other editable routing field is here.
 * routing.require_same_model_for_shard is the one deliberate omission:
 * deprecated and inert (audit F-17) and ledgered as correctly absent in
 * docs/extending/08-control-parity.md, even though mockup 1d still draws
 * the "Require same model for shard" toggle.
 */

// A null strategy/api_format means "inherit", not "empty" — spell that out
// in the editor's dropdowns instead of showing a blank first option.
const ROUTING_POLICY_ITEM_OVERRIDES = {
  api_format: { optionLabels: { "": "any api_format" } },
  strategy: { optionLabels: { "": "default strategy" } },
  prefer_provider: { optionLabels: { "": "no preference" } },
};

/* ---------------- schema plumbing ---------------- */

function routingSchemaFields() {
  // A malformed /config/schema can deliver `fields` as a scalar or with null
  // entries; every consumer below keys on f.name, so drop what has none. An
  // empty result renders the "schema unavailable" panel, which is honest.
  return asArray(state.configSchema?.sections?.routing?.fields).filter(
    (f) => f && typeof f === "object" && typeof f.name === "string"
  );
}

/** One top-level routing.* field. No-ops for a field the schema omits. */
function routingFieldInto(host, field, overrides) {
  if (!field) return;
  renderSchemaField(host, "routing", field, overrides);
}

/**
 * The item_schema for one policy. Falls back to inferring shape from the
 * entry's own keys so a policy stays editable when /config/schema is
 * unreachable but /config returned real rows.
 */
function routingPolicyItemSchema(policiesField, entry) {
  const declared = asArray(policiesField?.item_schema)
    .filter(Boolean)
    .filter((f) => !f.read_only);
  if (declared.length) return declared;
  return Object.keys(asObject(entry))
    .filter((k) => !k.startsWith("_"))
    .map((name) => ({
      name,
      widget: typeof entry[name] === "boolean" ? "toggle" : "text",
      default: typeof entry[name] === "boolean" ? false : "",
    }));
}

/**
 * Every field of one policy object, bound to that object. schema-form.js's
 * two documented entry points are section-scoped and cannot bind to a
 * single element of routing.policies, so the editor goes through the same
 * module's item-level renderer — the one schemaListOfObjectsRow uses for
 * each row of a list[BaseModel]. A field added to RoutingPolicy therefore
 * appears here with no change to this page.
 */
function renderRoutingPolicyFields(host, itemSchema, entry) {
  if (!itemSchema.length) {
    host.appendChild(textEl("p", "empty", "This policy exposes no editable fields."));
    return;
  }
  host.appendChild(
    schemaFieldsCard(itemSchema, entry, markDirty, ROUTING_POLICY_ITEM_OVERRIDES)
  );
}

/* ---------------- policy cards ---------------- */

/** The card's prose line: match conditions on the left of the arrow, target on the right. */
function routingPolicySummaryNode(policy) {
  const line = el("div", "field-help");
  const text = (t) => line.appendChild(document.createTextNode(t));
  const strong = (t) => line.appendChild(textEl("b", "", t));

  if (policy.model_prefix) {
    text("when model starts ");
    line.appendChild(codeEl(policy.model_prefix));
  } else {
    text("any model");
  }
  if (policy.source) {
    text(" · source ");
    line.appendChild(codeEl(policy.source));
  }
  text(policy.api_format ? ` · ${policy.api_format}` : " · any api");
  text(" → ");
  strong(policy.strategy || "default strategy");
  if (policy.prefer_provider) {
    text(" · prefer ");
    strong(PROVIDER_LABELS[policy.prefer_provider] || policy.prefer_provider);
  }
  text(policy.allow_cloud ? " · cloud " : " · cloud off");
  if (policy.allow_cloud) line.appendChild(textEl("b", "text-warn", "allowed"));
  return line;
}

function routingPolicyTitle(policy, index) {
  return policy.name || `policy ${index + 1}`;
}

function renderRoutingPolicyCard(list, policies, index, policiesField, rerender) {
  const policy = policies[index];
  const title = routingPolicyTitle(policy, index);
  const body = panel(list);

  const row = el("div", "row-between");
  const left = el("div", "row");

  left.appendChild(textEl("span", "mono muted", String(index + 1)));

  const toggle = switchRow("", policy.enabled !== false, (v) => {
    policy.enabled = v;
    markDirty();
    rerender();
  });
  const toggleInput = toggle.querySelector("input");
  if (toggleInput) toggleInput.setAttribute("aria-label", `Enable ${title}`);
  left.appendChild(toggle);

  const label = el("div");
  const nameRow = el("div", "row");
  nameRow.appendChild(textEl("div", "panel-title", title));
  if (policy.enabled === false) nameRow.appendChild(pill("neutral", "disabled"));
  if (policy.allow_cloud) nameRow.appendChild(pill("warn", "cloud"));
  label.appendChild(nameRow);
  label.appendChild(routingPolicySummaryNode(policy));
  left.appendChild(label);

  const right = el("div", "row");

  // Design 1d shows "matched 214× today" here. The agent counts requests
  // per source and per scenario but never per policy, so the slot renders
  // as unknown rather than inventing a number (gaps/routing.md).
  const matched = textEl("span", "muted", "matched —");
  matched.title = "Per-policy match counts are not exposed by the agent yet.";
  right.appendChild(matched);

  const tryBtn = button("Try this rule", "secondary small", null);
  tryBtn.disabled = true;
  tryBtn.title =
    "Not available yet — the agent has no endpoint that resolves a hypothetical request against the routing table.";
  right.appendChild(tryBtn);

  right.appendChild(
    button("Edit", "secondary small", () => {
      openRoutingPolicyEditor(policy, policiesField, title, rerender);
    })
  );

  const up = button("↑", "ghost", () => {
    policies.splice(index - 1, 0, policies.splice(index, 1)[0]);
    markDirty();
    rerender();
  });
  up.setAttribute("aria-label", `Move ${title} earlier`);
  up.disabled = index === 0;
  right.appendChild(up);

  const down = button("↓", "ghost", () => {
    policies.splice(index + 1, 0, policies.splice(index, 1)[0]);
    markDirty();
    rerender();
  });
  down.setAttribute("aria-label", `Move ${title} later`);
  down.disabled = index === policies.length - 1;
  right.appendChild(down);

  const del = button("Delete", "danger", () => {
    policies.splice(index, 1);
    markDirty();
    rerender();
  });
  del.setAttribute("aria-label", `Delete ${title}`);
  right.appendChild(del);

  row.append(left, right);
  body.appendChild(row);
}

/**
 * Policy editor sheet. Edits apply to the draft entry as they are typed
 * (same as the old inline editor — there is no per-row revert anywhere in
 * this dashboard); "Done" only closes and repaints the card.
 */
function openRoutingPolicyEditor(policy, policiesField, title, onDone) {
  openSheet(title, (sheet, close) => {
    sheet.appendChild(
      textEl(
        "p",
        "panel-desc",
        "Fields come from the routing policy schema, so a new policy field appears here automatically. Changes are held locally until you Save."
      )
    );
    const form = el("div", "stack");
    renderRoutingPolicyFields(form, routingPolicyItemSchema(policiesField, policy), policy);
    sheet.appendChild(form);

    const actions = el("div", "sheet-actions");
    actions.appendChild(
      button("Done", "", () => {
        close();
        onDone();
      })
    );
    sheet.appendChild(actions);
  });
}

function routingPolicyCountText(count) {
  return `${count} ${count === 1 ? "policy" : "policies"} · evaluated top to bottom`;
}

/**
 * Repaints just the card list (not the whole page) so an edit does not
 * scroll the user back to the top. `onCount` keeps the panel's counter in
 * step, since add/delete happen entirely inside this subtree.
 */
function renderRoutingPolicyList(host, draft, policiesField, onCount) {
  host.textContent = "";
  const policies = draft.policies;
  if (onCount) onCount(policies.length);
  if (!policies.length) {
    host.appendChild(
      textEl(
        "p",
        "empty",
        "No policies — every request uses the default strategy above."
      )
    );
    return;
  }
  const rerender = () =>
    renderRoutingPolicyList(host, draft, policiesField, onCount);
  policies.forEach((_, i) =>
    renderRoutingPolicyCard(host, policies, i, policiesField, rerender)
  );
}

/* ---------------- page ---------------- */

function renderRoutingPage(root) {
  const draft = state.configDraft?.routing;
  // Not just missing: a scalar `routing` section would swallow every write
  // below (sloppy-mode assignment to a primitive is a silent no-op) and then
  // read back undefined.
  if (!draft || typeof draft !== "object" || Array.isArray(draft)) {
    pageHeader(root, "Routing", "Policy table and mesh routing defaults");
    panel(root, "Routing configuration").appendChild(
      textEl(
        "p",
        "empty",
        "Routing configuration is unavailable — the admin API could not be reached."
      )
    );
    return;
  }
  // Normalise in place, not just for iteration: the cards push/splice this
  // array. A null or scalar row has no fields to render and cannot be edited
  // back into shape, so it is dropped rather than taking the page down.
  draft.policies = asArray(draft.policies).filter(
    (p) => p && typeof p === "object" && !Array.isArray(p)
  );

  const schemaFields = routingSchemaFields();
  const byName = Object.fromEntries(schemaFields.map((f) => [f.name, f]));
  const policiesField = byName.policies;

  // Assigned once the list exists; the header button is built first.
  let repaintPolicies = () => render();

  const addPolicy = button("Add policy", "secondary", () => {
    // The schema names a builder in default_factory (config-schema-rewrite-plan
    // §6 risk 1) so "Add policy" starts from something sensible rather than an
    // all-empty row; schema-form.js owns the name -> factory table.
    const factory = SCHEMA_ITEM_FACTORIES[policiesField?.default_factory];
    const entry = factory
      ? factory()
      : Object.fromEntries(
          asArray(policiesField?.item_schema)
            .filter(Boolean)
            .filter((f) => !f.read_only)
            .map((f) => [f.name, f.default])
        );
    draft.policies.push(entry);
    markDirty();
    repaintPolicies();
    openRoutingPolicyEditor(entry, policiesField, "New policy", repaintPolicies);
  });

  pageHeader(
    root,
    "Routing",
    "First match wins · reorder with ↑ ↓ · unsaved changes stay local until you Save",
    addPolicy
  );

  // Without the schema document there is no field spec to render any
  // routing scalar from, so say that once rather than drawing four empty
  // panels. Policies below still work — they are edited from draft data.
  if (!schemaFields.length) {
    panel(root, "Routing settings").appendChild(schemaUnavailableNode());
  } else {
    // Defaults row: the strategy every unmatched request falls through to,
    // and the mesh-wide switch that decides whether peers are candidates.
    const defaults = el("div", "grid-2");
    const strategyCell = el("div");
    const strategyBody = panel(
      strategyCell,
      "Default strategy",
      "used when no policy matches"
    );
    routingFieldInto(strategyBody, byName.default_strategy, { label: "Strategy" });
    defaults.appendChild(strategyCell);

    const limitsCell = el("div");
    const limitsBody = panel(limitsCell, "Mesh limits");
    routingFieldInto(limitsBody, byName.allow_remote, {
      label: "Allow remote backends",
    });
    defaults.appendChild(limitsCell);
    root.appendChild(defaults);
  }

  const policyNote = textEl(
    "div",
    "panel-note",
    routingPolicyCountText(draft.policies.length)
  );
  const policyBody = panel(root, "Policies", policyNote);
  policyBody.appendChild(
    textEl(
      "p",
      "panel-desc",
      "Every Try this rule button is disabled: the agent has no endpoint that resolves a hypothetical request against the routing table, so there is nothing truthful to show yet."
    )
  );
  const listHost = el("div");
  const onCount = (n) => {
    policyNote.textContent = routingPolicyCountText(n);
  };
  renderRoutingPolicyList(listHost, draft, policiesField, onCount);
  repaintPolicies = () =>
    renderRoutingPolicyList(listHost, draft, policiesField, onCount);
  policyBody.appendChild(listHost);

  // Design 1d's dashed fall-through footer, stated from the live draft
  // rather than the mockup's sample strategy.
  const fallthrough = el("div", "inset");
  const fallthroughText = el("p", "field-help");
  fallthroughText.appendChild(
    document.createTextNode(
      "Requests matching no policy fall through to the default strategy — "
    )
  );
  fallthroughText.appendChild(codeEl(draft.default_strategy || "local_first"));
  fallthroughText.appendChild(
    document.createTextNode(
      draft.allow_remote === false
        ? ", local backends only."
        : ", local and remote backends."
    )
  );
  fallthrough.appendChild(fallthroughText);
  policyBody.appendChild(fallthrough);

  if (!schemaFields.length) return;

  const backendsBody = panel(
    root,
    "Backend overrides",
    "manual entries for specific upstream URLs"
  );
  routingFieldInto(backendsBody, byName.backends, { itemLabel: "backend" });

  // Not in mockup 1d, but the old routing tab exposed every one of these
  // and they belong to no other page — dropping them would take the only
  // control off a config field (Axis D parity).
  const tuningBody = panel(
    root,
    "Limits & timeouts",
    "back-pressure, health probing and upstream HTTP"
  );
  const tuningGrid = el("div", "grid-2");
  [
    "max_in_flight_per_backend",
    "follow_gateway",
    "spillover_max_local_in_flight",
    "health_ttl_s",
    "offline_retry_s",
    "max_backend_failures",
    "upstream_connect_timeout_s",
    "upstream_read_timeout_s",
  ].forEach((name) => routingFieldInto(tuningGrid, byName[name]));
  tuningBody.appendChild(tuningGrid);
}

registerPage("routing", renderRoutingPage);
