/** Overnight planner — Gantt visual + complete-or-timeout model. */
(function () {
  const HUB_PREFIX = (location.pathname.match(/^(\/hub-form)(?=\/|$)/) || [])[1] || '';

  const STEP_CATALOG = [
    { type: 'machine_wake', label: 'Wake machine', icon: '⏰', color: 'hsl(210 55% 42%)', defaults: { target: 'tnas', timeout_min: 5 } },
    { type: 'machine_sleep', label: 'Sleep machine', icon: '🌙', color: 'hsl(240 15% 40%)', defaults: { target: 'ubuntu', timeout_min: 3 } },
    { type: 'mount_check', label: 'Mount check', icon: '💾', color: 'hsl(200 40% 40%)', defaults: { timeout_min: 3 } },
    { type: 'llm_start', label: 'Start vLLM', icon: '⚡', color: 'hsl(145 45% 40%)', defaults: { timeout_min: 8 } },
    { type: 'llm_stop', label: 'Stop vLLM', icon: '⏹', color: 'hsl(145 35% 35%)', defaults: { timeout_min: 3 } },
    { type: 'dsh_research', label: 'DeepSeek research', icon: '🔬', color: 'hsl(270 45% 45%)', defaults: { timeout_min: 45 } },
    { type: 'dsh_log_review', label: 'DeepSeek log review', icon: '📋', color: 'hsl(320 40% 42%)', defaults: { timeout_min: 15 } },
    { type: 'sync_job', label: 'Seedvault sync', icon: '🔄', color: 'hsl(35 50% 42%)', defaults: { timeout_min: 30 } },
    { type: 'queue_drain', label: 'Queue drain', icon: '📥', color: 'hsl(240 25% 40%)', defaults: { timeout_min: 20 } },
    { type: 'storage_scan', label: 'Storage scan', icon: '🗂', color: 'hsl(240 25% 38%)', defaults: { timeout_min: 20 } },
  ];

  const TYPE_CLASS = {
    machine_wake: 'type-machine_wake',
    machine_sleep: 'type-machine_sleep',
    mount_check: 'type-mount_check',
    llm_start: 'type-llm_start',
    llm_stop: 'type-llm_stop',
    dsh_research: 'type-dsh_research',
    research_synthesis: 'type-dsh_research',
    dsh_log_review: 'type-dsh_log_review',
    log_review: 'type-dsh_log_review',
    sync_job: 'type-sync_job',
    queue_drain: 'type-queue_drain',
    storage_scan: 'type-storage_scan',
  };

  let schedule = {
    timezone: 'Australia/Brisbane',
    window: { start: '22:00', end: '06:00' },
    advance_mode: 'complete_or_timeout',
    steps: [],
  };
  let sortable = null;
  let statusPoll = null;

  function api(path, opts = {}) {
    return fetch(`${HUB_PREFIX}/api${path}`, {
      headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
      ...opts,
    }).then(async (r) => {
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.detail || r.statusText);
      return j;
    });
  }

  function uid() {
    return Math.random().toString(36).slice(2, 10);
  }

  function stepTimeout(step) {
    return Number(step.timeout_min ?? step.duration_min) || 5;
  }

  function parseHm(hm) {
    const [h, m] = hm.split(':').map(Number);
    return h * 60 + m;
  }

  function fmtHm(mins) {
    let m = mins % (24 * 60);
    if (m < 0) m += 24 * 60;
    const h = Math.floor(m / 60);
    const mm = m % 60;
    return `${String(h).padStart(2, '0')}:${String(mm).padStart(2, '0')}`;
  }

  function windowMins() {
    const start = parseHm(schedule.window?.start || '22:00');
    const end = parseHm(schedule.window?.end || '06:00');
    let w = end - start;
    if (w <= 0) w += 24 * 60;
    return { start, end, w };
  }

  /** Sequential time budget layout (each step gets max slot; actual run may finish early). */
  function computeLayout() {
    const { start, w } = windowMins();
    let cursor = start;
    const rows = [];
    let budgetTotal = 0;
    for (const step of schedule.steps || []) {
      const dur = stepTimeout(step);
      budgetTotal += dur;
      const t0 = cursor;
      cursor += dur;
      const leftPct = ((t0 - start) / w) * 100;
      const widthPct = (dur / w) * 100;
      rows.push({
        step,
        start: fmtHm(t0),
        end: fmtHm(cursor),
        leftPct: Math.max(0, Math.min(100, leftPct)),
        widthPct: Math.max(0.8, Math.min(100 - leftPct, widthPct)),
        dur,
      });
    }
    return { rows, budgetTotal, windowMins: w, windowStart: start };
  }

  function renderGanttHours() {
    const { start, w } = windowMins();
    const el = document.getElementById('ganttHours');
    const ticks = [];
    for (let i = 0; i <= 8; i++) {
      ticks.push(`<span>${fmtHm(start + (w * i) / 8)}</span>`);
    }
    el.innerHTML = ticks.join('');
    document.getElementById('windowLabel').textContent =
      `${schedule.window?.start || '22:00'} → ${schedule.window?.end || '06:00'} Brisbane`;
  }

  function renderGanttBars() {
    const { rows } = computeLayout();
    const lanes = document.getElementById('ganttLanes');
    const laneH = rows.length * 2.85 + 0.5;
    lanes.style.minHeight = `${laneH}rem`;
    lanes.innerHTML = rows
      .map((r, i) => {
        const cls = TYPE_CLASS[r.step.type] || 'type-queue_drain';
        const off = r.step.enabled ? '' : ' disabled';
        const label = r.step.label || r.step.type;
        return `<div class="gantt-bar ${cls}${off}" data-id="${r.step.id}" style="left:${r.leftPct}%;width:${r.widthPct}%;top:${i * 2.85}rem" title="${label}: ≤${r.dur}m (${r.start}–${r.end} budget)">
          ${label}<span class="cap">≤${r.dur}m</span>
        </div>`;
      })
      .join('');
  }

  function validate() {
    const { budgetTotal, windowMins: wm } = computeLayout();
    const banner = document.getElementById('warnBanner');
    const msgs = [];
    if (budgetTotal > wm) {
      msgs.push(
        `Time budgets total ${budgetTotal}m exceed the ${wm}m night window — some steps may not run unless they finish early.`
      );
    }
    const types = (schedule.steps || []).filter((s) => s.enabled).map((s) => s.type);
    const ls = types.indexOf('llm_start');
    const le = types.lastIndexOf('llm_stop');
    const lr = types.findIndex((t) => t === 'dsh_research' || t === 'research_synthesis');
    if (lr >= 0 && ls >= 0 && ls > lr) msgs.push('Start vLLM should come before DeepSeek research.');
    if (le >= 0 && lr >= 0 && le < lr) msgs.push('Stop vLLM should come after research / log review.');
    if (msgs.length) {
      banner.textContent = msgs.join(' ');
      banner.classList.add('show');
    } else {
      banner.classList.remove('show');
    }
  }

  function catalogMeta(type) {
    return STEP_CATALOG.find((c) => c.type === type) || { icon: '•', color: 'var(--accent)', label: type };
  }

  function renderPalette() {
    const el = document.getElementById('palette');
    el.innerHTML = STEP_CATALOG.map(
      (c) =>
        `<div class="palette-item" data-type="${c.type}"><span class="dot" style="background:${c.color}"></span>${c.icon} ${c.label}</div>`
    ).join('');
    el.querySelectorAll('.palette-item').forEach((node) => {
      node.addEventListener('click', () => addStep(node.dataset.type));
    });
  }

  function stepCardHtml(step) {
    const meta = catalogMeta(step.type);
    const label = step.label || meta.label;
    const to = stepTimeout(step);
    const border = meta.color || 'var(--accent)';
    return `<div class="step-card${step.enabled ? '' : ' disabled'}" data-id="${step.id}" style="border-left-color:${border}">
      <div class="top">
        <strong>${meta.icon} ${label}</strong>
        <span class="badge">≤ ${to} min</span>
      </div>
      <div class="meta">
        <label><input type="checkbox" class="step-enabled" ${step.enabled ? 'checked' : ''} /> enabled</label>
        <span>max <input type="number" min="1" max="240" class="step-to" value="${to}" /> min</span>
        <span>${step.type}${step.target ? ' · ' + step.target : ''}</span>
        <button type="button" class="btn step-remove" style="padding:0.1rem 0.4rem;font-size:0.72rem;margin-left:auto">Remove</button>
      </div>
    </div>`;
  }

  function bindStepEvents() {
    document.querySelectorAll('.step-card').forEach((card) => {
      const id = card.dataset.id;
      card.querySelector('.step-enabled')?.addEventListener('change', (e) => {
        const s = schedule.steps.find((x) => x.id === id);
        if (s) s.enabled = e.target.checked;
        renderVisuals();
      });
      card.querySelector('.step-to')?.addEventListener('change', (e) => {
        const s = schedule.steps.find((x) => x.id === id);
        if (s) {
          s.timeout_min = Number(e.target.value) || 5;
          delete s.duration_min;
        }
        renderVisuals();
      });
      card.querySelector('.step-remove')?.addEventListener('click', () => {
        schedule.steps = schedule.steps.filter((x) => x.id !== id);
        renderVisuals();
      });
    });
  }

  function renderSteps() {
    const el = document.getElementById('stepList');
    el.innerHTML = (schedule.steps || []).map(stepCardHtml).join('') ||
      '<p class="hint">Add steps from the left — drag to set run order.</p>';
    bindStepEvents();
    if (sortable) sortable.destroy();
    if (schedule.steps?.length) {
      sortable = Sortable.create(el, {
        animation: 160,
        ghostClass: 'sortable-ghost',
        onEnd(evt) {
          const moved = schedule.steps.splice(evt.oldIndex, 1)[0];
          schedule.steps.splice(evt.newIndex, 0, moved);
          renderVisuals();
        },
      });
    }
  }

  function renderVisuals() {
    renderGanttHours();
    renderGanttBars();
    renderSteps();
    validate();
  }

  function addStep(type) {
    const cat = STEP_CATALOG.find((c) => c.type === type);
    if (!cat) return;
    schedule.steps.push({
      id: `${type}-${uid()}`,
      type,
      label: cat.label,
      enabled: true,
      critical: false,
      timeout_min: cat.defaults.timeout_min,
      ...cat.defaults,
    });
    renderVisuals();
  }

  async function pollRunStatus() {
    try {
      const st = await api('/overnight/status');
      const el = document.getElementById('runStatus');
      if (!st.status || st.status === 'idle') {
        el.classList.remove('show');
        if (statusPoll) {
          clearInterval(statusPoll);
          statusPoll = null;
        }
        return;
      }
      el.classList.add('show');
      el.classList.remove('running', 'done');
      if (st.status === 'running') {
        el.classList.add('running');
        el.textContent = `Running: ${st.current_label || st.current_step || '…'} (${st.steps_done}/${st.steps_total})`;
      } else if (st.status === 'done') {
        el.classList.add('done');
        el.textContent = `Last run finished — ${st.steps_done}/${st.steps_total} steps`;
        clearInterval(statusPoll);
        statusPoll = null;
      } else if (st.status === 'failed') {
        el.textContent = `Run failed: ${st.error || 'unknown'}`;
        clearInterval(statusPoll);
        statusPoll = null;
      }
    } catch (_) { /* ignore */ }
  }

  async function load() {
    const data = await api('/overnight/schedule');
    schedule = data.schedule || schedule;
    schedule.advance_mode = schedule.advance_mode || 'complete_or_timeout';
    renderVisuals();
    pollRunStatus();
    if (!statusPoll) statusPoll = setInterval(pollRunStatus, 4000);
  }

  async function save() {
    const btn = document.getElementById('btnSave');
    btn.disabled = true;
    try {
      await api('/overnight/schedule', { method: 'PUT', body: JSON.stringify({ schedule }) });
      btn.textContent = 'Saved';
      setTimeout(() => { btn.textContent = 'Save'; }, 1500);
    } finally {
      btn.disabled = false;
    }
  }

  async function runNow() {
    const btn = document.getElementById('btnRun');
    btn.disabled = true;
    try {
      await api('/overnight/run', { method: 'POST', body: JSON.stringify({ dry_run: false }) });
      pollRunStatus();
      if (!statusPoll) statusPoll = setInterval(pollRunStatus, 3000);
    } catch (e) {
      alert(e.message || String(e));
    } finally {
      btn.disabled = false;
    }
  }

  function init() {
    renderPalette();
    document.getElementById('btnSave')?.addEventListener('click', save);
    document.getElementById('btnRun')?.addEventListener('click', runNow);
    load().catch((e) => {
      document.getElementById('warnBanner').textContent = 'Load failed: ' + e.message;
      document.getElementById('warnBanner').classList.add('show');
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
