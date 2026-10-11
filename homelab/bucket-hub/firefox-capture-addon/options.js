async function load() {
  const cfg = await browser.storage.local.get(["hubBase", "captureToken", "defaultKind"]);
  let hub = cfg.hubBase || "https://bucket-hub.stackrise.com.au";
  // Migrate LAN → public (Proton-safe)
  try {
    const host = new URL(/^https?:\/\//i.test(hub) ? hub : `http://${hub}`).hostname;
    if (
      host === "192.168.50.138" ||
      host === "100.85.42.0" ||
      host === "127.0.0.1" ||
      host === "localhost"
    ) {
      hub = "https://bucket-hub.stackrise.com.au";
    }
  } catch {
    hub = "https://bucket-hub.stackrise.com.au";
  }
  document.getElementById("hubBase").value = hub;
  document.getElementById("captureToken").value = cfg.captureToken || "";
  document.getElementById("defaultKind").value = cfg.defaultKind || "auto";
}

function normalizeHubBase(hubBase) {
  let base = (hubBase || "").trim().replace(/\/+$/, "");
  if (!base) return "";
  if (!/^https?:\/\//i.test(base)) base = `http://${base}`;
  return base.replace(/\/+$/, "");
}

async function requestOrigin(hubBase) {
  const base = normalizeHubBase(hubBase);
  if (!base || !browser.permissions) return true;
  try {
    const u = new URL(base);
    const origin = `${u.protocol}//${u.host}/*`;
    const have = await browser.permissions.contains({ origins: [origin] });
    if (have) return true;
    return await browser.permissions.request({ origins: [origin] });
  } catch {
    return false;
  }
}

document.getElementById("save").addEventListener("click", async () => {
  const hubBase = normalizeHubBase(document.getElementById("hubBase").value);
  const captureToken = document.getElementById("captureToken").value.trim();
  document.getElementById("hubBase").value = hubBase;
  const allowed = await requestOrigin(hubBase);
  await browser.storage.local.set({
    hubBase,
    captureToken,
    defaultKind: document.getElementById("defaultKind").value,
  });
  const msg = document.getElementById("msg");
  if (!allowed) {
    msg.className = "err";
    msg.textContent =
      "Saved, but Firefox denied host access. Allow the permission prompt, or check about:addons → Permissions.";
    return;
  }
  msg.className = "ok";
  msg.textContent =
    "Saved. Reload the temporary add-on on about:debugging. Keep a Hub tab open at this URL while capturing.";
});

document.getElementById("test").addEventListener("click", async () => {
  const hubBase = normalizeHubBase(document.getElementById("hubBase").value);
  const captureToken = document.getElementById("captureToken").value.trim();
  document.getElementById("hubBase").value = hubBase;
  const msg = document.getElementById("msg");
  msg.className = "";
  msg.textContent = "Testing…";
  await requestOrigin(hubBase);
  await browser.storage.local.set({
    hubBase,
    captureToken,
    defaultKind: document.getElementById("defaultKind").value,
  });
  try {
    const data = await browser.runtime.sendMessage({
      type: "test",
      hubBase,
      captureToken,
    });
    msg.className = "ok";
    msg.textContent = `OK — ${data.folders} folder(s) via ${data.via}. ${data.hubBase}`;
  } catch (e) {
    msg.className = "err";
    msg.textContent = String(e.message || e);
  }
});

load();
