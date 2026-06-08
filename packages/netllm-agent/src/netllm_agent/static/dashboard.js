function showToast(msg) {
  const toast = document.getElementById("toast");
  toast.textContent = msg;
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 2200);
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function clearChildren(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
}

function makeTable(headers, rows, cellFn) {
  const wrap = document.createElement("div");
  if (!rows.length) {
    const p = document.createElement("p");
    p.className = "sub";
    p.textContent = "None";
    wrap.appendChild(p);
    return wrap;
  }
  const table = document.createElement("table");
  const thead = document.createElement("thead");
  const hr = document.createElement("tr");
  for (const h of headers) {
    const th = document.createElement("th");
    th.textContent = h;
    hr.appendChild(th);
  }
  thead.appendChild(hr);
  table.appendChild(thead);
  const tbody = document.createElement("tbody");
  for (const row of rows) {
    const tr = document.createElement("tr");
    for (const cell of cellFn(row)) {
      const td = document.createElement("td");
      if (typeof cell === "string") {
        td.textContent = cell;
      } else {
        td.appendChild(cell);
      }
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  wrap.appendChild(table);
  return wrap;
}

function statusBadge(status) {
  const span = document.createElement("span");
  span.className = "badge " + (status === "online" ? "ok" : "warn");
  span.textContent = status || "unknown";
  return span;
}

function codeText(text) {
  const code = document.createElement("code");
  code.textContent = text;
  return code;
}

async function loadDashboard() {
  const [status, models, doctor, config, env] = await Promise.all([
    fetch("/netllm/v1/status").then((r) => r.json()),
    fetch("/v1/models").then((r) => r.json()),
    fetch("/netllm/v1/doctor").then((r) => r.json()),
    fetch("/netllm/v1/config").then((r) => r.json()),
    fetch("/netllm/v1/client-env").then((r) => r.json()),
  ]);

  setText("agent-line", `${status.hostname || "local"} · ${status.agent_id || "agent"}`);
  setText("role-stat", status.role || "peer");
  setText("listen-line", status.listen_url || "");
  setText("strategy-line", `Strategy: ${status.routing_strategy || "local_first"}`);

  const backends = status.backends || [];
  const healthy = backends.filter((b) => b.health && b.health.status === "online");
  setText("backend-stat", `${healthy.length} / ${backends.length} online`);
  setText("peer-stat", String((status.peers || []).length));

  const backendsEl = document.getElementById("backends-table");
  clearChildren(backendsEl);
  backendsEl.appendChild(
    makeTable(
      ["Provider", "URL", "Status", "Models"],
      backends,
      (b) => [
        b.provider || "—",
        codeText(b.base_url || ""),
        statusBadge((b.health && b.health.status) || "unknown"),
        String((b.health && b.health.models && b.health.models.length) || 0),
      ]
    )
  );

  const modelsEl = document.getElementById("models-table");
  clearChildren(modelsEl);
  modelsEl.appendChild(
    makeTable(
      ["Model", "Owner"],
      models.data || [],
      (m) => [codeText(m.id || ""), m.owned_by || "—"]
    )
  );

  const badgeEl = document.getElementById("doctor-badge");
  clearChildren(badgeEl);
  const badge = document.createElement("span");
  badge.className = "badge " + (doctor.ok ? "ok" : "warn");
  badge.textContent = doctor.ok ? "All checks passed" : "Issues found";
  badgeEl.appendChild(badge);

  const issuesEl = document.getElementById("doctor-issues");
  clearChildren(issuesEl);
  for (const issue of doctor.issues || []) {
    const div = document.createElement("div");
    div.className = "issue";
    const title = document.createElement("div");
    title.className = "title";
    title.textContent = issue.title;
    const fix = document.createElement("div");
    fix.className = "fix";
    fix.textContent = issue.fix;
    div.appendChild(title);
    div.appendChild(fix);
    issuesEl.appendChild(div);
  }

  document.getElementById("config-block").textContent = JSON.stringify(config, null, 2);

  const vars = env.vars || env;
  const envLines = Object.entries(vars)
    .map(([k, v]) => `export ${k}=${v}`)
    .join("\n");
  document.getElementById("env-block").textContent = envLines;
  window._envText = envLines;
}

document.getElementById("btn-refresh").addEventListener("click", () => {
  loadDashboard().catch((e) => showToast("Refresh failed: " + e.message));
});

document.getElementById("btn-discover").addEventListener("click", async () => {
  const btn = document.getElementById("btn-discover");
  btn.disabled = true;
  try {
    const r = await fetch("/netllm/v1/admin/discover", { method: "POST" });
    if (!r.ok) throw new Error(await r.text());
    showToast("Discovery complete");
    await loadDashboard();
  } catch (e) {
    showToast("Discover failed: " + e.message);
  } finally {
    btn.disabled = false;
  }
});

document.getElementById("btn-env").addEventListener("click", async () => {
  const text = window._envText || "";
  try {
    await navigator.clipboard.writeText(text);
    showToast("Copied client env to clipboard");
  } catch {
    showToast("Copy failed — select env block manually");
  }
});

loadDashboard().catch((e) => {
  setText("agent-line", "Agent unreachable");
  showToast(e.message);
});
