/* Generic schema-driven config form engine (docs/config-schema-rewrite-plan.md).
 *
 * Walks a fetched config schema section (GET /netllm/v1/config/schema, built by
 * netllm_core/config_schema.py) and renders one control per field, dispatching
 * on the field's "widget". Every page that edits plain config should go through
 * here rather than hand-rolling inputs — a hand-rolled control is how a config
 * field silently loses its dashboard surface, which the Axis D control-parity
 * kit (tests/conformance/kit_config_surfaces.py) fails on by name.
 *
 * `overrides` is the escape hatch the plan's §6 risk 1 calls for: per-field
 * { label, help, hideLabel, placeholder, step, options, optionLabels,
 *   suggestions, keys, keyLabels, keyPlaceholder, valuePlaceholder, itemLabel,
 *   itemOverrides, newItem, pendingKey, hidden, onChange(value) }
 * for the handful of fields that need bespoke copy or a side effect (e.g.
 * ui.check_for_updates_automatically starting/stopping a poll timer) without
 * forking the whole section back into hand-written code.
 *
 * Loaded after dashboard.js and before pages/*.js — it consumes dashboard.js's
 * primitives (el, textEl, button, switchRow, state, markDirty, render,
 * getByPath, setByPath) and defines none of them.
 *
 * THE PATCH CONTRACT (do not change): buildSchemaSectionPatch /
 * schemaItemToPatch in dashboard.js read the draft objects these rows produce.
 * write_only fields never round-trip a value from the server, so a typed secret
 * is stashed on `draft["_pending_<field>"]` (overridable per field via
 * overrides.pendingKey — swarm.cluster_token keeps its pre-existing
 * "_cluster_token" key) and left for the patch builder, which omits the field
 * entirely when no value was typed so the stored secret survives the save.
 * read_only fields are never rendered at all. Changing either shape silently
 * wipes secrets or emits unwritable keys on save.
 */

/* ---------------- shape guards ---------------- */

/* `x?.field || []` defends against a *missing* field, never against one that is
 * present with the wrong type (`"backends": "oops"`) or an array holding nulls
 * — a hostile or buggy peer/agent sends either and the page throws, hitting the
 * router's "This page failed to render" fallback. asArray/asObject normalise
 * before we iterate; schemaIsRecord filters the elements.
 *
 * These live in dashboard.js (loaded first). Defined here only as a fallback so
 * schema-form.js and every pages/*.js after it can rely on them existing —
 * `typeof` on an undeclared name is safe, and assigning through `window` never
 * collides with a `const`/`function` declaration in dashboard.js. */
if (typeof asArray !== "function") {
  window.asArray = function asArrayFallback(value) {
    return Array.isArray(value) ? value : [];
  };
}
if (typeof asObject !== "function") {
  window.asObject = function asObjectFallback(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  };
}

