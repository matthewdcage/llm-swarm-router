/**
 * Bucket-HUB — VPN status badge centered in Glance page header.
 * Polls same-origin /hub-form/api/status (hub-gateway).
 * Styles are injected here so the badge works even if custom CSS 404s.
 */
(function () {
  const STATUS_URL = "/hub-form/api/status";
  const POLL_MS = 5000;
  const STYLE_ID = "hub-vpn-style";

  function ensureStyles() {
    if (document.getElementById(STYLE_ID)) return;
    const s = document.createElement("style");
    s.id = STYLE_ID;
    s.textContent = `
.header.hub-header-has-vpn,
.header-container.hub-header-has-vpn .header {
  position: relative !important;
}
#hub-vpn-badge {
  position: absolute !important;
  left: 50% !important;
  top: 50% !important;
  transform: translate(-50%, -50%) !important;
  z-index: 40 !important;
  display: inline-flex !important;
  align-items: center !important;
  gap: 0.45rem !important;
  max-width: min(56vw, 30rem) !important;
  margin: 0 !important;
  padding: 0.3rem 0.75rem !important;
  border-radius: 999px !important;
  border: 1px solid hsl(240 8% 24%) !important;
  background: hsl(240 12% 9% / 0.95) !important;
  color: hsl(240 5% 68%) !important;
  font-size: 0.78rem !important;
  font-weight: 600 !important;
  letter-spacing: 0.01em !important;
  line-height: 1.2 !important;
  white-space: nowrap !important;
  overflow: hidden !important;
  text-overflow: ellipsis !important;
  pointer-events: none !important;
  box-shadow: 0 0 0 1px hsl(240 10% 4% / 0.5) !important;
}
#hub-vpn-badge .hub-vpn-dot {
  width: 0.55rem;
  height: 0.55rem;
  border-radius: 50%;
  flex-shrink: 0;
  background: currentColor;
}
#hub-vpn-badge.ok {
  color: hsl(145 45% 55%) !important;
  border-color: hsl(145 35% 32%) !important;
}
#hub-vpn-badge.ok .hub-vpn-dot {
  animation: hub-vpn-pulse 1.5s ease-in-out infinite;
  box-shadow: 0 0 0 0 hsl(145 45% 55% / 0.55);
}
#hub-vpn-badge.bad {
  color: hsl(0 70% 62%) !important;
  border-color: hsl(0 45% 36%) !important;
}
#hub-vpn-badge.unk {
  color: hsl(240 5% 55%) !important;
}
@keyframes hub-vpn-pulse {
  0%, 100% { opacity: 1; box-shadow: 0 0 0 0 hsl(145 45% 55% / 0.5); }
  50% { opacity: 0.4; box-shadow: 0 0 0 7px hsl(145 45% 55% / 0); }
}
@media (max-width: 900px) {
  #hub-vpn-badge {
    max-width: min(72vw, 18rem) !important;
    font-size: 0.68rem !important;
    padding: 0.22rem 0.55rem !important;
  }
}
`;
    document.head.appendChild(s);
  }

  function ensureBadge() {
    ensureStyles();
    const header =
      document.querySelector(".header") ||
      document.querySelector(".header-container");
    if (!header) return null;
    header.classList.add("hub-header-has-vpn");
    let el = document.getElementById("hub-vpn-badge");
    if (el) return el;
    el = document.createElement("div");
    el.id = "hub-vpn-badge";
    el.className = "unk";
    el.setAttribute("role", "status");
    el.setAttribute("aria-live", "polite");
    el.innerHTML =
      '<span class="hub-vpn-dot" aria-hidden="true"></span><span class="hub-vpn-text">Checking VPN…</span>';
    header.appendChild(el);
    return el;
  }

  function setText(el, text) {
    const t = el.querySelector(".hub-vpn-text");
    if (t) t.textContent = text;
    else el.textContent = text;
  }

  function paint(el, data) {
    const w = (data && data.worker) || {};
    const live = !!(data && data.vpn_live);
    const stale = !!(data && data.vpn_stale);
    const age = data && data.heartbeat_age_s;
    const detail = String(w.vpn_detail || "").trim();
    const ageTxt = age != null ? " · " + Math.round(age) + "s ago" : "";

    if (live) {
      el.className = "ok";
      const label =
        "VPN live · Proton" + (detail ? " · " + detail : "") + ageTxt;
      setText(el, label);
      el.title = detail
        ? "Proton via Gluetun · " + detail + ageTxt
        : "Proton via Gluetun · fail-closed when down";
      return;
    }
    el.className = "bad";
    if (stale) {
      const label =
        "VPN unknown · heartbeat stale" +
        (age != null ? " (" + Math.round(age) + "s)" : "") +
        " — fail-closed";
      setText(el, label);
      el.title = "Worker heartbeat stale — downloads blocked until VPN recovers";
    } else {
      const label =
        "VPN down" +
        (detail ? " · " + detail : "") +
        " — downloads blocked";
      setText(el, label);
      el.title = detail
        ? "VPN down · " + detail
        : "VPN down — Hub fail-closed; no search/download egress";
    }
  }

  async function tick() {
    const el = ensureBadge();
    if (!el) return;
    try {
      const r = await fetch(STATUS_URL, { cache: "no-store" });
      if (!r.ok) throw new Error("status " + r.status);
      paint(el, await r.json());
    } catch (_) {
      el.className = "bad";
      setText(el, "VPN status unavailable");
    }
  }

  function boot() {
    ensureBadge();
    tick();
    setInterval(tick, POLL_MS);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
