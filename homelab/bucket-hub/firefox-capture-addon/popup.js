function detectMapKey(url, kind) {
  const k = (kind || "auto").toLowerCase();
  if (k === "media" || k === "website" || k === "github" || k === "hf") return k;
  const u = (url || "").toLowerCase();
  if (u.includes("github.com")) return "github";
  if (u.includes("huggingface.co")) return "hf";
  if (
    u.includes("youtube.com") ||
    u.includes("youtu.be") ||
    u.includes("vimeo.com") ||
    u.includes("tiktok.com")
  ) {
    return "media";
  }
  if (u.startsWith("magnet:") || u.endsWith(".torrent")) return "magnet";
  return "website";
}

function applyFolderFromMap(destSel, typeFolderMap, mapKey, preferred) {
  const row = (typeFolderMap && (typeFolderMap[mapKey] || typeFolderMap.default)) || null;
  const wantDrive = (preferred && preferred.driveId) || (row && row.drive_id) || "";
  const wantFolder = (preferred && preferred.folderId) || (row && row.folder_id) || "";
  if (!wantDrive || !wantFolder) {
    destSel.value = "";
    return;
  }
  const val = `${wantDrive}|${wantFolder}`;
  for (const opt of destSel.options) {
    if (opt.value === val) {
      destSel.value = val;
      return;
    }
  }
  destSel.value = "";
}

async function main() {
  const [tab] = await browser.tabs.query({ active: true, currentWindow: true });
  const pageUrl = (tab && tab.url) || "";
  const pageTitle = (tab && tab.title) || "";
  document.getElementById("pageUrl").textContent = pageUrl || "(no URL)";

  const cfg = await browser.storage.local.get([
    "hubBase",
    "captureToken",
    "defaultKind",
    "driveId",
    "folderId",
  ]);
  const kindSel = document.getElementById("kind");
  // Always start from Auto so each page is segmented from its URL.
  // (Sticky "media" from a prior YouTube send was mis-filing GitHub as video.)
  kindSel.value = "auto";

  const status = document.getElementById("status");
  const destSel = document.getElementById("dest");
  const sendBtn = document.getElementById("send");
  let typeFolderMap = {};

  function refreshFolderHint() {
    const mapKey = detectMapKey(pageUrl, kindSel.value);
    applyFolderFromMap(destSel, typeFolderMap, mapKey, null);
    const row = typeFolderMap[mapKey] || typeFolderMap.default;
    if (row && destSel.value) {
      const label = destSel.selectedOptions[0]
        ? destSel.selectedOptions[0].textContent
        : `${row.drive_id}/${row.folder_id}`;
      status.className = "";
      status.textContent = `File type → ${mapKey} → ${label}`;
    }
  }

  if (!cfg.captureToken) {
    status.className = "err";
    status.textContent =
      "Open Options: set capture token + Hub URL https://bucket-hub.stackrise.com.au";
  } else {
    status.textContent = "Connecting…";
    try {
      const data = await browser.runtime.sendMessage({ type: "destinations" });
      const list = data.destinations || [];
      typeFolderMap = data.type_folder_map || {};
      for (const d of list) {
        const opt = document.createElement("option");
        opt.value = `${d.drive_id}|${d.folder_id}`;
        opt.textContent = d.label;
        destSel.appendChild(opt);
      }
      refreshFolderHint();
      if (!list.length) {
        status.className = "";
        status.textContent =
          "Ready (Hub will pick folder from file type). Keep Hub tab open.";
      } else if (!status.textContent.startsWith("File type")) {
        status.className = "";
        status.textContent = `${list.length} folder(s) · ${data.hubBase || "Hub"}`;
      }
    } catch (e) {
      status.className = "err";
      status.textContent = String(e.message || e);
    }
  }

  kindSel.addEventListener("change", refreshFolderHint);

  document.getElementById("opts").addEventListener("click", () => {
    browser.runtime.openOptionsPage();
  });

  sendBtn.addEventListener("click", async () => {
    if (!pageUrl || pageUrl.startsWith("about:")) {
      status.className = "err";
      status.textContent = "Open a normal http(s) page first (not a new tab).";
      return;
    }
    sendBtn.disabled = true;
    sendBtn.textContent = "Sending…";
    status.className = "";
    status.textContent = "Sending… (watch for a Firefox notification)";
    const kind = kindSel.value;
    const dest = destSel.value;
    let drive_id = "";
    let folder_id = "";
    // Empty dest → Hub applies type_folder_map. Explicit pick overrides.
    if (dest.includes("|")) {
      [drive_id, folder_id] = dest.split("|");
    }
    try {
      const data = await browser.runtime.sendMessage({
        type: "capture",
        body: {
          url: pageUrl,
          title: pageTitle,
          kind,
          drive_id,
          folder_id,
        },
      });
      const destLabel = (data.job && data.job.destination) || "queue";
      const jobId = (data.job && data.job.id) || "";
      status.className = "ok";
      status.textContent = jobId
        ? `Queued ${jobId} → ${destLabel} (Proton)`
        : `Queued → ${destLabel} (Proton)`;
      sendBtn.textContent = "Queued";
      await browser.storage.local.set({
        defaultKind: "auto",
        driveId: drive_id,
        folderId: folder_id,
      });
    } catch (err) {
      status.className = "err";
      status.textContent = String(err.message || err);
      sendBtn.disabled = false;
      sendBtn.textContent = "Send";
    }
  });
}

main();
