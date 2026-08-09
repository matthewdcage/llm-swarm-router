"""Cross-surface control descriptors — Axis D (docs/extending/PROGRAM.md §3).

A `ControlDescriptor` states, once, that a **unit of control** exists and on
which surfaces it is obliged to exist. It describes *presence obligations,
not widgets*: nothing here is rendered, and no UI is generated from it
(PROGRAM.md §6.3 rejects generated SwiftUI/dashboard JS, and is right).

**Granularity is a tab / feature, not a config field.** That is a deliberate
deviation from the literal Axis D text, recorded in
`docs/extending/08-control-parity.md`. One descriptor per config field was
measured against PROGRAM.md §7's own tripwire (">20% intentionally-absent
means the spec is wrong — redesign it, do not add entries") and failed it:
22.2% absent with the CLI excluded, 62% with the CLI required, at a cost of
~500 hand-maintained facts and zero deletions. At tab granularity every
descriptor has a real renderer, a real button, a real Swift symbol, and often
a real CLI command family, and the day-one ledger is 2/16.

Field-level parity did not disappear — it moved to where it belongs, as a
coverage **disposition** computed from the surfaces themselves in
`tests/conformance/kit_config_surfaces.py` (F-21's own prescribed fix: every
field is schema-rendered, hand-rendered, derived, or ledgered).

Served additively on the existing `GET /netllm/v1/config/schema` under
`"controls"` — no new endpoint, no client break.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

#: A surface that can carry a control. `cli` is deliberately NOT a per-field
#: surface: only 6 of 79 leaf config field names collide with a Typer long
#: option, and just two of those (`cloud enable --auth`, `--region`) actually
#: write a config field. It is required for **action**-kind controls only.
SurfaceKey = Literal["dashboard", "macos", "cli"]

#: `config` — edits `NetllmConfig` through `POST /netllm/v1/admin/config`.
#: `view`   — read-only presentation of live agent state.
#: `action` — performs something; `admin_route` and `cli` are discriminating.
ControlKind = Literal["config", "view", "action"]


@dataclass(frozen=True)
class ControlDescriptor:
    """One unit of control and the surfaces obliged to carry it."""

    key: str
    """Stable id. For tab controls this is also the dashboard tab key."""

    kind: ControlKind

    title: str
    """Human label, for the generated docs table."""

    dashboard_renderer: str
    """Identifier that must appear as a `TAB_RENDERERS` value in dashboard.js."""

    swift_symbol: str
    """Identifier or `sectionHeader("…")` call that must appear in the macOS
    settings source. A per-surface *presence unit*: `sources` is a tab on the
    web and a section inside `routingTab` on macOS, and both count as present.
    """

    surfaces_required: tuple[SurfaceKey, ...]
    """Genuinely per-control. `ui.menubar_*` is a macOS concept and
    `netllm install` is CLI-only by nature; a uniform roster would be a lie."""

    admin_route: str = ""
    """HTTP route this control drives. Left empty for `config`-kind controls:
    all 99 config fields go through `POST /netllm/v1/admin/config`, so
    asserting it there is a tautology. Discriminating for `action` kind."""

    cli: tuple[str, ...] = ()
    """Leaf command paths as Typer renders them, e.g. `("cloud enable",)`."""

    config_sections: tuple[str, ...] = ()
    """`config_schema` sections this control edits, if any."""

    is_tab: bool = True
    """True when the dashboard presence unit is a tab: `dashboard_renderer`
    must then be a `TAB_RENDERERS` value and `key` must have both a
    `data-tab=` button and an `id="tab-<key>"` section in index.html.
    False for actions that live inside another tab (drain, rediscover)."""


CONTROLS: tuple[ControlDescriptor, ...] = (
    ControlDescriptor(
        key="status",
        kind="view",
        title="Status",
        dashboard_renderer="renderStatusTab",
        swift_symbol="statusTab",
        surfaces_required=("dashboard", "macos"),
        admin_route="/netllm/v1/status",
        cli=("status",),
    ),
    ControlDescriptor(
        key="serving",
        kind="view",
        title="Serving stats",
        dashboard_renderer="renderServingTab",
        swift_symbol="servingTab",
        surfaces_required=("dashboard", "macos"),
        admin_route="/netllm/v1/telemetry",
    ),
    ControlDescriptor(
        key="backends",
        kind="view",
        title="Backends",
        dashboard_renderer="renderBackendsTab",
        swift_symbol="backendsTab",
        surfaces_required=("dashboard", "macos"),
        admin_route="/netllm/v1/backends",
    ),
    ControlDescriptor(
        key="models",
        kind="view",
        title="Models",
        dashboard_renderer="renderModelsTab",
        swift_symbol="modelsTab",
        surfaces_required=("dashboard", "macos"),
        cli=("models",),
    ),
    ControlDescriptor(
        key="peers",
        kind="view",
        title="Swarm peers",
        dashboard_renderer="renderPeersTab",
        swift_symbol="peersTab",
        surfaces_required=("dashboard", "macos"),
        admin_route="/netllm/v1/peers",
        cli=("peers",),
    ),
    ControlDescriptor(
        key="agent",
        kind="config",
        title="Agent settings",
        dashboard_renderer="renderAgentTab",
        swift_symbol="agentTab",
        surfaces_required=("dashboard", "macos"),
        config_sections=("agent",),
    ),
    ControlDescriptor(
        key="discovery",
        kind="config",
        title="Discovery & known servers",
        dashboard_renderer="renderDiscoveryTab",
        swift_symbol="discoveryTab",
        surfaces_required=("dashboard", "macos"),
        config_sections=("discovery",),
        cli=("discover",),
    ),
    ControlDescriptor(
        key="swarm",
        kind="config",
        title="Swarm",
        dashboard_renderer="renderSwarmTab",
        swift_symbol="swarmTab",
        surfaces_required=("dashboard", "macos"),
        config_sections=("swarm",),
        cli=("join", "swarm-token"),
    ),
    ControlDescriptor(
        key="routing",
        kind="config",
        title="Routing",
        dashboard_renderer="renderRoutingTab",
        swift_symbol="routingTab",
        surfaces_required=("dashboard", "macos"),
        config_sections=("routing",),
    ),
    ControlDescriptor(
        key="sources",
        kind="config",
        title="Sources",
        dashboard_renderer="renderSourcesTab",
        # A section inside `routingTab`, not a tab. Landmine: a naive
        # tab-set diff calls this absent on macOS; a human calls it present.
        swift_symbol='sectionHeader("Sources")',
        surfaces_required=("dashboard", "macos"),
        cli=("sources list", "sources toggle"),
    ),
    ControlDescriptor(
        key="cloud",
        kind="config",
        title="Cloud providers",
        dashboard_renderer="renderCloudTab",
        swift_symbol="cloudTab",
        surfaces_required=("dashboard", "macos"),
        config_sections=("cloud",),
        cli=("cloud list", "cloud enable", "cloud disable", "cloud set-key"),
    ),
    ControlDescriptor(
        key="ui",
        kind="config",
        title="App UI settings",
        dashboard_renderer="renderUiTab",
        swift_symbol="uiTab",
        surfaces_required=("dashboard", "macos"),
        config_sections=("ui",),
    ),
    ControlDescriptor(
        key="logs",
        kind="view",
        title="Agent logs",
        dashboard_renderer="renderLogsTab",
        swift_symbol="logsTab",
        surfaces_required=("dashboard", "macos"),
        admin_route="/netllm/v1/logs",
    ),
    ControlDescriptor(
        key="tools",
        kind="action",
        title="Doctor & Test",
        dashboard_renderer="renderToolsTab",
        swift_symbol="toolsTab",
        surfaces_required=("dashboard", "macos", "cli"),
        admin_route="/netllm/v1/doctor",
        cli=("doctor", "test", "gateway"),
    ),
    # --- action-kind controls that are not tabs ---------------------------
    ControlDescriptor(
        key="drain",
        kind="action",
        title="Drain (take out of rotation)",
        dashboard_renderer="renderDrainButton",
        swift_symbol="drainButton",
        surfaces_required=("dashboard", "macos", "cli"),
        admin_route="/netllm/v1/admin/drain",
        cli=("drain",),
        is_tab=False,
    ),
    ControlDescriptor(
        key="rediscover",
        kind="action",
        title="Re-run local discovery",
        dashboard_renderer="renderStatusTab",
        swift_symbol="runDiscover",
        surfaces_required=("dashboard", "macos", "cli"),
        admin_route="/netllm/v1/admin/discover",
        cli=("discover",),
        is_tab=False,
    ),
)


def control_descriptor_payload() -> list[dict[str, Any]]:
    """Wire projection of `CONTROLS`, merged into the schema document.

    Additive: a client that has never heard of `"controls"` ignores it.
    """
    return [
        {
            "key": control.key,
            "kind": control.kind,
            "title": control.title,
            "dashboard_renderer": control.dashboard_renderer,
            "swift_symbol": control.swift_symbol,
            "surfaces_required": list(control.surfaces_required),
            "admin_route": control.admin_route,
            "cli": list(control.cli),
            "config_sections": list(control.config_sections),
            "is_tab": control.is_tab,
        }
        for control in CONTROLS
    ]