// A renderable record: a plain object, so `entry.foo` cannot throw.
function schemaIsRecord(value) {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

/* ---------------- shared ids ---------------- */

// Label/input association needs stable-per-render unique ids; a plain
// incrementing counter is enough since the whole page is rebuilt on render().
let _schemaIdSeq = 0;

// Shared incrementing id so multiple <datalist> elements on one page
// (e.g. two model pools) never collide.
let _datalistSeq = 0;

/**
 * Back-compat alias for the pre-redesign checkbox helper. The design has no
 * checkbox — booleans are switches — but enough call sites say `checkboxRow`
 * that keeping the name as a thin wrapper is cheaper than renaming them.
 */
function checkboxRow(label, checked, onChange) {
  return switchRow(label, checked, onChange);
}

/* ---------------- field chrome ---------------- */

function schemaFieldLabel(field, overrides) {
  if (overrides?.label) return overrides.label;
  return field.name.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

// Help copy comes from the pydantic Field(description=...) the schema carries
// as `help`; an override can replace it or suppress it with "".
function schemaFieldHelp(field, overrides) {
  if (overrides && overrides.help !== undefined) return overrides.help;
  return field?.help || "";
}

/**
 * Wraps one control in the standard `.field` shell (caption + control + help).
 * `labelFor` ties the caption to the control for screen readers; omit it for
 * composite widgets (lists, dicts) that have no single focusable input.
 */
function schemaFieldShell(field, overrides, control, labelFor) {
  const wrap = el("div", "field");
  if (!overrides?.hideLabel) {
    const label = textEl("label", "field-label", schemaFieldLabel(field, overrides));
    if (labelFor) label.htmlFor = labelFor;
    wrap.appendChild(label);
  }
  wrap.appendChild(control);
  const help = schemaFieldHelp(field, overrides);
  if (help) wrap.appendChild(textEl("div", "field-help", help));
  return wrap;
}

// One removable row of a collection widget. `.row` is flex; the growing input
// is the only thing here that needs a style (layout, never colour).
function schemaListRow(...nodes) {
  const item = el("div", "row");
  nodes.forEach((n) => item.appendChild(n));
  return item;
}

function schemaTextInput(value, placeholder) {
  const input = el("input");
  input.type = "text";
  input.value = value == null ? "" : value;
  if (placeholder) input.placeholder = placeholder;
  input.style.flex = "1";
  return input;
}

function schemaRemoveButton(onRemove) {
  return button("Remove", "danger", onRemove);
}

/* ---------------- scalar widgets ---------------- */

function schemaFieldRow(field, draft, onDirty, overrides, inputType) {
  const input = el("input");
  input.type = inputType;
  input.id = `sf-${field.name}-${_schemaIdSeq++}`;
  const current = draft[field.name];
  input.value = current === undefined || current === null ? "" : current;
  if (overrides?.placeholder) input.placeholder = overrides.placeholder;
  if (overrides?.step) input.step = overrides.step;
  input.oninput = () => {
    draft[field.name] = inputType === "number" ? Number(input.value) : input.value;
    onDirty();
    if (overrides?.onChange) overrides.onChange(draft[field.name]);
  };
  return schemaFieldShell(field, overrides, input, input.id);
}

function schemaSelectRow(field, draft, onDirty, overrides) {
  const select = el("select");
  select.id = `sf-${field.name}-${_schemaIdSeq++}`;
  // Optional Literal fields (e.g. RoutingPolicy.api_format/strategy) get a
  // leading "unset" option mapping to null, matching what the pydantic
  // model actually allows — a required select has no such option.
  const options = asArray(overrides?.options || field.options);
  const withBlank = field.optional ? ["", ...options] : options;
  withBlank.forEach((opt) => {
    const optionEl = el("option");
    optionEl.value = opt;
    optionEl.textContent = overrides?.optionLabels?.[opt] || opt || "(default)";
    select.appendChild(optionEl);
  });
  select.value = draft[field.name] ?? field.default ?? "";
  select.onchange = () => {
    draft[field.name] = select.value || (field.optional ? null : select.value);
    onDirty();
    if (overrides?.onChange) overrides.onChange(draft[field.name]);
  };
  return schemaFieldShell(field, overrides, select, select.id);
}

// Secret fields (write_only: true) never round-trip a real value from the
// server — draft[field.name] is always "". See THE PATCH CONTRACT above for
// why the typed value lands on a separate pending key instead.
function schemaSecretRow(field, draft, onDirty, overrides) {
  const pendingKey = overrides?.pendingKey || `_pending_${field.name}`;
  const input = el("input");
  input.type = "password";
  input.id = `sf-${field.name}-${_schemaIdSeq++}`;
  input.autocomplete = "off";
  input.placeholder = overrides?.placeholder || "Leave blank to keep existing";
  input.oninput = () => {
    draft[pendingKey] = input.value;
    onDirty();
  };
  return schemaFieldShell(field, overrides, input, input.id);
}

/* ---------------- collection widgets ---------------- */

// Plain list-of-strings (routing.model_aliases values aside — those are
// dict_list_strings below): add/remove rows syncing straight back into
// draft[field.name], generalizing renderStringListEditor (which is keyed by a
// dotted path into the *global* configDraft) to work against any draft object.
//
// Known-good candidates (docs/models-ux-plan.md phase A/D) come from
// `overrides.suggestions` — [{value, label}, ...], label falling back to value
// — rendered as a native <datalist> that autocompletes the text input. Assist,
// not restrict: free typing stays allowed (an offline host is a legitimate
// value).
function schemaListStringsRow(field, draft, onDirty, overrides) {
  const box = el("div", "stack");
  const list = el("div", "stack");
  const suggestions = asArray(overrides?.suggestions).filter(schemaIsRecord);
  let datalistEl = null;
  if (suggestions.length) {
    datalistEl = el("datalist");
    datalistEl.id = `dl-${field.name}-${_datalistSeq++}`;
    suggestions.forEach((s) => {
      const opt = el("option");
      opt.value = s.value;
      if (s.label && s.label !== s.value) opt.label = s.label;
      datalistEl.appendChild(opt);
    });
  }

  function sync() {
    const inputs = list.querySelectorAll("input");
    draft[field.name] = [...inputs].map((i) => i.value.trim()).filter(Boolean);
    onDirty();
  }

  function addRow(value = "") {
    const input = schemaTextInput(value, overrides?.placeholder);
    if (datalistEl) input.setAttribute("list", datalistEl.id);
    input.oninput = sync;
    const item = schemaListRow(input);
    item.appendChild(
      schemaRemoveButton(() => {
        item.remove();
        sync();
      })
    );
    list.appendChild(item);
  }

  // A stored value of the wrong type (config.ui.model_favorites = "abc") is not
  // editable as a string list; render it as empty rather than throwing. sync()
  // rewrites the draft slot from the inputs, so nothing stale survives a save.
  asArray(draft[field.name]).forEach(addRow);
  const add = button(`+ Add ${overrides?.itemLabel || ""}`.trim(), "secondary", () => addRow());
  box.append(list, schemaListRow(add));
  if (datalistEl) box.appendChild(datalistEl);
  return schemaFieldShell(field, overrides, box);
}

// dict[str, list[str]]. `overrides.keys` pins the key set
// (discovery.provider_urls: one row per known provider, no add/remove).
// Without it the keys are user-typed (routing.model_aliases) and the widget
// grows a name field, an add button and a per-key remove — otherwise an alias
// that does not already exist in config.toml could never be created from this
// surface, which is a control that is present but unusable.
function schemaDictListStringsRow(field, draft, onDirty, overrides) {
  const box = el("div", "stack");
  // Normalise in place: a scalar here would otherwise make Object.keys() yield
  // character indices and every row edit write into a value the patch builder
  // cannot use.
  if (!schemaIsRecord(draft[field.name])) draft[field.name] = {};
  const dict = draft[field.name];
  const fixedKeys = overrides?.keys;

  asArray(fixedKeys || Object.keys(dict)).forEach((key) => {
    const group = el("div", "inset");
    const heading = el("div", "row-between");
    heading.appendChild(textEl("div", "field-label", overrides?.keyLabels?.[key] || key));
    if (!fixedKeys) {
      heading.appendChild(
        schemaRemoveButton(() => {
          delete dict[key];
          onDirty();
          render();
        })
      );
    }
    group.appendChild(heading);

    const list = el("div", "stack");
    function sync() {
      const inputs = list.querySelectorAll("input");
      dict[key] = [...inputs].map((i) => i.value.trim()).filter(Boolean);
      onDirty();
    }
    function addRow(value = "") {
      const input = schemaTextInput(value, overrides?.placeholder);
      input.oninput = sync;
      const item = schemaListRow(input);
      item.appendChild(
        schemaRemoveButton(() => {
          item.remove();
          sync();
        })
      );
      list.appendChild(item);
    }
    // discovery.provider_urls.ollama = "http://x" (a string, not a list) is the
    // shape that used to throw here.
    asArray(dict[key]).forEach(addRow);
    const add = button(`+ Add ${overrides?.itemLabel || "URL"}`, "secondary", () => addRow());
    group.append(list, schemaListRow(add));
    box.appendChild(group);
  });

  if (!fixedKeys) {
    const newKey = schemaTextInput("", overrides?.keyPlaceholder || "new key");
    const addKey = button("+ Add", "secondary", () => {
      const name = newKey.value.trim();
      if (!name || Object.prototype.hasOwnProperty.call(dict, name)) return;
      dict[name] = [];
      onDirty();
      render();
    });
    box.appendChild(schemaListRow(newKey, addKey));
  }
  return schemaFieldShell(field, overrides, box);
}

// list[BaseModel] (routing.policies / routing.backends): one removable
// mini-form per item, fields driven by field.item_schema — the same dispatch
// schemaFieldsCard uses for a whole section, just scoped to one array element.
// `overrides.newItem()` supplies the "+ Add" default (the plan's §6 risk 1
// default_factory hint resolves to this via SCHEMA_ITEM_FACTORIES); falls back
// to field.item_schema's own per-field defaults.
function schemaListOfObjectsRow(field, draft, onDirty, overrides) {
  const box = el("div", "stack");
  // Normalise the draft slot itself (not just the iteration): the remove button
  // splices `draft[field.name]` by index, so what we render and what we mutate
  // have to be the same array. Entries that are not objects have no fields to
  // render and cannot be edited back into shape, so they are dropped.
  draft[field.name] = asArray(draft[field.name]).filter(schemaIsRecord);
  const list = el("div", "stack");

  function itemDefault() {
    if (overrides?.newItem) return overrides.newItem();
    const out = {};
    asArray(field.item_schema).filter(schemaIsRecord).forEach((f) => (out[f.name] = f.default));
    return out;
  }

  function addRow(entry) {
    const item = el("div", "inset");
    item.appendChild(
      schemaFieldsCard(field.item_schema || [], entry, onDirty, overrides?.itemOverrides)
    );
    const actions = el("div", "row");
    actions.appendChild(
      schemaRemoveButton(() => {
        const idx = draft[field.name].indexOf(entry);
        if (idx >= 0) draft[field.name].splice(idx, 1);
        item.remove();
        onDirty();
      })
    );
    item.appendChild(actions);
    list.appendChild(item);
  }

  draft[field.name].forEach((entry) => addRow(entry));
  const add = button(`+ Add ${overrides?.itemLabel || "row"}`, "secondary", () => {
    const entry = itemDefault();
    draft[field.name].push(entry);
    addRow(entry);
    onDirty();
  });
  box.append(list, schemaListRow(add));
  return schemaFieldShell(field, overrides, box);
}

// dict[str, str] with arbitrary user-typed keys AND a plain string value
// (routing.sources[].model_rewrites: requested model name -> concrete model
// name). Distinct from schemaDictOfObjectsRow below — that widget's value is an
// object rendered via field.item_schema, which a bare string value doesn't
// have, so reusing it here would silently drop every value (key input only, no
// visible way to edit or preserve the string).
function schemaDictStringsRow(field, draft, onDirty, overrides) {
  const box = el("div", "stack");
  if (!schemaIsRecord(draft[field.name])) draft[field.name] = {};
  const dict = draft[field.name];
  const list = el("div", "stack");

  function addRow(key, value) {
    const keyInput = schemaTextInput(key, overrides?.keyPlaceholder || "key");
    const valueInput = schemaTextInput(value, overrides?.valuePlaceholder || "value");
    keyInput.oninput = () => {
      const newKey = keyInput.value.trim();
      if (!newKey || newKey === key) return;
      delete dict[key];
      dict[newKey] = valueInput.value;
      key = newKey;
      onDirty();
    };
    valueInput.oninput = () => {
      dict[key] = valueInput.value;
      onDirty();
    };
    const item = schemaListRow(keyInput, valueInput);
    item.appendChild(
      schemaRemoveButton(() => {
        delete dict[key];
        item.remove();
        onDirty();
      })
    );
    list.appendChild(item);
  }

  Object.entries(dict).forEach(([key, value]) => addRow(key, value));
  const add = button(`+ Add ${overrides?.itemLabel || "mapping"}`, "secondary", () => {
    dict[""] = "";
    addRow("", "");
    onDirty();
  });
  box.append(list, schemaListRow(add));
  return schemaFieldShell(field, overrides, box);
}

// dict[str, BaseModel] with arbitrary user-typed keys (routing.model_pools:
// pool name -> ModelPool). Cloud's dict[str, CloudProviderConfig] is NOT
// rendered with this widget — its keys are a fixed, server-known registry id
// set, not user-typed, so the cloud page builds per-provider cards directly
// with schemaFieldsCard.
function schemaDictOfObjectsRow(field, draft, onDirty, overrides) {
  const box = el("div", "stack");
  if (!schemaIsRecord(draft[field.name])) draft[field.name] = {};
  const dict = draft[field.name];
  const list = el("div", "stack");

  function addRow(key, entry) {
    const item = el("div", "inset");
    const keyInput = schemaTextInput(key, overrides?.keyPlaceholder || "name");
    keyInput.oninput = () => {
      const newKey = keyInput.value.trim();
      if (!newKey || newKey === key) return;
      delete dict[key];
      dict[newKey] = entry;
      key = newKey;
      onDirty();
    };
    const head = schemaListRow(keyInput);
    head.appendChild(
      schemaRemoveButton(() => {
        delete dict[key];
        item.remove();
        onDirty();
      })
    );
    item.appendChild(head);
    item.appendChild(
      schemaFieldsCard(field.item_schema || [], entry, onDirty, overrides?.itemOverrides)
    );
    list.appendChild(item);
  }

  Object.entries(dict).forEach(([key, entry]) => {
    // Each value is rendered as a sub-form against its own draft slot, so a
    // null/scalar value has to become an editable object first.
    if (!schemaIsRecord(entry)) dict[key] = {};
    addRow(key, dict[key]);
  });
  const add = button(`+ Add ${overrides?.itemLabel || "row"}`, "secondary", () => {
    const entry = {};
    asArray(field.item_schema).filter(schemaIsRecord).forEach((f) => (entry[f.name] = f.default));
    const key = "";
    dict[key] = entry;
    addRow(key, entry);
    onDirty();
  });
  box.append(list, schemaListRow(add));
  return schemaFieldShell(field, overrides, box);
}

// A field whose value is one fixed nested model (SourceConfig.match:
// SourceMatch), not a collection of them — no add/remove/key, just the
// sub-object's own fields rendered inline against its own draft slot.
function schemaNestedObjectRow(field, draft, onDirty, overrides) {
  if (!schemaIsRecord(draft[field.name])) draft[field.name] = {};
  const box = el("div", "inset");
  box.appendChild(
    schemaFieldsCard(field.item_schema || [], draft[field.name], onDirty, overrides?.itemOverrides)
  );
  return schemaFieldShell(field, overrides, box);
}

/* ---------------- dispatch ---------------- */

function schemaRenderField(field, draft, onDirty, overrides) {
  if (field.widget === "toggle") {
    // Absent means "on": every toggle in the config models defaults true, and
    // a defaults-only draft (admin API unreachable) must not read as all-off.
    const checked = draft[field.name] !== false;
    const control = checkboxRow(schemaFieldLabel(field, overrides), checked, (v) => {
      draft[field.name] = v;
      onDirty();
      if (overrides?.onChange) overrides.onChange(v);
    });
    // The switch carries its own visible label, so the shell's caption would
    // just be the same words twice.
    return schemaFieldShell(field, { ...overrides, hideLabel: true }, control);
  }
  if (field.widget === "select") return schemaSelectRow(field, draft, onDirty, overrides);
  if (field.widget === "number") return schemaFieldRow(field, draft, onDirty, overrides, "number");
  if (field.widget === "secret") return schemaSecretRow(field, draft, onDirty, overrides);
  if (field.widget === "list_strings") {
    return schemaListStringsRow(field, draft, onDirty, overrides);
  }
  if (field.widget === "dict_list_strings") {
    return schemaDictListStringsRow(field, draft, onDirty, overrides);
  }
  if (field.widget === "dict_strings") {
    return schemaDictStringsRow(field, draft, onDirty, overrides);
  }
  if (field.widget === "list") return schemaListOfObjectsRow(field, draft, onDirty, overrides);
  if (field.widget === "dict") return schemaDictOfObjectsRow(field, draft, onDirty, overrides);
  if (field.widget === "object") return schemaNestedObjectRow(field, draft, onDirty, overrides);
  // "text" and any unrecognized widget render as a plain text input rather
  // than silently omitting the field.
  return schemaFieldRow(field, draft, onDirty, overrides, "text");
}

/**
 * Render every editable (non-read_only) field in `fields` (a schema section's
 * .fields, or a nested .item_schema) against `draft`, as one vertical stack.
 * `overrides` is keyed by field name — see the header comment above.
 */
function schemaFieldsCard(fields, draft, onDirty, overrides = {}) {
  const stack = el("div", "stack");
  const safeOverrides = asObject(overrides);
  // Every widget assigns into the draft; a non-object draft (a null entry in a
  // list of objects, say) gets a throwaway so the controls still render.
  const safeDraft = asObject(draft);
  // A malformed /config/schema (`fields` a string/number, or holding nulls, or
  // entries with no `name`) is the single payload that breaks the most pages,
  // because every widget below binds to `field.name`. Drop what cannot be bound
  // rather than throwing the whole section away.
  asArray(fields)
    .filter((f) => schemaIsRecord(f) && typeof f.name === "string")
    // read_only fields are not editable anywhere (buildSchemaSectionPatch drops
    // them too); `overrides[name].hidden` opts a field out of the generic
    // layout entirely — e.g. cloud.providers, which the cloud page renders
    // itself because per-provider cards need live registry metadata the schema
    // doesn't carry.
    .filter((f) => !f.read_only && !safeOverrides[f.name]?.hidden)
    .forEach((field) => {
      stack.appendChild(
        schemaRenderField(field, safeDraft, onDirty, safeOverrides[field.name])
      );
    });
  return stack;
}

// The draft slot one section edits. Created on demand so a page still renders
// (and still collects edits) against a defaults-only or partially-populated
// draft — state.configDraft can be either when the admin API is unreachable.
function schemaSectionDraft(sectionKey) {
  if (!state.configDraft || typeof state.configDraft !== "object") state.configDraft = {};
  const current = state.configDraft[sectionKey];
  if (!current || typeof current !== "object") state.configDraft[sectionKey] = {};
  return state.configDraft[sectionKey];
}

function schemaUnavailableNode() {
  return textEl("p", "empty", "Config schema unavailable — check the admin API connection.");
}

function schemaFormNode(sectionKey, schema, draft, onDirty, overrides) {
  const section = schema?.sections?.[sectionKey];
  if (!section) return schemaUnavailableNode();
  return schemaFieldsCard(section.fields, draft, onDirty, overrides);
}

/**
 * Render one config schema section's editable fields.
 *
 *   renderSchemaForm(root, sectionKey, overrides)
 *
 * appends into `root` against state.configSchema / state.configDraft and
 * returns the node. The pre-redesign call shape
 *
 *   renderSchemaForm(sectionKey, schema, draft, onDirty, overrides)
 *
 * still works and returns the node without appending — sections are migrated
 * page by page, and the two are told apart by whether the first argument is a
 * string (a section key) or a node.
 */
function renderSchemaForm(a, b, c, d, e) {
  if (typeof a === "string") return schemaFormNode(a, b, c || {}, d || markDirty, e || {});
  const node = schemaFormNode(b, state.configSchema, schemaSectionDraft(b), markDirty, c || {});
  if (a) a.appendChild(node);
  return node;
}

/**
 * Render a single schema field. Same dual call shape as renderSchemaForm:
 *
 *   renderSchemaField(root, sectionKey, field, overrides)   // appends
 *   renderSchemaField(field, draft, onDirty, overrides)     // returns only
 *
 * discriminated on whether the first argument is a node (a field spec is a
 * plain object and has no appendChild).
 */
function renderSchemaField(a, b, c, d) {
  if (a && typeof a.appendChild === "function") {
    const node = schemaRenderField(c, schemaSectionDraft(b), markDirty, d);
    a.appendChild(node);
    return node;
  }
  return schemaRenderField(a, b, c || markDirty, d);
}

// Resolves a schema field's "default_factory" hint (§6 risk 1 — a named builder
// for a sensible "Add row" default instead of an empty one) to the actual JS
// factory function.
const SCHEMA_ITEM_FACTORIES = {
  local_openai_policy: () => ({
    name: "local-openai",
    model_prefix: "",
    api_format: "openai",
    strategy: null,
    prefer_provider: null,
    allow_cloud: false,
    enabled: true,
  }),
};

/**
 * String-list editor keyed by a dotted path into the *global* configDraft
 * (swarm.peers, discovery.*), for the call sites that predate the schema
 * document and edit a path rather than a field spec.
 */
function renderStringListEditor(path, placeholder) {
  const box = el("div", "stack");
  const list = el("div", "stack");
  // swarm.peers / discovery.* arrive from a config draft the admin API may have
  // handed us in any shape; a scalar there renders as an empty editor.
  const items = asArray(getByPath(state.configDraft, path));

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
    const input = schemaTextInput(value, placeholder);
    input.oninput = sync;
    const item = schemaListRow(input);
    item.appendChild(
      schemaRemoveButton(() => {
        item.remove();
        sync();
      })
    );
    list.appendChild(item);
  }

  items.forEach(addRow);
  const add = button("+ Add", "secondary", () => addRow());
  box.append(list, schemaListRow(add));
  return box;
}
