"""UI-6: the doctor payload is a check inventory, not a prose list.

Two things are under test and they pull in opposite directions:

1. `checks[]` exists, covers every check that ran (passing ones included), and
   carries a stable `id` a client can key a fix button on.
2. `ok`, `issues[]` and `notes[]` still mean exactly what they meant before,
   because the macOS app and `netllm doctor` read them. The derivation is the
   compatibility contract, so it is asserted directly rather than by example.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from netllm_agent.admin import DOCTOR_CHECK_IDS, doctor_payload
from netllm_agent.service import AgentService
from netllm_core.doctor_checks import (
    DOCTOR_ACTION_KINDS,
    DOCTOR_SEVERITIES,
    doctor_check,
    doctor_report,
)
from netllm_core.models import NetllmConfig

REPO_ROOT = Path(__file__).resolve().parents[1]
ROUTES_MANIFEST = REPO_ROOT / "tests/contract/routes.json"

#: The frozen roster, written out here rather than imported, so that renaming a
#: check id in the source is a two-file diff that says what it is doing. Same
#: discipline as tests/contract/test_error_taxonomy_table.py.
FROZEN_CHECK_IDS = frozenset(
    {
        "swarm.open_lan_no_token",
        "swarm.token_but_open_inference",
        "agent.gateway_advertise",
        "backends.healthy",
        "backends.auth_required",
        "cloud.provider_key",
        "cloud.unknown_provider",
        "config.deprecated_key",
        "config.schema_version",
        "swarm.mdns_available",
        "swarm.peer_config",
    }
)

#: The exact note string the open-LAN advisory has always emitted. It is the
#: one wire string this feature could have quietly reworded, and the macOS app
#: shows it verbatim.
OPEN_LAN_NOTE = (
    "LAN swarm is open (no cluster token). Enable Require cluster token "
    "in Settings on untrusted networks."
)


def _payload(cfg: NetllmConfig, tmp_path: Path | None = None) -> dict[str, Any]:
    # A config_path inside tmp_path keeps the deprecation report off the
    # developer's real ~/.netllm/config.toml.
    path = (tmp_path / "config.toml") if tmp_path else None
    return doctor_payload(cfg, AgentService(cfg), path)


def _lan_cfg() -> NetllmConfig:
    cfg = NetllmConfig()
    cfg.agent.listen = "0.0.0.0:11400"
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    return cfg


# --- roster and row shape ---------------------------------------------------


def test_every_check_id_is_in_the_frozen_roster(tmp_path: Path) -> None:
    payload = _payload(NetllmConfig(), tmp_path)
    ids = {c["id"] for c in payload["checks"]}
    assert ids <= FROZEN_CHECK_IDS
    assert set(DOCTOR_CHECK_IDS) == FROZEN_CHECK_IDS


def test_every_roster_check_reports_even_when_it_passes(tmp_path: Path) -> None:
    """The point of the feature: a passing check leaves a row.

    Before UI-6 a clean run produced an empty payload, so the Doctor page could
    say "no issues" and nothing else -- it could not say what it had checked.
    """
    payload = _payload(NetllmConfig(), tmp_path)
    assert {c["id"] for c in payload["checks"]} == FROZEN_CHECK_IDS


def test_check_rows_are_uniquely_keyed_by_id_and_subject(tmp_path: Path) -> None:
    payload = _payload(NetllmConfig(), tmp_path)
    keys = [(c["id"], c["subject"]) for c in payload["checks"]]
    assert len(keys) == len(set(keys))


def test_every_row_has_the_declared_shape(tmp_path: Path) -> None:
    for check in _payload(_lan_cfg(), tmp_path)["checks"]:
        assert isinstance(check["id"], str) and check["id"]
        assert isinstance(check["title"], str) and check["title"]
        assert isinstance(check["ok"], bool)
        assert check["severity"] in DOCTOR_SEVERITIES
        assert isinstance(check["detail"], str)
        assert check["action"]["kind"] in DOCTOR_ACTION_KINDS


def test_a_passing_row_is_info_and_offers_nothing_to_click(tmp_path: Path) -> None:
    """Severity describes what was *found*, so a pass is never "error"."""
    for check in _payload(NetllmConfig(), tmp_path)["checks"]:
        if check["ok"]:
            assert check["severity"] == "info"
            assert check["action"]["kind"] == "none"


# --- backward compatibility -------------------------------------------------


def test_issues_and_notes_are_exactly_the_derivation(tmp_path: Path) -> None:
    """The whole compatibility contract, asserted as an identity."""
    cfg = _lan_cfg()
    cfg.agent.role = "gateway"
    cfg.agent.advertise = False
    payload = _payload(cfg, tmp_path)
    checks = payload["checks"]
    assert payload["issues"] == [
        {"title": c["title"], "fix": c.get("fix", "")}
        for c in checks
        if not c["ok"] and c["severity"] == "error"
    ]
    assert payload.get("notes", []) == [
        c["detail"] for c in checks if not c["ok"] and c["severity"] == "warn"
    ]
    assert payload["ok"] is (not payload["issues"])


def test_open_lan_without_a_token_stays_advisory(tmp_path: Path) -> None:
    """It is a note, not a failure -- and the structured shape must not
    quietly promote it to one. `ok` stays True and `issues` stays empty."""
    payload = _payload(_lan_cfg(), tmp_path)
    row = next(c for c in payload["checks"] if c["id"] == "swarm.open_lan_no_token")
    assert row["ok"] is False
    assert row["severity"] == "warn"
    assert row["detail"] == OPEN_LAN_NOTE
    assert payload["notes"] == [OPEN_LAN_NOTE]
    assert all("open" not in issue["title"].lower() for issue in payload["issues"])


def test_notes_is_omitted_when_empty(tmp_path: Path) -> None:
    """A client testing `"notes" in payload` behaved a particular way before
    this change and must keep behaving that way."""
    payload = _payload(NetllmConfig(), tmp_path)
    assert "notes" not in payload


# --- one condition flips one check ------------------------------------------


@pytest.mark.parametrize(
    ("mutate", "flipped_ids"),
    [
        # Setting a token also clears the open-LAN advisory, which is the
        # point of setting one; both flips are named so the test says what it
        # expects rather than tolerating any extra movement.
        (
            lambda cfg: (
                setattr(cfg.swarm, "cluster_token", "tok"),
                setattr(cfg.swarm, "require_token_for_inference", False),
            ),
            {"swarm.token_but_open_inference", "swarm.open_lan_no_token"},
        ),
        (
            lambda cfg: (
                setattr(cfg.agent, "role", "gateway"),
                setattr(cfg.agent, "advertise", False),
            ),
            {"agent.gateway_advertise"},
        ),
    ],
)
def test_each_condition_flips_only_the_checks_it_is_about(
    mutate, flipped_ids: set[str], tmp_path: Path
) -> None:
    baseline = {
        (c["id"], c["subject"]): c["ok"]
        for c in _payload(_lan_cfg(), tmp_path)["checks"]
    }
    cfg = _lan_cfg()
    mutate(cfg)
    after = {
        (c["id"], c["subject"]): c["ok"] for c in _payload(cfg, tmp_path)["checks"]
    }
    flipped = {key for key, ok in after.items() if baseline.get(key) != ok}
    assert {check_id for check_id, _ in flipped} == flipped_ids


def test_the_flag_that_fixes_a_check_makes_it_pass(tmp_path: Path) -> None:
    cfg = _lan_cfg()
    cfg.swarm.cluster_token = "tok"
    cfg.swarm.require_token_for_inference = True
    row = next(
        c
        for c in _payload(cfg, tmp_path)["checks"]
        if c["id"] == "swarm.token_but_open_inference"
    )
    assert row["ok"] is True


# --- declared actions -------------------------------------------------------


def test_declared_actions_name_a_route_that_exists(tmp_path: Path) -> None:
    """A typo'd endpoint has to fail here rather than 404 in a browser.

    Asserted as a set intersection against the frozen route table, which is the
    same manifest tests/test_contract.py holds the app to.
    """
    manifest = json.loads(ROUTES_MANIFEST.read_text(encoding="utf-8"))["routes"]
    known = {(row["path"], method) for row in manifest for method in row["methods"]}

    cfg = _lan_cfg()
    cfg.swarm.cluster_token = "tok"
    cfg.swarm.require_token_for_inference = False
    cfg.agent.role = "gateway"
    cfg.agent.advertise = False
    cfg.swarm.mdns = True

    declared = [
        c["action"]
        for c in _payload(cfg, tmp_path)["checks"]
        if c["action"]["kind"] in {"config_patch", "admin_post"}
    ]
    assert declared, "no check declared a server-side remediation to check"
    for action in declared:
        assert (action["endpoint"], action["method"]) in known


def test_no_check_declares_a_generic_fix_executor(tmp_path: Path) -> None:
    """`POST /netllm/v1/admin/doctor/fix {id}` is the shape this feature
    deliberately does not have: it turns one admin route into an open-ended one
    whose effect the caller cannot inspect."""
    for check in _payload(_lan_cfg(), tmp_path)["checks"]:
        assert "doctor/fix" not in check["action"].get("endpoint", "")


# --- the shared builder -----------------------------------------------------


def test_builder_rejects_an_unknown_severity() -> None:
    with pytest.raises(ValueError, match="severity"):
        doctor_check("x.y", ok=False, title="t", severity="fatal")


def test_builder_rejects_an_unknown_action_kind() -> None:
    with pytest.raises(ValueError, match="action kind"):
        doctor_check("x.y", ok=False, title="t", action={"kind": "shell"})


def test_report_of_no_checks_is_ok() -> None:
    assert doctor_report([]) == {"ok": True, "checks": [], "issues": []}
