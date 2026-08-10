# Stub — a control descriptor

Guide: [../04-cli-and-control-plane.md](../04-cli-and-control-plane.md) ·
Kit: `tests/conformance/kit_config_surfaces.py` ·
Design as built: [../08-control-parity.md](../08-control-parity.md)

## A tab

`packages/netllm-core/src/netllm_core/control_plane.py`, inside `CONTROLS`:

```python
    ControlDescriptor(
        key="mypage",                      # stable id, not necessarily the page key
        kind="view",                       # config | view | action
        title="My Tab",
        dashboard_renderer="renderMyPage", # defined in static/pages/mypage.js
        swift_symbol="myTab",              # must appear in the macOS settings source
        surfaces_required=("dashboard", "macos"),
        config_sections=(),
        is_tab=True,
    ),
```

`is_tab=True` also obliges the page key to be in `const PAGES` in
`dashboard.js`, to be registered by `registerPage("mypage", …)` in its own
module, and for `index.html` to carry a `data-page="mypage"` button **and** an
`id="page-mypage"` section. Add the page to `DASHBOARD_CONTROLS` in
`tests/conformance/kit_config_surfaces.py`.

## An action

```python
    ControlDescriptor(
        key="myaction",
        kind="action",
        title="My Action",
        dashboard_renderer="renderMyActionButton",
        swift_symbol="myActionButton",
        surfaces_required=("dashboard", "macos", "cli"),
        admin_route="/netllm/v1/admin/myaction",   # discriminating for actions
        cli=("myaction",),                          # as Typer renders the leaf
        is_tab=False,
    ),
```

## A config field with no control yet

Do **not** add a descriptor. Add a dated exception to
`tests/conformance/ledgers/control-parity.toml` — see
[ledger-entry.md](ledger-entry.md). If the ledger passes **20%** of
descriptors, the spec is wrong: redesign it, do not add entries
([../PROGRAM.md](../PROGRAM.md) §7). The kit asserts the tripwire.

## Regenerate the obligations table

```bash
python3 scripts/generate-registry-artifacts.py
```

## Verify

```bash
uv run pytest tests/conformance/kit_config_surfaces.py -k mypage
```
