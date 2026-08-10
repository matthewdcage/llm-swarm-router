"""Accessibility regression tests for the web dashboard.

The contrast sweep here is the load-bearing one: it walks every visible text
node on all 11 pages in both themes and asserts each pair clears its WCAG
1.4.3 threshold. It exists because the failures it locks out were
token-level — one edit to `apps/netllm-mac/design-tokens.json` can regress
~20 selectors across 9 pages at once, and nothing else in the suite notices.

Compositing is computed from live `getComputedStyle`, not from the token
file. The backdrop painted behind a `.pill.ok` is a translucent tint over an
`.inset` over a `.panel`, and the foreground of a faded label is its colour
folded through every ancestor `opacity`. An implementation that reads
`color` and the nearest `background-color` reports false passes on exactly
the pairs that fail.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import RunningServer

STATIC = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "netllm-agent"
    / "src"
    / "netllm_agent"
    / "static"
)

PAGES = [
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
]

# WCAG 1.4.3 exempts controls in a disabled state. `#btn-save` is the visible
# case: `button:disabled{opacity:.45}` over the accent fill measures ~1.9:1.
# `sweep()` skips by *state*, not by selector, so a control that stops being
# disabled comes straight back under test.
A11Y_LIB = r"""
window.__a11y = (() => {
  function parse(c) {
    if (!c || c === "transparent") return [0, 0, 0, 0];
    const m = c.match(/rgba?\(([^)]+)\)/);
    if (!m) return [0, 0, 0, 0];
    const p = m[1].split(/[,\s/]+/).filter(Boolean).map(Number);
    return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
  }
  function over(fg, bg) {
    const a = fg[3];
    return [
      fg[0] * a + bg[0] * (1 - a),
      fg[1] * a + bg[1] * (1 - a),
      fg[2] * a + bg[2] * (1 - a),
    ];
  }
  function lum(c) {
    const f = (v) => {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
  }
  function ratio(a, b) {
    const l1 = lum(a), l2 = lum(b);
    return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
  }
  // Composite every translucent layer from `start` upward until an opaque one
  // is found. A `.pill.ok` is a 12%-alpha tint over `.inset` over `.panel`;
  // stopping at the first non-transparent layer overstates the contrast.
  function bgChain(start) {
    const layers = [];
    let n = start;
    while (n && n.nodeType === 1) {
      const c = parse(getComputedStyle(n).backgroundColor);
      if (c[3] > 0) layers.push(c);
      if (c[3] >= 1) break;
      n = n.parentElement;
    }
    let base = [255, 255, 255];
    for (let i = layers.length - 1; i >= 0; i--) base = over(layers[i], base);
    return base;
  }
  // An ancestor `opacity` fades the text and its own backdrop together
  // against whatever sits behind the faded group, so it belongs on both
  // sides of the pair — applying it to the foreground alone understates.
  function topOpacityGroup(el) {
    let g = null, n = el;
    while (n && n.nodeType === 1) {
      if (parseFloat(getComputedStyle(n).opacity) < 1) g = n;
      n = n.parentElement;
    }
    return g;
  }
  function cumOpacity(el, upTo) {
    let a = 1, n = el;
    while (n && n.nodeType === 1) {
      a *= parseFloat(getComputedStyle(n).opacity);
      if (n === upTo) break;
      n = n.parentElement;
    }
    return a;
  }
  function pair(el) {
    const cs = getComputedStyle(el);
    let bg = bgChain(el);
    let fg = over(parse(cs.color), bg);
    const g = topOpacityGroup(el);
    if (g) {
      const backdrop = bgChain(g.parentElement || document.body);
      const A = cumOpacity(el, g);
      bg = over([bg[0], bg[1], bg[2], A], backdrop);
      fg = over([fg[0], fg[1], fg[2], A], backdrop);
    }
    const size = parseFloat(cs.fontSize);
    const weight = parseInt(cs.fontWeight, 10) || 400;
    const large = size >= 24 || (size >= 18.66 && weight >= 700);
    return {
      fg: fg.map((v) => Math.round(v)),
      bg: bg.map((v) => Math.round(v)),
      ratio: Math.round(ratio(fg, bg) * 100) / 100,
      size, weight, large,
      required: large ? 3.0 : 4.5,
    };
  }
  // The backdrop an outline is painted on. `outline-offset: 2px` puts the
  // ring outside the control, so it is the parent's surface that matters —
  // measuring against the control's own fill would score a pressed chip's
  // ring against the accent tint it sits inside.
  function outlineBackdrop(el) {
    return bgChain(el.parentElement || document.body);
  }
  function label(el) {
    const cls = (el.getAttribute("class") || "").trim().split(/\s+/)
      .filter(Boolean).join(".");
    let s = el.tagName.toLowerCase() + (cls ? "." + cls : "");
    if (el.id) s += "#" + el.id;
    const ap = el.getAttribute("aria-pressed");
    if (ap) s += "[aria-pressed=" + ap + "]";
    return s;
  }
  function ownText(el) {
    let t = "";
    for (const n of el.childNodes) if (n.nodeType === 3) t += n.nodeValue;
    return t.trim();
  }
  function visible(el) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return false;
    const cs = getComputedStyle(el);
    return cs.visibility !== "hidden" && cs.display !== "none";
  }
  // A disabled control is exempt, and so is text inside one: the label of a
  // disabled chip is painted through that chip's own opacity.
  function inDisabled(el) {
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
      if (n.disabled === true || n.getAttribute("aria-disabled") === "true") {
        return true;
      }
    }
    return false;
  }
  function sweep(rootSel) {
    const root = document.querySelector(rootSel || "body");
    const out = [];
    root.querySelectorAll("*").forEach((el) => {
      const tag = el.tagName;
      if (tag === "SCRIPT" || tag === "STYLE" || tag === "SVG") return;
      if (!ownText(el)) return;
      if (!visible(el)) return;
      if (inDisabled(el)) return;
      out.push({ sel: label(el), text: ownText(el).slice(0, 40), ...pair(el) });
    });
    return out;
  }
  return { pair, sweep, ratio, label, outlineBackdrop };
})();
"""

# Token pairings that do not all appear organically in a single render —
# status pills in every state on both surfaces, every log level, the accent
# stat, `code` nested in muted text. Without this a palette regression could
# hide behind an empty peer list.
PROBE_HTML = """
<div id="__a11y_probe" style="padding:16px">
  <div class="panel">
    <div class="panel-head"><div class="panel-title">Probe</div>
      <div class="panel-note">panel note text</div></div>
    <p class="panel-desc">panel desc text</p>
    <p class="muted">muted text</p>
    <p class="field-help">field help <code>code sample</code></p>
    <div class="field-label">field label</div>
    <span class="text-ok">ok text</span>
    <span class="text-warn">warn text</span>
    <span class="text-danger">danger text</span>
    <span class="pill ok"><span class="dot"></span>Healthy</span>
    <span class="pill warn"><span class="dot"></span>Degraded</span>
    <span class="pill error"><span class="dot"></span>Failed</span>
    <span class="pill neutral">Neutral</span>
    <button type="button">Primary</button>
    <button type="button" class="secondary">Secondary</button>
    <button type="button" class="ghost">Ghost</button>
    <button type="button" class="danger">Danger</button>
    <button type="button" class="chip" aria-pressed="false">Chip off</button>
    <button type="button" class="chip" aria-pressed="true">Chip on</button>
    <div class="segmented"><button type="button" aria-pressed="true">On</button>
      <button type="button" aria-pressed="false">Off</button></div>
    <div class="inset">
      <p class="muted">muted on inset <code>code on inset</code></p>
      <div class="field-label">inset field label</div>
      <p class="field-help">inset field help</p>
      <span class="pill ok"><span class="dot"></span>Healthy</span>
      <span class="pill warn"><span class="dot"></span>Degraded</span>
      <span class="pill error"><span class="dot"></span>Failed</span>
      <span class="pill neutral">Neutral</span>
      <button type="button" class="danger">Danger</button>
      <button type="button" class="chip" aria-pressed="true">Chip on</button>
    </div>
    <div class="log-stream">
      <div class="log-line"><span class="log-time">12:00:00</span>
        <span class="log-level error">error</span><span>msg</span></div>
      <div class="log-line"><span class="log-time">12:00:00</span>
        <span class="log-level warn">warn</span><span>msg</span></div>
      <div class="log-line"><span class="log-time">12:00:00</span>
        <span class="log-level info">info</span><span>msg</span></div>
      <div class="log-line"><span class="log-time">12:00:00</span>
        <span class="log-level debug">debug</span><span>msg</span></div>
    </div>
    <div class="stat-row"><div><div class="stat-label">Stat label</div>
      <div class="stat-value">42<span class="unit">tok/s</span></div></div>
      <div><div class="stat-value accent">99</div></div></div>
    <div class="table">
      <div class="table-head" style="grid-template-columns:1fr 1fr">
        <div>Header A</div><div>Header B</div></div>
      <div class="table-row" style="grid-template-columns:1fr 1fr">
        <div>cell</div><div>cell</div></div>
      <div class="table-foot">table foot</div>
      <div class="empty">empty state</div></div>
    <div class="rail-layout"><div class="rail" style="display:block">
      <div class="rail-items">
        <button type="button" class="rail-item">Rail item
          <span class="muted">on PATH</span></button>
        <button type="button" class="rail-item active">Rail active
          <span class="muted">on PATH</span></button>
      </div></div></div>
    <div class="mesh-node"><div class="mesh-node-name">node</div>
      <div class="mesh-node-sub">sub</div>
      <div class="mesh-self-role">gateway</div></div>
    <div class="endpoint-card"><div class="endpoint-label">Client endpoint</div>
      <div class="endpoint-url">http://x</div></div>
    <div class="finding"><div class="finding-body">
      <div class="finding-title">Finding</div>
      <div class="finding-detail">finding detail</div></div></div>
    <div class="nav-sublabel">nav sublabel</div>
    <div class="nav-group-label">Nav group</div>
    <div class="legend"><span><span class="swatch"></span>legend item</span></div>
  </div>
