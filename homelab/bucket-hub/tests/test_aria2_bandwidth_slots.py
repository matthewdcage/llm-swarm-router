"""Bandwidth-gated aria2 dispatch policy."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

HUB_ROOT = Path(__file__).resolve().parents[1]


def _worker_mod():
    sys.path.insert(0, str(HUB_ROOT))
    if "hub-worker" in sys.modules:
        return importlib.reload(sys.modules["hub-worker"])
    return importlib.import_module("hub-worker")


def test_dispatch_blocked_at_max_slots():
    mod = _worker_mod()
    assert not mod.aria2_dispatch_allowed(
        16, 0, max_active=16, target_mbps=600, bootstrap_slots=4
    )


def test_dispatch_bootstrap_below_cap():
    mod = _worker_mod()
    assert mod.aria2_dispatch_allowed(
        3, 900_000_000, max_active=16, target_mbps=600, bootstrap_slots=4
    )


def test_dispatch_stops_when_aggregate_at_target():
    mod = _worker_mod()
    # 600 Mbps * 0.9 headroom => 67_500_000 B/s
    cap = int(600 * 1_000_000 / 8 * 0.9)
    assert not mod.aria2_dispatch_allowed(
        8, cap, max_active=16, target_mbps=600, bootstrap_slots=4, headroom=0.9
    )
    assert mod.aria2_dispatch_allowed(
        8, cap - 1, max_active=16, target_mbps=600, bootstrap_slots=4, headroom=0.9
    )


def test_target_mbps_zero_uses_slot_cap_only():
    mod = _worker_mod()
    assert mod.aria2_dispatch_allowed(
        10, 0, max_active=16, target_mbps=0, bootstrap_slots=4
    )
    assert not mod.aria2_dispatch_allowed(
        16, 0, max_active=16, target_mbps=0, bootstrap_slots=4
    )
