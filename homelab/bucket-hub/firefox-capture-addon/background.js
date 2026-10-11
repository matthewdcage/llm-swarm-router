/* Bucket-HUB Capture — background (Firefox) v1.0.4
 *
 * Prefer https://bucket-hub.stackrise.com.au (Proton + Access).
 * Never open LAN Hub from the popup — tabs.create closes the popup
 * before status can render. Reuse an existing Hub tab, or notify.
 */

const PUBLIC_HUB = "https://bucket-hub.stackrise.com.au";

function normalizeHubBase(hubBase) {
  let base = (hubBase || "").trim().replace(/\/+$/, "");
  if (!base) return "";
  if (!/^https?:\/\//i.test(base)) base = `http://${base}`;
  return base.replace(/\/+$/, "");
}

/** LAN bases fail under full-tunnel Proton — migrate to public Hub. */
function preferPublicHub(hubBase) {
  const base = normalizeHubBase(hubBase);
  if (!base) return PUBLIC_HUB;
  try {
    const host = new URL(base).hostname;
    if (
      host === "192.168.50.138" ||
      host === "100.85.42.0" ||
      host === "127.0.0.1" ||
      host === "localhost"
    ) {
      return PUBLIC_HUB;
    }
  } catch {
    return PUBLIC_HUB;
  }
  return base;
}

async function resolvedHubBase(stored) {
  const next = preferPublicHub(stored);
  if (next !== normalizeHubBase(stored || "")) {
    await browser.storage.local.set({ hubBase: next });
  }
  return next;
}

function captureEndpoint(hubBase) {
  const base = normalizeHubBase(hubBase);
  if (!base) return "";
  if (base.includes("/hub-form")) return `${base}/api/capture`;
  if (/:3020$/i.test(base)) return `${base}/api/capture`;
  return `${base}/hub-form/api/capture`;
}

function destinationsEndpoint(hubBase) {
  return captureEndpoint(hubBase).replace(/\/api\/capture$/, "/api/capture/destinations");
}

function hubLandingUrl(hubBase) {
  const base = normalizeHubBase(hubBase);
  if (!base) return "";
  if (base.includes("/hub-form")) return `${base.replace(/\/$/, "")}/`;
  if (/:3020$/i.test(base)) return `${base}/`;
  return `${base}/hub-form/`;
}

function originPattern(hubBase) {
  try {
    const u = new URL(normalizeHubBase(hubBase));
    return `${u.protocol}//${u.host}/*`;
  } catch {
    return "";
  }
}

async function ensureHostPermission(hubBase) {
  const origin = originPattern(hubBase);
  if (!origin || !browser.permissions) return true;
  try {
    const have = await browser.permissions.contains({ origins: [origin] });
    if (have) return true;
    return await browser.permissions.request({ origins: [origin] });
  } catch {
    return false;
  }
}

function isNetworkError(err) {
  const msg = String((err && err.message) || err || "");
  return /NetworkError|Failed to fetch|NetworkError when attempting/i.test(msg);
}

async function waitTabComplete(tabId, timeoutMs = 20000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const tab = await browser.tabs.get(tabId);
    if (tab.status === "complete" && tab.url && !tab.url.startsWith("about:")) {
      return tab;
    }
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error("Hub tab did not finish loading (finish Cloudflare Access login first)");
}

async function findHubTab(hubBase) {
  const base = normalizeHubBase(hubBase);
  const origin = new URL(base).origin;
  const existing = await browser.tabs.query({ url: `${origin}/*` });
  if (!existing.length) return null;
  const tab = existing[0];
  if (tab.status !== "complete") await waitTabComplete(tab.id);
  return tab;
}

async function openHubTab(hubBase) {
  const landing = hubLandingUrl(hubBase);
  const tab = await browser.tabs.create({ url: landing, active: false });
  await waitTabComplete(tab.id);
  return tab;
}

async function notify(title, message) {
  try {
    await browser.notifications.create({
      type: "basic",
      title,
      message: String(message).slice(0, 250),
    });
  } catch {
    /* notifications optional */
  }
}

/** Same-origin fetch inside a Hub page (Access cookies apply). */
async function fetchViaHubTab(tab, url, init) {
  const results = await browser.tabs.executeScript(tab.id, {
    code: `
      (async () => {
        const url = ${JSON.stringify(url)};
        const init = ${JSON.stringify({
          method: init.method || "GET",
          headers: init.headers || {},
          body: init.body || null,
        })};
        try {
          const r = await fetch(url, init);
          const text = await r.text();
          return { ok: r.ok, status: r.status, text };
        } catch (e) {
          return { ok: false, status: 0, text: "", error: String(e && e.message || e) };
        }
      })()
    `,
  });
  const result = results && results[0];
  if (!result) throw new Error("Hub tab inject returned nothing");
  if (result.error) throw new Error(result.error);
  let data = {};
  try {
    data = result.text ? JSON.parse(result.text) : {};
  } catch {
    data = { detail: result.text.slice(0, 200) };
  }
  return { ok: result.ok, status: result.status, data };
}

async function hubFetch(hubBase, url, init, { allowCreateTab = false } = {}) {
  await ensureHostPermission(hubBase);
  try {
    const r = await fetch(url, init);
    // Cloudflare Access login HTML often returns 200 — require JSON-ish success paths
    const ct = (r.headers.get("content-type") || "").toLowerCase();
    if (ct.includes("text/html") && !ct.includes("json")) {
      throw new Error("NetworkError: got Access/login HTML (use open Hub tab)");
    }
    const data = await r.json().catch(() => ({}));
    return { ok: r.ok, status: r.status, data, via: "direct" };
  } catch (err) {
    if (!isNetworkError(err) && !/Access\/login HTML/i.test(String(err.message || err))) {
      throw err;
    }
    let tab = await findHubTab(hubBase);
    if (!tab && allowCreateTab) {
      tab = await openHubTab(hubBase);
    }
    if (!tab) {
      throw new Error(
        `Open ${hubLandingUrl(hubBase)} in a Firefox tab (Access logged in), then retry. ` +
          "Do not use the LAN IP under Proton."
      );
    }
    const viaTab = await fetchViaHubTab(tab, url, init);
    return { ...viaTab, via: "hub-tab" };
  }
}

function networkHint(hubBase, err) {
  const url = destinationsEndpoint(hubBase) || "(no URL)";
  const msg = String((err && err.message) || err || "NetworkError");
  return (
    `${msg}\nTried: ${url}\n` +
    `Use Hub URL ${PUBLIC_HUB}. Keep that Hub tab open while capturing.`
  );
}

async function sendCapture(cfg, body) {
  const token = (cfg.captureToken || "").trim();
  const hubBase = await resolvedHubBase(cfg.hubBase);
  const url = captureEndpoint(hubBase);
  if (!url || !token) {
    throw new Error("Configure Hub URL and capture token in extension options");
  }
  let result;
  try {
    result = await hubFetch(
      hubBase,
      url,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Capture-Token": token,
        },
        body: JSON.stringify(body),
      },
      { allowCreateTab: true }
    );
  } catch (err) {
    const hint = networkHint(hubBase, err);
    await notify("Bucket-HUB capture failed", hint);
    throw new Error(hint);
  }
  if (!result.ok) {
    const detail =
      typeof result.data.detail === "string"
        ? result.data.detail
        : JSON.stringify(result.data.detail || result.data);
    const msg = detail || `HTTP ${result.status}`;
    await notify("Bucket-HUB capture failed", msg);
    throw new Error(msg);
  }
  const destLabel = (result.data.job && result.data.job.destination) || "queue";
  const jobId = (result.data.job && result.data.job.id) || "";
  await notify(
    "Bucket-HUB queued",
    `${jobId ? jobId + " → " : ""}${destLabel} (Proton)`
  );
  return result.data;
}

