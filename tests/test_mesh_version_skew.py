"""A peer on another version must not poison this node.

`peer_config_warnings` is the one place a peer's self-reported version changes
what this agent says, and it is the place the prerelease-ordering bug was
actually exposed: `fetch_latest_release` filters prereleases out of the update
check, this does not. An operator running `0.5.0rc1` was told the peer on
`0.5.0` was behind them.

The rest of the file pins the containment property. Peer records arrive from
whatever is on the LAN, so the rule is: they may produce *text*, never a
decision about this node's own config, and never an exception.
"""

from __future__ import annotations

import copy

import pytest
from netllm_core.models import NetllmConfig
from netllm_core.update import mesh_skew


def _service_with_peer(*, my_version: str, peer_version: str, monkeypatch):
    from netllm_agent.service import AgentService
    from netllm_discovery.swarm import PeerRecord

    monkeypatch.setattr("netllm_agent.service.status.get_version", lambda: my_version)
    service = AgentService(NetllmConfig())
    service.swarm.register_peer(
        PeerRecord(
            agent_id="p",
            listen_url="http://192.168.1.11:11400",
            hostname="other-mac",
            version=peer_version,
        )
    )
    return service


def test_a_prerelease_operator_is_not_told_to_downgrade(monkeypatch) -> None:
    """THE regression. This agent runs the release candidate; the peer runs
    the final release. The peer is newer, and the warning has to say so."""
    service = _service_with_peer(
        my_version="0.5.0rc1", peer_version="0.5.0", monkeypatch=monkeypatch
    )
    warnings = service.peer_config_warnings()
    assert len(warnings) == 1
    assert "this agent is older than peer other-mac" in warnings[0], (
        "the release candidate was reported as NEWER than the release it is a "
        "candidate for — compare_versions read '0.5.0rc1' as [0,5,0,1]"
    )


def test_a_prerelease_and_a_build_are_not_the_same_version(monkeypatch) -> None:
    """`0.5.0rc1` and `0.5.0.1` both scraped to [0,5,0,1] and compared equal,
    so a genuinely skewed mesh produced no warning at all."""
    service = _service_with_peer(
        my_version="0.5.0.1", peer_version="0.5.0rc1", monkeypatch=monkeypatch
    )
    warnings = service.peer_config_warnings()
    assert len(warnings) == 1
    assert "peer other-mac is older than this agent" in warnings[0]


@pytest.mark.parametrize(
    ("peer_version", "phrase"),
    [
        ("0.5.3", "one minor of skew is fully supported"),
        ("0.7.0", "two minors of skew"),
        ("0.9.0", "outside the compatibility promise"),
    ],
)
def test_the_warning_states_the_support_level_not_just_update_it(
    monkeypatch, peer_version: str, phrase: str
) -> None:
    """N-1 supported, N-2 degraded, beyond that unsupported. Before this the
    advice was the same sentence whether the peer was one patch behind or two
    majors, which is why nobody acted on it."""
    service = _service_with_peer(
        my_version="0.5.0", peer_version=peer_version, monkeypatch=monkeypatch
    )
    warnings = service.peer_config_warnings()
    assert len(warnings) == 1
    assert phrase in warnings[0], warnings[0]


@pytest.mark.parametrize("peer_version", ["unknown", "v", "not-a-version", "🙂"])
def test_an_unreadable_peer_version_never_becomes_confident_advice(
    monkeypatch, peer_version: str
) -> None:
    """Anything on the LAN can heartbeat, so a version string is untrusted
    input. It used to be degraded to `0.0.0`, which made a peer sending junk
    look two majors behind: the node then told the operator the mesh was
    outside the compatibility promise, naming a version nobody runs.

    Now it reports exactly what it saw and assesses nothing.
    """
    service = _service_with_peer(
        my_version="0.5.0", peer_version=peer_version, monkeypatch=monkeypatch
    )
    warnings = service.peer_config_warnings()
    assert len(warnings) == 1
    assert "unreadable netllm version" in warnings[0]
    assert "is older than" not in warnings[0]
    assert "compatibility promise" not in warnings[0]


def test_an_absent_peer_version_is_silent(monkeypatch) -> None:
    """Peers predating the field send "". That is not drift, it is an old
    heartbeat shape, and it must stay silent."""
    service = _service_with_peer(
        my_version="0.5.0", peer_version="", monkeypatch=monkeypatch
    )
    assert service.peer_config_warnings() == []


def test_an_absurd_peer_version_is_reported_as_unreadable(monkeypatch) -> None:
    """A 500-digit "version" is not a version, and we no longer pretend it is.

    This test previously asserted the warning "this agent is older than peer
    other-mac" — a confident claim about a number nobody is running, from data
    a stranger on the LAN controls. Version segments are now bounded to 9
    digits, so this is classified unreadable and the operator is told to go
    look at the peer instead.

    Two reasons the change is an improvement rather than a relaxation:
    the advice is honest, and the bound is what stops `int()` raising on a
    4400-digit string, which used to 500 `GET /netllm/v1/status` for everyone
    (CPython refuses decimal conversions past 4300 digits). The original
    intent — nothing raises — is still asserted, and now holds for inputs
    that used to blow up.
    """
    service = _service_with_peer(
        my_version="0.5.0", peer_version="9" * 500, monkeypatch=monkeypatch
    )
    warnings = service.peer_config_warnings()
    assert len(warnings) == 1
    assert "unreadable netllm version" in warnings[0]
    assert "older than peer" not in warnings[0], (
        "an unreadable version must not produce a confident skew claim"
    )


def test_a_peer_never_changes_this_agents_config(monkeypatch) -> None:
    """Containment: computing the warnings must not touch our own config.

    Nothing in the mesh protocol should be able to make this node rewrite its
    own settings, and the cheapest way to keep that true is to assert it.
    """
    service = _service_with_peer(
        my_version="0.5.0", peer_version="9.9.9", monkeypatch=monkeypatch
    )
    before = copy.deepcopy(service.config.model_dump(mode="json"))
    service.peer_config_warnings()
    service.status_payload()
    assert service.config.model_dump(mode="json") == before


def test_status_payload_carries_the_warning_for_the_dashboard(monkeypatch) -> None:
    service = _service_with_peer(
        my_version="0.5.0", peer_version="0.9.0", monkeypatch=monkeypatch
    )
    payload = service.status_payload()
    assert "outside the compatibility promise" in payload["peer_warnings"][0]


def test_skew_is_symmetric() -> None:
    """Which machine is newer changes the sentence's subject, not the support
    level — otherwise two machines in one mesh disagree about their own skew."""
    for left, right in [("0.5.0", "0.7.0"), ("0.5.0", "0.6.0"), ("0.5.0", "2.0.0")]:
        assert mesh_skew(left, right).level == mesh_skew(right, left).level
