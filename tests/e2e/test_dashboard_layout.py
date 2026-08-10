"""Layout correctness for the redesigned dashboard.

These are geometry assertions, not screenshot diffs: every check reads real
bounding boxes out of a real browser after the real agent has rendered the
page. They exist because the defects they pin were all invisible to the
existing e2e suite — the pages rendered, the text was right, and the boxes
were in the wrong place.

What is pinned here:

* nothing paints outside the box that contains it, on every page, at a wide
  and a narrow viewport (the Integrations `Copy` column used to paint ~43px
  outside `.table`);
* stacked boxes are separated by the container's rhythm — not merely
  non-touching, but exactly the expected gap, because the old
  `.panel + .panel` rule silently dropped to zero whenever a non-panel node
  sat between two panels and leaked a stray margin onto grid items;
* the mesh radar never draws two node cards on top of each other, at any node
  count, and never paints one outside its stage;
* the local backend cards share a top edge within a row at every count and
  width.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

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

WIDTHS = [1440, 1024]

# Containers own the gap between the boxes they stack. Keep in step with the
# `--stack-gap` / `--panel-rhythm` custom properties in dashboard.css.
STACK_GAP = 14
PANEL_RHYTHM = 10
RHYTHM_BY_CONTAINER = {
    ".page.active": STACK_GAP,
    ".stack": STACK_GAP,
    ".rail-body": STACK_GAP,
    ".rail-section": STACK_GAP,
    ".rail": STACK_GAP,
    ".panel": PANEL_RHYTHM,
    ".panel-body": PANEL_RHYTHM,
}


# The id that broke Integrations. Model ids come off the wire from LAN peers,
# so "no id is ever this long" is not an assumption the layout may make: the
# Model ID cell renders a <select>, and a <select> is as wide as its widest
# <option> whatever the grid track asks for.
LONG_MODEL_ID = (
    "a-very-long-model-identifier-that-should-wrap-somewhere-sensible:70b-instruct-q8"
)


def _seed_models(page, extra: list[str]) -> None:
    """Add model ids to /v1/models on the wire, then reload."""

    def handler(route) -> None:  # noqa: ANN001 - playwright type
        response = route.fetch()
        data = response.json()
        rows = list(data.get("data") or [])
        rows.extend({"id": m, "object": "model", "owned_by": "seed"} for m in extra)
        data["data"] = rows
        route.fulfill(
            status=200, content_type="application/json", body=json.dumps(data)
        )

    page.route("**/v1/models*", handler)
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(300)


def _show(page, key: str) -> None:
    page.click(f'.nav-item[data-page="{key}"]')
    page.wait_for_selector(f"#page-{key}.page.active")
    # The page renderers are synchronous, but ResizeObserver-driven layout
    # (the mesh radar) settles one frame later.
    page.wait_for_timeout(250)


# --------------------------------------------------------------------------
# containment
# --------------------------------------------------------------------------

_CONTAINMENT_JS = """
() => {
  const out = [];
  const sel = (e) => e.tagName.toLowerCase() +
    (e.className && typeof e.className === 'string'
      ? '.' + e.className.trim().split(/\\s+/).join('.') : '');
  const active = document.querySelector('.page.active');
  if (!active) return out;
  active.querySelectorAll('.panel, .inset, .table, .log-stream').forEach((box) => {
    const br = box.getBoundingClientRect();
    // A box that has to scroll to show its own content is already too small
    // for it, whether or not a child happens to paint outside.
    if (box.scrollWidth > box.clientWidth + 1) {
      out.push({box: sel(box), kind: 'self-scroll',
                scrollWidth: box.scrollWidth, clientWidth: box.clientWidth});
    }
    box.querySelectorAll('*').forEach((child) => {
      const cr = child.getBoundingClientRect();
      if (cr.width === 0 || cr.height === 0) return;
      if (cr.right > br.right + 1 || cr.left < br.left - 1) {
        out.push({box: sel(box), child: sel(child), kind: 'outside',
                  childLeft: Math.round(cr.left), childRight: Math.round(cr.right),
                  boxLeft: Math.round(br.left), boxRight: Math.round(br.right),
                  text: (child.textContent || '').trim().slice(0, 40)});
      }
    });
  });
  return out;
}
"""


@pytest.mark.parametrize("width", WIDTHS)
def test_nothing_paints_outside_its_container(dash, width: int) -> None:
    """No element may render outside the `.panel` / `.table` holding it.

    Before the column-sizing fix this failed on Integrations: the value column
    was a plain `1fr` track, whose automatic minimum is the min-content width
    of a <select> sized by a 70-character model id, so the grid grew past
    `.table` and the `Copy` button landed outside the panel.
    """
    dash.set_viewport_size({"width": width, "height": 1000})
    _seed_models(dash, [LONG_MODEL_ID])
    violations: dict[str, Any] = {}
    for key in PAGES:
        _show(dash, key)
        found = dash.evaluate(_CONTAINMENT_JS)
        if found:
            violations[key] = found
    assert not violations, json.dumps(violations, indent=2)[:4000]


# --------------------------------------------------------------------------
# vertical rhythm
# --------------------------------------------------------------------------

_RHYTHM_JS = """
(rhythm) => {
  const out = [];
  const sel = (e) => e.tagName.toLowerCase() +
    (e.className && typeof e.className === 'string'
      ? '.' + e.className.trim().split(/\\s+/).join('.') : '');
  const active = document.querySelector('.page.active');
  if (!active) return out;
  Object.entries(rhythm).forEach(([containerSel, expected]) => {
    const containers = containerSel === '.page.active'
      ? [active]
      : [...active.querySelectorAll(containerSel)];
    containers.forEach((container) => {
      const kids = [...container.children].filter((k) => {
        const r = k.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
      });
      for (let i = 1; i < kids.length; i += 1) {
        const a = kids[i - 1].getBoundingClientRect();
        const b = kids[i].getBoundingClientRect();
        // Only siblings that actually stack: two boxes side by side in a
        // horizontal flow are not separated vertically at all.
        const overlapX = Math.min(a.right, b.right) - Math.max(a.left, b.left);
        if (overlapX <= 1 || b.top < a.top) continue;
        const gap = b.top - a.bottom;
        if (Math.abs(gap - expected) > 1.5) {
          out.push({container: containerSel, a: sel(kids[i - 1]), b: sel(kids[i]),
                    gap: Math.round(gap * 10) / 10, expected});
        }
      }
    });
  });
  return out;
}
"""


@pytest.mark.parametrize("width", WIDTHS)
def test_stacked_boxes_keep_the_container_rhythm(dash, width: int) -> None:
    """Spacing between stacked boxes is the container's, and always the same.

    This pins the rhythm rather than merely the absence of touching: a gap of
    0 (the Cloud hero panel against the rail layout below it) and a gap of 28
    (a container gap doubled with a leftover margin) both fail.
    """
    dash.set_viewport_size({"width": width, "height": 1000})
    violations: dict[str, Any] = {}
    for key in PAGES:
        _show(dash, key)
        found = dash.evaluate(_RHYTHM_JS, RHYTHM_BY_CONTAINER)
        if found:
            violations[key] = found
    assert not violations, json.dumps(violations, indent=2)[:4000]


# --------------------------------------------------------------------------
# rows of boxes share a top edge
# --------------------------------------------------------------------------

_ROW_ALIGN_JS = """
() => {
  const out = [];
  const sel = (e) => e.tagName.toLowerCase() +
    (e.className && typeof e.className === 'string'
      ? '.' + e.className.trim().split(/\\s+/).join('.') : '');
  const BOX = '.panel, .inset, .card, .table';
  const active = document.querySelector('.page.active');
  if (!active) return out;
  active.querySelectorAll('.grid-2, .grid-cards, .row-boxes').forEach((row) => {
    const kids = [...row.children].filter((k) => {
      const r = k.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    });
    const rects = kids.map((k) => k.getBoundingClientRect());
    for (let i = 0; i < kids.length; i += 1) {
      for (let j = i + 1; j < kids.length; j += 1) {
        const a = rects[i], b = rects[j];
        // Same row = vertical extents overlap, horizontal extents do not.
        // Comparing every child's top against every other reported an honest
        // 2x2 grid as a misaligned row.
        const vOverlap = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
        const hOverlap = Math.min(a.right, b.right) - Math.max(a.left, b.left);
        if (vOverlap <= Math.min(a.height, b.height) * 0.5) continue;
        if (hOverlap > 1) continue;
        if (Math.abs(a.top - b.top) <= 1.5) continue;
        if (!kids[i].matches(BOX) || !kids[j].matches(BOX)) continue;
        out.push({row: sel(row), a: sel(kids[i]), b: sel(kids[j]),
                  aTop: Math.round(a.top), bTop: Math.round(b.top)});
      }
    }
  });
  return out;
}
"""


@pytest.mark.parametrize("width", WIDTHS)
def test_boxes_in_a_row_share_a_top_edge(dash, width: int) -> None:
    """Two cards in one grid row cannot start at different heights.

    They used to: `.panel + .panel { margin-top: 14px }` matched the second
    card in a `.grid-2` — an adjacent sibling that is also a grid item — and
    pushed it 14px down inside its own track.
    """
    dash.set_viewport_size({"width": width, "height": 1000})
    violations: dict[str, Any] = {}
    for key in PAGES:
        _show(dash, key)
        found = dash.evaluate(_ROW_ALIGN_JS)
        if found:
            violations[key] = found
    assert not violations, json.dumps(violations, indent=2)[:4000]


# --------------------------------------------------------------------------
# seeded payloads
# --------------------------------------------------------------------------


def _peer_payload(count: int) -> tuple[list[dict], list[dict], dict[str, int]]:
    """`count` LAN peers, each with the backend row that carries its traffic.

    Request counts are deliberately uneven — the radar encodes share as
    distance, so equal shares would not exercise the placement at all.
    """
    peers, backends, routed = [], [], {}
    for i in range(count):
        agent_id = f"peer{i:02d}"
        peers.append(
            {
                "agent_id": agent_id,
                "hostname": f"node-{i:02d}",
                "listen_url": f"http://10.0.0.{20 + i}:11400",
                "role": "peer",
            }
        )
        backends.append(
            {
                "id": f"peer:{agent_id}",
                "base_url": f"http://10.0.0.{20 + i}:11400/v1",
                "provider": "custom",
                "enabled": True,
                "local": False,
                "agent_id": agent_id,
                "health": {
                    "status": "online" if i % 3 else "timeout",
                    "http_status": 200,
                    "model_count": 3 + i,
                    "models": [f"model-{i}-a", f"model-{i}-b"],
                    "detail": "",
                    "last_check_epoch_s": 1.0,
                },
                "in_flight": 0,
                "latency_ema_ms": 12.0 + i,
                "cloud_provider": "",
                "max_concurrency": 0,
                "api_key_set": False,
            }
        )
        routed[f"peer:{agent_id}"] = 100 - i * 7
    return peers, backends, routed


def _local_backend_payload(count: int) -> list[dict]:
    """`count` local backends with mixed health, so card heights differ.

    A failing probe adds an explanation `.inset` and an action row, which is
    what made the short card's stretched dead space obvious.
    """
    states = [
        ("online", 200),
        ("online", 401),  # "needs a key" — renders the explanation block
        ("offline", None),
        ("timeout", None),
    ]
    rows = []
    for i in range(count):
        status, http = states[i % len(states)]
        rows.append(
            {
                "id": f"local-{i}",
                "base_url": f"http://127.0.0.1:{8000 + i}/v1",
                "provider": "vllm",
                "enabled": True,
                "local": True,
                "agent_id": "e2e-host",
                "health": {
                    "status": status,
                    "http_status": http,
                    "model_count": 2,
                    "models": [
                        f"local-model-{i}",
                        "a-much-longer-model-id:70b-instruct-q8",
                    ],
                    "detail": "seeded",
                    "last_check_epoch_s": 1.0,
                },
                "in_flight": i,
                "latency_ema_ms": 9.0 + i,
                "cloud_provider": "",
                "max_concurrency": 0,
                "api_key_set": False,
            }
        )
    return rows


def _seed_status(page, *, peers: int = 0, local: int = 0) -> None:
    """Rewrite /netllm/v1/status on the wire, then reload.

    The agent is real and reports what it actually found; these counts are
    the thing under test, so they are injected at the boundary rather than by
    starting N stub servers.
    """
    peer_rows, peer_backends, routed = _peer_payload(peers)
    local_rows = _local_backend_payload(local)

    def handler(route) -> None:  # noqa: ANN001 - playwright type
        response = route.fetch()
        data = response.json()
        data["peers"] = peer_rows
        data["backends"] = peer_backends + local_rows
        data["routed_requests"] = {**routed, "local-0": 40} if peers else {}
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(data),
        )

    page.route("**/netllm/v1/status*", handler)
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(300)


# --------------------------------------------------------------------------
# mesh radar
# --------------------------------------------------------------------------

_MESH_JS = """
() => {
  const mesh = document.querySelector('#page-overview .mesh');
  const ledger = document.querySelector('#page-overview .mesh-ledger');
  if (!mesh) return null;
  const visible = (e) => {
    if (!e) return false;
    const r = e.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const box = mesh.getBoundingClientRect();
  const cards = [...mesh.querySelectorAll('.mesh-node')].map((n) => {
    const r = n.getBoundingClientRect();
    return {left: r.left, top: r.top, right: r.right, bottom: r.bottom,
            self: n.classList.contains('self'),
            text: (n.textContent || '').trim().slice(0, 30)};
  });
  return {
    radarVisible: visible(mesh),
    ledgerVisible: visible(ledger),
    box: {left: box.left, top: box.top, right: box.right, bottom: box.bottom,
          width: box.width, height: box.height},
    cards,
  };
}
"""


@pytest.mark.parametrize("peers", [0, 1, 2, 3, 4, 6, 8])
def test_mesh_radar_never_overlaps_or_escapes(dash, peers: int) -> None:
    """No two node cards intersect, and none paints outside the stage.

    The radar solved positions in a fixed 454x400 coordinate space and drew
    fixed-size cards into a fluid box, so any node count above a handful — or
    any narrowing of the panel — stacked cards on top of each other. It now
    solves against the measured stage and card sizes, and hands over to the
    ledger when no arrangement fits. Either outcome is acceptable here; a
    tangle is not.
    """
    dash.set_viewport_size({"width": 1440, "height": 1000})
    _seed_status(dash, peers=peers, local=1)
    _show(dash, "overview")
    data = dash.evaluate(_MESH_JS)
    assert data is not None, "overview did not render a mesh stage"

    # Exactly one of the two representations is on screen.
    assert data["radarVisible"] != data["ledgerVisible"], (
        f"radar={data['radarVisible']} ledger={data['ledgerVisible']}"
    )
    if not data["radarVisible"]:
        return

    cards = data["cards"]
    assert len(cards) == peers + 1, f"expected {peers + 1} cards, got {len(cards)}"

    box = data["box"]
    for card in cards:
        assert card["left"] >= box["left"] - 1, f"{card} escapes left of {box}"
        assert card["right"] <= box["right"] + 1, f"{card} escapes right of {box}"
        assert card["top"] >= box["top"] - 1, f"{card} escapes above {box}"
        assert card["bottom"] <= box["bottom"] + 1, f"{card} escapes below {box}"

    for i, a in enumerate(cards):
        for b in cards[i + 1 :]:
            overlap_x = min(a["right"], b["right"]) - max(a["left"], b["left"])
            overlap_y = min(a["bottom"], b["bottom"]) - max(a["top"], b["top"])
            assert overlap_x <= 0 or overlap_y <= 0, (
                f"mesh cards overlap by {overlap_x:.0f}x{overlap_y:.0f}px: "
                f"{a['text']!r} vs {b['text']!r}"
            )


def test_mesh_falls_back_to_the_ledger_when_the_panel_is_narrow(dash) -> None:
    """A panel too narrow for a card ring shows the table, not a pile."""
    dash.set_viewport_size({"width": 900, "height": 1000})
    _seed_status(dash, peers=4, local=1)
    _show(dash, "overview")
    data = dash.evaluate(_MESH_JS)
    assert data is not None
    assert data["ledgerVisible"], "narrow panel should hand over to the ledger"
    assert not data["radarVisible"]


def test_mesh_uses_the_width_it_is_given(dash) -> None:
    """The stage scales with its panel instead of sitting at a fixed size.

    A fixed 454px box inside an ~800px panel left a wide dead margin either
    side while the cards were crammed into the middle.
    """
    dash.set_viewport_size({"width": 1600, "height": 1000})
    _seed_status(dash, peers=3, local=1)
    _show(dash, "overview")
    data = dash.evaluate(_MESH_JS)
    assert data is not None and data["radarVisible"]
    panel_width = dash.evaluate(
        "() => document.querySelector('#page-overview .mesh-stage')"
        ".getBoundingClientRect().width"
    )
    assert data["box"]["width"] > 454, (
        f"stage stayed at {data['box']['width']}px inside a {panel_width}px panel"
    )
    assert data["box"]["width"] <= panel_width + 1


def test_mesh_cards_carry_no_repeated_prose(dash) -> None:
    """The key explains the diagram once; the cards do not repeat it N times."""
    dash.set_viewport_size({"width": 1440, "height": 1000})
    _seed_status(dash, peers=3, local=1)
    _show(dash, "overview")
    text = dash.evaluate(
        "() => [...document.querySelectorAll('#page-overview .mesh-node')]"
        ".map((n) => n.textContent).join(' | ')"
    )
    assert "of requests served here" not in text
    assert "online" not in text
    assert "peer ·" not in text
    key = dash.evaluate(
        "() => (document.querySelector('#page-overview .mesh-key')"
        " || {}).textContent || ''"
    )
    assert "share" in key


# --------------------------------------------------------------------------
# backend cards
# --------------------------------------------------------------------------


@pytest.mark.parametrize("count", [1, 2, 3, 4, 6, 9])
@pytest.mark.parametrize("width", WIDTHS)
def test_local_backend_cards_line_up_at_every_count(
    dash, count: int, width: int
) -> None:
    """Cards in one row start at the same height and stay inside the page.

    Odd counts are the interesting case: the last row is partly filled, which
    is where a wrap bug would show.
    """
    dash.set_viewport_size({"width": width, "height": 1000})
    _seed_status(dash, peers=0, local=count)
    _show(dash, "backends")

    cards = dash.evaluate(
        """
        () => {
          const grid = document.querySelector('#page-backends .grid-cards');
          if (!grid) return null;
          const gr = grid.getBoundingClientRect();
          return {
            grid: {left: gr.left, right: gr.right},
            cards: [...grid.children].map((c) => {
              const r = c.getBoundingClientRect();
              return {left: r.left, top: r.top, right: r.right, bottom: r.bottom};
            }),
          };
        }
        """
    )
    assert cards is not None, "backends page rendered no local card grid"
    assert len(cards["cards"]) == count

    for card in cards["cards"]:
        assert card["left"] >= cards["grid"]["left"] - 1
        assert card["right"] <= cards["grid"]["right"] + 1

    rows: dict[int, list[dict]] = {}
    for card in cards["cards"]:
        rows.setdefault(round(card["top"]), []).append(card)
    for card_row in rows.values():
        tops = {round(c["top"]) for c in card_row}
        assert len(tops) == 1, f"cards in one row start at different tops: {tops}"

    # No card may overlap another.
    for i, a in enumerate(cards["cards"]):
        for b in cards["cards"][i + 1 :]:
            overlap_x = min(a["right"], b["right"]) - max(a["left"], b["left"])
            overlap_y = min(a["bottom"], b["bottom"]) - max(a["top"], b["top"])
            assert overlap_x <= 1 or overlap_y <= 1, "backend cards overlap"


# --------------------------------------------------------------------------
# sub-views and drill-ins
# --------------------------------------------------------------------------

# Every view a page can be in that is NOT reachable by clicking a nav item.
# The suite above walks the 11 top-level pages only, which is exactly why the
# pool detail shipped with its two columns built out of unclassed <div>s:
# neither `.page.active` nor `.stack` matched them, no container owned the
# gap, and three panels in each column butted together at 0px.
SUB_VIEWS = [
    ("models", "pool detail", "state.openPoolId = 'e2e-pool'; render();"),
    (
        "models",
        "nodes view",
        "state.openPoolId = null; modelsViewMode = 'nodes'; render();",
    ),
    ("models", "aliases view", "modelsViewMode = 'aliases'; render();"),
    ("backends", "local scope", "backendsScope = 'local'; render();"),
    ("backends", "remote scope", "backendsScope = 'remote'; render();"),
    ("logs", "level filter", "state.logLevelFilter = new Set(['error']); render();"),
]


def _open_sub_view(page, key: str, script: str) -> None:
    _show(page, key)
    page.evaluate(script)
    page.wait_for_timeout(250)


def _reset_sub_views(page) -> None:
    page.evaluate(
        "state.openPoolId = null; modelsViewMode = 'pools'; backendsScope = 'all';"
        " state.logLevelFilter = new Set(); render();"
    )
    page.wait_for_timeout(150)


@pytest.fixture
def dash_with_pool(dash):  # noqa: ANN001, ANN201 - playwright types
    """A dashboard whose draft holds a pool, so the pool detail has content."""
    dash.evaluate(
        """
        () => {
          state.configDraft.routing.model_pools = {
            'e2e-pool': {hosts: ['peer:aaaa1111', 'peer:bbbb2222'],
                         models: ['gemma4:27b', 'qwen3.5-35b']},
          };
        }
        """
    )
    return dash


@pytest.mark.parametrize("width", WIDTHS)
def test_sub_views_keep_the_container_rhythm(dash_with_pool, width: int) -> None:  # noqa: ANN001
    """Drill-in views obey the same spacing rule as the pages that own them.

    A sub-view is still a column of boxes; building its columns out of bare
    `<div>` wrappers opts out of every container gap rule in the stylesheet.
    The fix is always to name the container (`.stack`), never to reintroduce
    a `.panel + .panel` adjacency rule.
    """
    dash_with_pool.set_viewport_size({"width": width, "height": 1000})
    violations: dict[str, Any] = {}
    for key, label, script in SUB_VIEWS:
        _open_sub_view(dash_with_pool, key, script)
        found = dash_with_pool.evaluate(_RHYTHM_JS, RHYTHM_BY_CONTAINER)
        if found:
            violations[f"{key}: {label}"] = found
        _reset_sub_views(dash_with_pool)
    assert not violations, json.dumps(violations, indent=2)[:4000]


@pytest.mark.parametrize("width", WIDTHS)
def test_sub_views_paint_inside_their_containers(dash_with_pool, width: int) -> None:  # noqa: ANN001
    """Containment holds in a drill-in too, at both widths."""
    dash_with_pool.set_viewport_size({"width": width, "height": 1000})
    violations: dict[str, Any] = {}
    for key, label, script in SUB_VIEWS:
        _open_sub_view(dash_with_pool, key, script)
        found = dash_with_pool.evaluate(_CONTAINMENT_JS)
        if found:
            violations[f"{key}: {label}"] = found
        _reset_sub_views(dash_with_pool)
    assert not violations, json.dumps(violations, indent=2)[:4000]


def test_pool_detail_columns_are_named_containers(dash_with_pool) -> None:  # noqa: ANN001
    """The specific defect: the pool detail's two columns own their gap.

    Asserted structurally as well as geometrically — a future refactor that
    reverts the columns to bare `<div>`s but happens to leave one panel per
    column would pass a gap check by accident.
    """
    _open_sub_view(dash_with_pool, "models", "state.openPoolId = 'e2e-pool'; render();")
    columns = dash_with_pool.evaluate(
        """
        () => {
          const grid = document.querySelector('.page.active .grid-2');
          if (!grid) return null;
          return [...grid.children].map((c) => ({
            cls: c.className,
            panels: c.querySelectorAll(':scope > .panel').length,
            gap: getComputedStyle(c).rowGap,
          }));
        }
        """
    )
    assert columns, "pool detail rendered no column grid"
    for column in columns:
        if column["panels"] < 2:
            continue
        assert "stack" in column["cls"], (
            "a pool-detail column holding several panels is not a named "
            f"container: {column}"
        )
        assert column["gap"] == f"{STACK_GAP}px", column