async function loadDestinations(cfg) {
  const token = (cfg.captureToken || "").trim();
  const hubBase = await resolvedHubBase(cfg.hubBase);
  const url = destinationsEndpoint(hubBase);
  if (!url || !token) return { destinations: [], kinds: [], hubBase };
  let result;
  try {
    // Never create a tab here — that closes the popup.
    result = await hubFetch(
      hubBase,
      url,
      { method: "GET", headers: { "X-Capture-Token": token } },
      { allowCreateTab: false }
    );
  } catch (err) {
    throw new Error(networkHint(hubBase, err));
  }
  if (!result.ok) {
    const detail =
      typeof result.data.detail === "string"
        ? result.data.detail
        : `HTTP ${result.status}`;
    throw new Error(`Destinations failed: ${detail}`);
  }
  return { ...result.data, hubBase };
}

async function testConnection(cfg) {
  const hubBase = await resolvedHubBase(cfg.hubBase);
  const token = (cfg.captureToken || "").trim();
  if (!token) throw new Error("Set capture token first");
  // Allow create on Test from Options page (not popup).
  const url = destinationsEndpoint(hubBase);
  const result = await hubFetch(
    hubBase,
    url,
    { method: "GET", headers: { "X-Capture-Token": token } },
    { allowCreateTab: true }
  );
  if (!result.ok) {
    throw new Error(
      typeof result.data.detail === "string"
        ? result.data.detail
        : `HTTP ${result.status}`
    );
  }
  return {
    ok: true,
    folders: (result.data.destinations || []).length,
    url,
    hubBase,
    via: result.via,
  };
}

browser.runtime.onInstalled.addListener(async () => {
  const cfg = await browser.storage.local.get(["hubBase"]);
  await resolvedHubBase(cfg.hubBase || PUBLIC_HUB);
  await browser.contextMenus.removeAll();
  browser.contextMenus.create({
    id: "hub-send-page",
    title: "Send page to Bucket-HUB",
    contexts: ["page"],
  });
  browser.contextMenus.create({
    id: "hub-send-link",
    title: "Send link to Bucket-HUB",
    contexts: ["link"],
  });
});

browser.contextMenus.onClicked.addListener(async (info, tab) => {
  const cfg = await browser.storage.local.get([
    "hubBase",
    "captureToken",
    "defaultKind",
    "driveId",
    "folderId",
  ]);
  const url =
    info.menuItemId === "hub-send-link" ? info.linkUrl : (tab && tab.url) || "";
  const title = (tab && tab.title) || "";
  if (!url) return;
  try {
    await sendCapture(cfg, {
      url,
      title,
      kind: cfg.defaultKind || "media",
      drive_id: cfg.driveId || "",
      folder_id: cfg.folderId || "",
    });
  } catch (err) {
    console.error("Bucket-HUB capture failed", err);
  }
});

browser.runtime.onMessage.addListener((msg) => {
  if (msg && msg.type === "capture") {
    return browser.storage.local
      .get(["hubBase", "captureToken", "defaultKind", "driveId", "folderId"])
      .then((cfg) => sendCapture(cfg, msg.body));
  }
  if (msg && msg.type === "destinations") {
    return browser.storage.local
      .get(["hubBase", "captureToken"])
      .then((cfg) => loadDestinations(cfg));
  }
  if (msg && msg.type === "test") {
    return browser.storage.local.get(["hubBase", "captureToken"]).then((cfg) => {
      if (msg.hubBase) cfg.hubBase = msg.hubBase;
      if (msg.captureToken) cfg.captureToken = msg.captureToken;
      return testConnection(cfg);
    });
  }
  return undefined;
});
