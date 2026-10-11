/**
 * Bucket-HUB — Requests badge + review modal in Glance header.
 * Polls /hub-form/api/requests/summary; modal loads review jobs for approve/dismiss.
 * Styles live in glance-hub.css (cache-busted by Glance).
 */
(function () {
  const SUMMARY_URL = "/hub-form/api/requests/summary";
  const JOBS_URL = "/hub-form/api/jobs?status=review&limit=40";
  const POLL_MS = 8000;

  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function removeThemePickers() {
    document.querySelectorAll(".header .theme-picker").forEach((el) => el.remove());
    document
      .querySelectorAll(".mobile-navigation-actions .theme-picker")
      .forEach((el) => el.remove());
    document.getElementById("hub-storage-btn")?.remove();
  }

  function ensureTray() {
    const header = document.querySelector(".header");
    if (!header) return null;

    removeThemePickers();

    let tray = document.getElementById("hub-header-tray");
    if (!tray) {
      tray = document.createElement("div");
      tray.id = "hub-header-tray";
      tray.setAttribute("aria-label", "Hub actions");
      header.appendChild(tray);
    }

    header.classList.add("hub-header-has-tray");
    return tray;
  }

  function ensureModal() {
    let root = document.getElementById("hub-requests-modal");
    if (root) return root;

    root = document.createElement("div");
    root.id = "hub-requests-modal";
    root.className = "hub-modal hidden";
    root.setAttribute("role", "dialog");
    root.setAttribute("aria-modal", "true");
    root.setAttribute("aria-labelledby", "hub-requests-modal-title");
    root.innerHTML = `
      <div class="hub-modal-backdrop" data-hub-modal-close></div>
      <div class="hub-modal-panel">
        <div class="hub-modal-header">
          <h2 id="hub-requests-modal-title">Requests <span id="hub-requests-modal-count"></span></h2>
          <button type="button" class="hub-modal-close" data-hub-modal-close aria-label="Close">×</button>
        </div>
        <p class="hub-modal-sub">Approve with a URL or dismiss.</p>
        <div id="hub-requests-modal-list" class="hub-modal-list">
          <p class="hub-modal-empty">Loading…</p>
        </div>
        <p id="hub-requests-modal-msg" class="hub-modal-msg" role="status" aria-live="polite"></p>
      </div>
    `;
    document.body.appendChild(root);

    root.addEventListener("click", (e) => {
      const t = e.target;
      if (t && t.closest && t.closest("[data-hub-modal-close]")) {
        closeModal();
      }
    });

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !root.classList.contains("hidden")) {
        closeModal();
      }
    });

    return root;
  }

  function setModalMsg(text, ok) {
    const el = document.getElementById("hub-requests-modal-msg");
    if (!el) return;
    el.textContent = text || "";
    el.classList.toggle("ok", !!ok);
    el.classList.toggle("err", text && !ok);
  }

  function renderRow(j) {
    const title = escapeHtml(j.title || j.kind || j.id);
    const body = j.url
      ? `<div class="hub-req-body">${escapeHtml(j.url)}</div>`
      : `<div class="hub-req-body">${escapeHtml(j.notes || j.title || "")}</div>`;
    const err = j.error
      ? `<div class="hub-req-err">${escapeHtml(j.error)}</div>`
      : "";
    const meta = `${escapeHtml(j.folder_name || "")} · ${
      j.created_at ? escapeHtml(String(j.created_at).slice(0, 16).replace("T", " ")) : ""
    }`;
    const by = j.requested_by
      ? `<span class="hub-req-by">@${escapeHtml(j.requested_by)}</span>`
      : "";
    const urlVal = j.url ? escapeHtml(j.url) : "";
    return `<article class="hub-req-item" data-job-id="${escapeHtml(j.id)}">
      <div class="hub-req-top">
        <strong>${title}</strong>
        ${by}
        <span class="hub-req-status">${escapeHtml(j.status)}</span>
      </div>
      ${body}${err}
      <div class="hub-req-meta">${meta}</div>
      <div class="hub-req-actions">
        <input type="text" class="hub-req-url" data-job="${escapeHtml(j.id)}" placeholder="Paste URL to approve" value="${urlVal}"/>
        <button type="button" class="hub-req-btn approve" data-approve="${escapeHtml(j.id)}">Approve</button>
        <button type="button" class="hub-req-btn dismiss" data-dismiss="${escapeHtml(j.id)}">Dismiss</button>
      </div>
    </article>`;
  }

  async function approve(id, url) {
    setModalMsg("");
    const r = await fetch(`/hub-form/api/jobs/${encodeURIComponent(id)}/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: url || "" }),
    });
    let data = {};
    try {
      data = await r.json();
    } catch (_) {
      /* ignore */
    }
    if (!r.ok) {
      setModalMsg(
        typeof data.detail === "string"
          ? data.detail
          : JSON.stringify(data.detail || data),
        false
      );
      return;
    }
    setModalMsg("Approved", true);
    await loadModalList();
    await poll();
  }

  async function dismiss(id) {
    setModalMsg("");
    const r = await fetch(`/hub-form/api/jobs/${encodeURIComponent(id)}/dismiss`, {
      method: "POST",
    });
    let data = {};
    try {
      data = await r.json();
    } catch (_) {
      /* ignore */
    }
    if (!r.ok) {
      setModalMsg(
        typeof data.detail === "string"
          ? data.detail
          : JSON.stringify(data.detail || data),
        false
      );
      return;
    }
    setModalMsg("Dismissed", true);
    await loadModalList();
    await poll();
  }

  async function loadModalList() {
    const list = document.getElementById("hub-requests-modal-list");
    const countEl = document.getElementById("hub-requests-modal-count");
    if (!list) return;
    list.innerHTML = '<p class="hub-modal-empty">Loading…</p>';
    try {
      const r = await fetch(JOBS_URL, { cache: "no-store" });
      if (!r.ok) throw new Error(String(r.status));
      const data = await r.json();
      const jobs = (data.jobs || []).filter((j) => j.status === "review");
      if (countEl) {
        countEl.textContent = jobs.length ? `(${jobs.length})` : "";
      }
      if (!jobs.length) {
        list.innerHTML = '<p class="hub-modal-empty">No requests yet.</p>';
        return;
      }
      list.innerHTML = jobs.map(renderRow).join("");
      list.querySelectorAll("[data-approve]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const id = btn.getAttribute("data-approve");
          const input = list.querySelector(`.hub-req-url[data-job="${id}"]`);
          approve(id, input ? input.value.trim() : "");
        });
      });
      list.querySelectorAll("[data-dismiss]").forEach((btn) => {
        btn.addEventListener("click", () => {
          dismiss(btn.getAttribute("data-dismiss"));
        });
      });
    } catch (_) {
      list.innerHTML = '<p class="hub-modal-empty">Could not load Requests.</p>';
    }
  }

  function openModal() {
    const root = ensureModal();
    root.classList.remove("hidden");
    document.body.classList.add("hub-modal-open");
    setModalMsg("");
    loadModalList();
    const closeBtn = root.querySelector(".hub-modal-close");
    if (closeBtn) closeBtn.focus();
  }

  function closeModal() {
    const root = document.getElementById("hub-requests-modal");
    if (!root) return;
    root.classList.add("hidden");
    document.body.classList.remove("hub-modal-open");
  }

  function ensureButton() {
    const tray = ensureTray();
    if (!tray) return null;

    ensureModal();

    let el = document.getElementById("hub-requests-btn");
    if (!el) {
      el = document.createElement("button");
      el.type = "button";
      el.id = "hub-requests-btn";
      el.className = "hub-header-btn";
      el.title = "Requests";
      el.setAttribute("aria-label", "Requests");
      el.setAttribute("aria-haspopup", "dialog");
      el.innerHTML =
        '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16v12H7l-3 3V5z"/></svg>' +
        '<span id="hub-requests-count" class="hidden">0</span>';
      el.addEventListener("click", (e) => {
        e.preventDefault();
        openModal();
      });
      tray.appendChild(el);
    } else if (el.parentElement !== tray) {
      tray.appendChild(el);
    } else if (el.tagName === "A") {
      /* migrate old cached anchor → button behaviour */
      el.removeAttribute("href");
      el.addEventListener("click", (e) => {
        e.preventDefault();
        openModal();
      });
    }
    return el;
  }

  async function poll() {
    const btn = ensureButton();
    if (!btn) return;
    const badge = document.getElementById("hub-requests-count");
    try {
      const r = await fetch(SUMMARY_URL, { cache: "no-store" });
      if (!r.ok) throw new Error(String(r.status));
      const data = await r.json();
      const n = Number(data.pending) || 0;
      if (n > 0) {
        badge.textContent = n > 99 ? "99+" : String(n);
        badge.classList.remove("hidden");
        btn.setAttribute("aria-label", `Requests (${n} pending)`);
      } else {
        badge.classList.add("hidden");
        btn.setAttribute("aria-label", "Requests");
      }
    } catch (_) {
      /* keep last badge state */
    }
  }

  function start() {
    ensureButton();
    poll();
    setInterval(poll, POLL_MS);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
