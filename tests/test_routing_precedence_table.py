"""The routing-precedence table in docs/config-reference.md is normative.

F-25's last open item: "globals → policies → source → scenario → headers,
five layers that can each set strategy/local_only/allow_cloud — document a
single precedence table in the config reference; the logic is correct but
only discoverable by reading resolve_routing."

A documented table that nothing checks drifts. This module parses the layer
order straight out of the doc and asserts it against `resolve_routing`, so
the doc and the function fail together or not at all.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from netllm_core.models import (
    CloudConfig,
    RoutingConfig,
    RoutingPolicy,
    ScenarioRule,
    SourceConfig,
)
from netllm_core.routing_policy import resolve_routing

DOC = Path(__file__).resolve().parents[1] / "docs" / "config-reference.md"

BEGIN = "<!-- ROUTING-PRECEDENCE-TABLE:BEGIN -->"
END = "<!-- ROUTING-PRECEDENCE-TABLE:END -->"

# One distinct strategy per layer, so the winner names its own layer.
LAYER_STRATEGY = {
    "globals": "round_robin",
    "policies": "least_load",
    "source": "failover",
    "scenario": "latency_weighted",
    "headers": "local_first",
}


def documented_layers() -> list[str]:
    """The layer column of the doc's table, lowest precedence first."""
    text = DOC.read_text()
    body = text.split(BEGIN, 1)[1].split(END, 1)[0]
    layers: list[tuple[int, str]] = []
    for line in body.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or not re.fullmatch(r"\d+", cells[0]):
            continue
        layers.append((int(cells[0]), cells[1]))
    assert layers, f"no numbered table rows found between the markers in {DOC}"
    ranks = [n for n, _ in layers]
    assert ranks == sorted(ranks) == list(range(1, len(ranks) + 1)), (
        f"table rows must be numbered 1..n in precedence order, got {ranks}"
    )
    return [name for _, name in layers]


def resolve_with(layers: set[str]):
    """Run resolve_routing with exactly `layers` setting a strategy.

    Every layer present sets its own distinct strategy, so the returned
    strategy identifies which layer won.
    """
    routing = RoutingConfig(default_strategy=LAYER_STRATEGY["globals"])
    if "policies" in layers:
        routing.policies = [
            RoutingPolicy(
                name="p",
                strategy=LAYER_STRATEGY["policies"],
                allow_cloud=True,
                enabled=True,
            )
        ]
    source = None
    if "source" in layers or "scenario" in layers:
        source = SourceConfig(id="cli")
        if "source" in layers:
            source.strategy = LAYER_STRATEGY["source"]
        if "scenario" in layers:
            source.scenarios = {
                "think": ScenarioRule(strategy=LAYER_STRATEGY["scenario"])
            }
    return resolve_routing(
        routing,
        model="m",
        api_format="openai",
        header_local_only=False,
        header_strategy=(LAYER_STRATEGY["headers"] if "headers" in layers else None),
        cloud=CloudConfig(),
        source=source,
        scenario="think",
    )


def test_documented_layer_order_is_the_expected_five() -> None:
    assert documented_layers() == [
        "globals",
        "policies",
        "source",
        "scenario",
        "headers",
    ]


def test_each_documented_layer_beats_every_layer_below_it() -> None:
    """The whole table at once: with layers 1..k present, layer k wins."""
    layers = documented_layers()
    present: set[str] = set()
    for layer in layers:
        present.add(layer)
        resolved = resolve_with(set(present))
        assert resolved.strategy == LAYER_STRATEGY[layer], (
            f"docs/config-reference.md ranks {layer!r} above "
            f"{sorted(present - {layer})}, but resolve_routing returned "
            f"{resolved.strategy!r}"
        )


@pytest.mark.parametrize(
    ("lower", "higher"),
    [
        ("globals", "policies"),
        ("policies", "source"),
        ("source", "scenario"),
        ("scenario", "headers"),
    ],
)
def test_adjacent_pairs_resolve_to_the_higher_layer(lower: str, higher: str) -> None:
    """Each adjacent pair in isolation — so a failure names the exact edge
    of the order that drifted, not just 'the table is wrong'."""
    resolved = resolve_with({"globals", lower, higher})
    assert resolved.strategy == LAYER_STRATEGY[higher]


def test_header_local_only_is_a_ceiling_not_a_layer() -> None:
    """Documented asymmetry 1: no layer below can reopen cloud/remote."""
    source = SourceConfig(id="cli", allow_cloud=True)
    resolved = resolve_routing(
        RoutingConfig(),
        model="m",
        api_format="openai",
        header_local_only=True,
        cloud=CloudConfig(enabled=True, fallback="local"),
        source=source,
    )
    assert resolved.local_only is True
    assert resolved.allow_cloud_inject is False
    assert resolved.cloud_leads is False


def test_cloud_master_switch_is_a_floor_not_a_layer() -> None:
    """Documented asymmetry 2: allow_cloud only re-enables cloud while the
    [cloud] master switch is on."""
    source = SourceConfig(id="cli", allow_cloud=True)
    off = resolve_routing(
        RoutingConfig(),
        model="m",
        api_format="openai",
        header_local_only=False,
        cloud=CloudConfig(enabled=False),
        source=source,
    )
    assert off.allow_cloud_inject is False
    on = resolve_routing(
        RoutingConfig(),
        model="m",
        api_format="openai",
        header_local_only=False,
        cloud=CloudConfig(enabled=True),
        source=source,
    )
    assert on.allow_cloud_inject is True


def test_local_only_is_checked_before_allow_cloud_within_a_layer() -> None:
    """Documented tie-break: a layer setting both is local-only."""
    source = SourceConfig(id="cli", local_only=True, allow_cloud=True)
    resolved = resolve_routing(
        RoutingConfig(),
        model="m",
        api_format="openai",
        header_local_only=False,
        cloud=CloudConfig(),
        source=source,
    )
    assert resolved.local_only is True
    assert resolved.allow_cloud_inject is False