</div>
"""

# Every banner kind, measured where it is actually painted.
BANNER_SWEEP = """
(() => {
  const out = [];
  const b = document.getElementById("global-banner");
  b.hidden = false;
  for (const kind of ["", "ok", "warn", "error"]) {
    if (kind) b.dataset.kind = kind; else delete b.dataset.kind;
    b.textContent = "Banner message text";
    out.push({
      sel: "#global-banner[data-kind=" + (kind || "none") + "]",
      text: "banner", ...window.__a11y.pair(b),
    });
  }
  b.hidden = true;
  b.textContent = "";
  delete b.dataset.kind;
  return out;
})()
"""

FOCUS_PROBE = """
() => {
  const a = document.activeElement;
  if (!a || a === document.body) return null;
  // A visually-hidden checkbox (.switch input is 0x0) paints its ring on the
  // sibling track, which is the thing the user actually sees.
  const target = (a.tagName === "INPUT" && a.offsetWidth === 0)
    ? (a.nextElementSibling || a) : a;
  const cs = getComputedStyle(target);
  return {
    sel: window.__a11y.label(a),
    width: cs.outlineWidth,
    style: cs.outlineStyle,
    colour: cs.outlineColor,
    bg: window.__a11y.outlineBackdrop(target),
  };
}
"""


def _seconds(value: str) -> float:
    value = value.strip()
    if value.endswith("ms"):
        return float(value[:-2]) / 1000
    if value.endswith("s"):
        return float(value[:-1])
    return float(value)


def _rgb(css_colour: str) -> list[float]:
    parts = css_colour.replace("rgba(", "").replace("rgb(", "").rstrip(")")
    return [float(v) for v in parts.replace("/", ",").split(",")[:3]]


def _describe(row: dict) -> str:
    return (
        f"{row['ratio']}:1 (needs {row['required']}) "
        f"{row['theme']}/{row['page']} {row['sel']} "
        f"fg=rgb{tuple(row['fg'])} on bg=rgb{tuple(row['bg'])} "
        f"{row['size']}px/{row['weight']} :: {row['text']!r}"
    )


@pytest.fixture
def a11y_page(page, agent: RunningServer):  # noqa: ANN001, ANN201
    """Dashboard page with the contrast helpers injected."""
    page.goto(f"{agent.base_url}/ui/", wait_until="networkidle")
    page.evaluate(A11Y_LIB)
    return page


def _sweep_everything(page, theme: str) -> list[dict]:  # noqa: ANN001
    page.evaluate(f"applyTheme('{theme}')")
    rows: list[dict] = []

    page.evaluate(
        "html => document.getElementById('page-overview')"
        ".insertAdjacentHTML('beforeend', html)",
        PROBE_HTML,
    )
    rows += [
        {**r, "page": "probe", "theme": theme}
        for r in page.evaluate("window.__a11y.sweep('#__a11y_probe')")
    ]
    rows += [
        {**r, "page": "chrome", "theme": theme} for r in page.evaluate(BANNER_SWEEP)
    ]
    page.evaluate("document.getElementById('__a11y_probe').remove()")

    for page_key in PAGES:
        page.evaluate(f"navigate('{page_key}')")
        page.wait_for_timeout(350)
        rows += [
            {**r, "page": page_key, "theme": theme}
            for r in page.evaluate("window.__a11y.sweep('body')")
        ]
    return rows


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_every_text_pair_meets_wcag_contrast(a11y_page, theme: str) -> None:  # noqa: ANN001
    """The palette regression guard.

    Every visible text node on all 11 pages, plus a probe block forcing the
    states a single render may not produce, must clear 4.5:1 (3:1 for large
    text). Disabled controls are skipped by `sweep()` as WCAG 1.4.3 exempt.
    """
    rows = _sweep_everything(a11y_page, theme)
    assert len(rows) > 500, f"the sweep collected only {len(rows)} pairs"

    failures = [r for r in rows if r["ratio"] < r["required"]]
    # Dedupe so one regressed token does not print hundreds of near-identical
    # lines; the reported count is of distinct colour pairs.
    distinct: dict[tuple, dict] = {}
    for row in failures:
        key = (row["sel"], tuple(row["fg"]), tuple(row["bg"]))
        if key not in distinct or row["ratio"] < distinct[key]["ratio"]:
            distinct[key] = row

    assert not distinct, (
        f"{len(distinct)} distinct contrast failures in the {theme} theme "
        f"({len(failures)} occurrences):\n  "
        + "\n  ".join(
            _describe(r) for r in sorted(distinct.values(), key=lambda r: r["ratio"])
        )
    )


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_status_tokens_clear_threshold_on_their_own_tint(
    a11y_page,  # noqa: ANN001
    theme: str,
) -> None:
    """`.pill.ok/.warn/.error` paint a status colour on a tint of that same
    colour — the pairing that put every status badge on every page under the
    threshold. Pinned separately so the root cause is named when it breaks."""
    a11y_page.evaluate(f"applyTheme('{theme}')")
    a11y_page.evaluate(
        "html => document.getElementById('page-overview')"
        ".insertAdjacentHTML('beforeend', html)",
        PROBE_HTML,
    )
    rows = a11y_page.evaluate("window.__a11y.sweep('#__a11y_probe')")
    a11y_page.evaluate("document.getElementById('__a11y_probe').remove()")

    pills = [r for r in rows if r["sel"].startswith("span.pill")]
    assert len(pills) >= 8, f"{theme}: the probe rendered only {len(pills)} pills"
    for row in pills:
        assert row["ratio"] >= row["required"], f"{theme}: {row}"


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_toast_meets_contrast_once_shown(a11y_page, theme: str) -> None:  # noqa: ANN001
    """The toast fades in, so it has to be measured after the transition —
    sampling mid-fade reads fg == bg and reports for the wrong reason."""
    a11y_page.evaluate(f"applyTheme('{theme}')")
    a11y_page.evaluate("showToast('Toast message text')")
    a11y_page.wait_for_function(
        "() => getComputedStyle(document.getElementById('toast')).opacity === '1'"
    )
    result = a11y_page.evaluate("window.__a11y.pair(document.getElementById('toast'))")
    assert result["ratio"] >= result["required"], result


def test_accent_has_a_separate_text_token(a11y_page) -> None:  # noqa: ANN001
    """`--accent` is tuned as a surface colour; used as a foreground it
    measured 2.70–4.02:1. `--accent-text` is the token that carries the
    foreground role, and the two must not collapse back into one value."""
    for theme in ("light", "dark"):
        a11y_page.evaluate(f"applyTheme('{theme}')")
        accent, accent_text, accent_solid = a11y_page.evaluate(
            "() => { const s = getComputedStyle(document.documentElement);"
            " return ['--accent', '--accent-text', '--accent-solid']"
            ".map((n) => s.getPropertyValue(n).trim()); }"
        )
        assert accent_text, f"{theme}: --accent-text is not defined"
        assert accent_solid, f"{theme}: --accent-solid is not defined"
        assert accent_text != accent, f"{theme}: --accent-text duplicates --accent"


def test_accent_is_not_painted_as_text_anywhere(a11y_page) -> None:  # noqa: ANN001
    """The three call sites that reused the surface accent as a foreground."""
    a11y_page.evaluate("navigate('logs')")
    a11y_page.wait_for_timeout(300)
    resolved_accent = a11y_page.evaluate(
        "() => { const p = document.createElement('span');"
        " p.style.color = 'var(--accent)'; document.body.appendChild(p);"
        " const c = getComputedStyle(p).color; p.remove(); return c; }"
    )
    offenders = a11y_page.evaluate(
        "(accent) => [...document.querySelectorAll('.log-level.info,"
        ' .stat-value.accent, .chip[aria-pressed="true"]\')]'
        ".filter((el) => getComputedStyle(el).color === accent)"
        ".map((el) => window.__a11y.label(el))",
        resolved_accent,
    )
    assert offenders == [], offenders


# --------------------------------------------------------------------------
# Keyboard entry point and focus visibility
# --------------------------------------------------------------------------


def test_skip_link_is_first_stop_and_moves_focus_to_main(a11y_page) -> None:  # noqa: ANN001
    """The skip link must be the first tab stop, must paint when focused, and
    must land focus inside `<main>`."""
    a11y_page.evaluate("() => document.body.focus()")
    a11y_page.keyboard.press("Tab")
    assert a11y_page.evaluate(
        "() => [document.activeElement.className,"
        " document.activeElement.getAttribute('href')]"
    ) == ["skip-link", "#page-main"]

    # `left:-9999px` unfocused, `left:0` focused — a skip link that never
    # paints is present but not operable.
    box = a11y_page.locator(".skip-link").bounding_box()
    assert box is not None and box["x"] >= 0, box

    a11y_page.keyboard.press("Enter")
    a11y_page.wait_for_timeout(200)
    assert a11y_page.evaluate("() => document.activeElement.id") == "page-main"


@pytest.mark.xfail(
    reason=(
        "K1: href='#page-main' fires hashchange, and wireChrome's handler "
        "calls navigate('page-main'), which is not in PAGES and falls back to "
        "overview — so the first keyboard action on any page throws away the "
        "page the user was on. The fix is in dashboard.js (ignore a hash that "
        "is not a known page key); this test flips to xpass when it lands."
    ),
    strict=False,
)
def test_skip_link_does_not_change_the_current_page(a11y_page) -> None:  # noqa: ANN001
    """Activating the skip link from Logs must leave the user on Logs."""
    a11y_page.evaluate("navigate('logs')")
    a11y_page.wait_for_timeout(250)
    assert a11y_page.evaluate("() => state.page") == "logs"

    a11y_page.evaluate("() => document.querySelector('.skip-link').focus()")
    a11y_page.keyboard.press("Enter")
    a11y_page.wait_for_timeout(300)

    assert a11y_page.evaluate("() => state.page") == "logs"
    expect(a11y_page.locator("#page-logs")).to_have_class("page active")


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_focused_controls_paint_a_visible_ring(a11y_page, theme: str) -> None:  # noqa: ANN001
    """Every keyboard-focused control needs a ring, and the ring needs 3:1
    against the surface it is drawn on (WCAG 1.4.11)."""
    a11y_page.evaluate(f"applyTheme('{theme}')")
    a11y_page.evaluate("() => document.body.focus()")

    checked = 0
    for _ in range(16):
        a11y_page.keyboard.press("Tab")
        info = a11y_page.evaluate(FOCUS_PROBE)
        if info is None:
            continue
        assert info["style"] not in ("none", ""), f"{theme}: no ring on {info}"
        assert float(info["width"].removesuffix("px")) >= 1, f"{theme}: {info}"
        ratio = a11y_page.evaluate(
            "([a, b]) => window.__a11y.ratio(a, b)",
            [_rgb(info["colour"]), info["bg"]],
        )
        assert ratio >= 3.0, f"{theme}: focus ring {round(ratio, 2)}:1 — {info}"
        checked += 1

    assert checked >= 10, f"{theme}: only {checked} focus stops were reached"


def test_topbar_actions_stay_reachable_at_mobile_width(a11y_page) -> None:  # noqa: ANN001
    """`.topbar-actions` was a rigid 509px block inside an `overflow:hidden`
    shell, so below that width Refresh and Save were painted off-screen with
    no scroll path to them — a pointer-only loss of function."""
    a11y_page.set_viewport_size({"width": 320, "height": 720})
    a11y_page.wait_for_timeout(250)
    for control in ("#btn-discover", "#btn-drain", "#btn-refresh", "#btn-save"):
        box = a11y_page.locator(control).bounding_box()
        assert box is not None, f"{control} has no box"
        assert box["x"] >= 0, f"{control} starts off-screen: {box}"
        assert box["x"] + box["width"] <= 321, f"{control} is clipped: {box}"
    assert a11y_page.evaluate(
        "() => document.documentElement.scrollWidth"
        " <= document.documentElement.clientWidth"
    ), "the document scrolls horizontally at 320px"


def test_global_banner_is_a_live_region(a11y_page) -> None:  # noqa: ANN001
    """`setBanner()` writes "Could not reach the agent" here; without a live
    region a screen-reader user is never told the agent went away."""
    banner = a11y_page.locator("#global-banner")
    assert banner.get_attribute("aria-live") in ("polite", "assertive")
    assert banner.get_attribute("role") in ("status", "alert")


# --------------------------------------------------------------------------
# Reduced motion
# --------------------------------------------------------------------------


@pytest.fixture
def reduced_motion_page(browser, agent: RunningServer):  # noqa: ANN001, ANN201
    context = browser.new_context(reduced_motion="reduce")
    page = context.new_page()
    page.goto(f"{agent.base_url}/ui/", wait_until="networkidle")
    page.evaluate(A11Y_LIB)
    try:
        yield page
    finally:
        context.close()


def test_reduced_motion_stops_animations(reduced_motion_page) -> None:  # noqa: ANN001
    """The pulsing status dot and the toast fade must both stop."""
    expect(reduced_motion_page.locator("#status-badge .dot")).to_be_visible()
    duration, iterations = reduced_motion_page.evaluate(
        "() => { const cs = getComputedStyle("
        "document.querySelector('#status-badge .dot'));"
        " return [cs.animationDuration, cs.animationIterationCount]; }"
    )
    assert _seconds(duration) <= 0.001, duration
    assert iterations == "1", iterations

    transition = reduced_motion_page.evaluate(
        "() => getComputedStyle(document.getElementById('toast')).transitionDuration"
    )
    assert all(_seconds(v) <= 0.001 for v in transition.split(",")), transition


def test_reduced_motion_guard_is_not_always_on(dash) -> None:  # noqa: ANN001
    """The test above only means something if the animation runs otherwise."""
    duration = dash.evaluate(
        "() => getComputedStyle(document.querySelector('#status-badge .dot'))"
        ".animationDuration"
    )
    assert _seconds(duration) > 0.1, duration


def test_no_dead_keyframes_in_the_stylesheet() -> None:
    """`@keyframes flow` was referenced by nothing — dead CSS that reads like
    a motion source a reviewer then has to rule out by hand. Any keyframes
    block that survives must be driven by some `animation` declaration."""
    sources = "".join(
        path.read_text(encoding="utf-8")
        for path in [
            *sorted(STATIC.glob("*.css")),
            *sorted(STATIC.glob("*.js")),
            *sorted((STATIC / "pages").glob("*.js")),
        ]
    )
    declared = set(re.findall(r"@keyframes\s+([A-Za-z0-9_-]+)", sources))
    assert declared, "no @keyframes found — did the stylesheet move?"
    for name in declared:
        used = re.search(rf"animation[^;{{}}]*:[^;{{}}]*\b{re.escape(name)}\b", sources)
        assert used, f"@keyframes {name} is declared but never referenced"
