"""Dashboard controls the UI audit found DEAD, BROKEN or MISSING.

Each test below corresponds to a defect confirmed by execution during the
2026-08-09 UI surface audit (149 Swift controls, 99 web controls inventoried).
They are cheap presence/parity checks rather than browser tests, because the
failure mode in every case was a control rendering against data the server
never sent -- which a served-content assertion catches.
"""

from __future__ import annotations

from pathlib import Path

from netllm_core.models import NetllmConfig, UiConfig

REPO_ROOT = Path(__file__).resolve().parents[1]
STATIC = REPO_ROOT / "packages/netllm-agent/src/netllm_agent/static"


def test_config_summary_exports_every_ui_field() -> None:
    """The dashboard renders the `ui` section straight from the schema.

    `config_summary` exported three of ten fields, so seven controls rendered
    against `undefined`: the six menubar_* toggles showed CHECKED against a
    stored False, and the model_favorites editor POSTed `[]` -- which, because
    lists are full-replace, wiped favourites set from the macOS app.
    """
    from netllm_agent.admin import config_summary

    cfg = NetllmConfig()
    exported = set(config_summary(cfg)["ui"])
    missing = set(UiConfig.model_fields) - exported
    assert not missing, (
        f"config_summary omits {sorted(missing)} — the dashboard renders those "
        "controls against undefined and can destroy their stored values on save"
    )


def test_model_favorites_survives_a_dashboard_round_trip() -> None:
    """Read the exported value, send it back, keep the favourites."""
    from netllm_agent.admin import config_summary
    from netllm_core.config_merge import apply_config_patch

    cfg = NetllmConfig()
    cfg.ui.model_favorites = ["qwen3-coder", "gpt-oss-120b"]
    echoed = config_summary(cfg)["ui"]
    merged = apply_config_patch(cfg, {"ui": dict(echoed)})
    assert merged.ui.model_favorites == ["qwen3-coder", "gpt-oss-120b"]


# test_drain_has_a_control_on_the_dashboard moved to
# tests/conformance/kit_config_surfaces.py as
# test_drain_is_wired_end_to_end_on_the_dashboard, next to the `drain`
# ControlDescriptor whose dashboard leg it proves (Axis D).


def test_status_json_link_points_at_the_status_endpoint() -> None:
    """The link read href="/" which 307s back to /ui/ for an HTML Accept —
    so "Status JSON" reloaded the dashboard instead of showing JSON."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    marker = 'id="btn-status-json"'
    line = next(x for x in html.splitlines() if marker in x)
    assert 'href="/netllm/v1/status"' in line, (
        f"Status JSON link is wrong: {line.strip()}"
    )


def shipped_js() -> list[Path]:
    """Every JavaScript file the agent serves, core and pages.

    The dashboard used to be one `dashboard.js`; it is now the core module,
    `schema-form.js`, `bootstrap.js` and one module per page under `pages/`.
    Globbed rather than listed so a new page cannot be added outside the
    invariant below simply by not being named here.
    """
    files = sorted(STATIC.glob("*.js")) + sorted(STATIC.glob("pages/*.js"))
    assert len(files) > 12, f"only found {len(files)} JS files — did they move?"
    return files


def test_every_html_id_the_js_binds_exists() -> None:
    """Both directions are silent failures: getElementById on a missing id
    returns null, and an unbound id is simply dead.

    Widened from `dashboard.js` alone to every shipped module: the split moved
    most of the DOM binding into `pages/*.js`, so scanning only the core file
    would have left thirteen files unchecked. An id may be satisfied either by
    `index.html` (the static chrome) or by an `elem.id = "…"` assignment in the
    JS itself — pages build their own subtrees, and a search box the page
    creates and then re-focuses by id is correctly wired.
    """
    import re

    html = (STATIC / "index.html").read_text(encoding="utf-8")
    html_ids = set(re.findall(r'id="([^"]+)"', html))

    bound: dict[str, list[str]] = {}
    created: set[str] = set()
    for path in shipped_js():
        source = path.read_text(encoding="utf-8")
        for name in re.findall(r'getElementById\("([^"]+)"\)', source):
            bound.setdefault(name, []).append(path.name)
        created |= set(re.findall(r'\.id\s*=\s*"([^"]+)"', source))

    assert bound, "no getElementById() calls found at all — the scan is broken"
    missing = {
        name: sorted(set(files))
        for name, files in bound.items()
        if name not in html_ids and name not in created
    }
    assert not missing, (
        "these ids are bound by the dashboard JS but nothing in index.html or "
        f"the JS ever creates them: {missing}"
    )
