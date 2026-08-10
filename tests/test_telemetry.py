"""Tests for unified telemetry API and oMLX normalizers."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from netllm_agent.app import create_app
from netllm_agent.telemetry import (
    LEDGER_MAX_KEYS,
    LEDGER_OVERFLOW_KEY,
    RequestLedger,
    TelemetryService,
)
from netllm_core.models import NetllmConfig

FIXTURES = Path(__file__).parent / "fixtures" / "omlx"


@pytest.fixture
def client() -> TestClient:
    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    app = create_app(cfg)
    with TestClient(app) as test_client:
        yield test_client


def test_telemetry_endpoint_schema(client: TestClient) -> None:
    resp = client.get("/netllm/v1/telemetry?watch=0")
    assert resp.status_code == 200
    data = resp.json()
    assert data["schema_version"] == 1
    assert "router" in data
    assert data["omlx"]["available"] is False
    assert "history" in data
    assert len(data["history"]["router_rps"]) <= 60


def test_telemetry_records_router_usage(tmp_path: Path) -> None:
    stats_path = tmp_path / "stats.json"
    svc = TelemetryService(stats_path=stats_path)
    svc.record_usage(prompt_tokens=10, completion_tokens=5, prefill_duration=0.1)
    assert svc._session.requests == 1
    assert svc._session.prompt_tokens == 10
    assert svc._alltime.completion_tokens == 5


@pytest.mark.asyncio
async def test_telemetry_lazy_omlx_probe_without_watch() -> None:
    svc = TelemetryService()
    probe = AsyncMock(return_value={"available": True, "live": {"prefill_tps": 1.0}})
    with patch("netllm_agent.telemetry.probe_omlx_telemetry", probe):
        payload = await svc.build_payload(
            type(
                "S",
                (),
                {"pool": type("P", (), {"backends": []})(), "_shardless_fallbacks": 0},
            )(),
            scopes={"omlx"},
        )
    probe.assert_not_called()
    assert payload["omlx"]["available"] is False


@pytest.mark.asyncio
async def test_telemetry_probes_omlx_when_watching() -> None:
    svc = TelemetryService()
    svc.subscribe()
    probe = AsyncMock(
        return_value={
            "available": True,
            "live": {"prefill_tps": 10.0, "generation_tps": 5.0},
            "session": {"avg_prefill_tps": 9.0},
        }
    )
    fake_service = type(
        "S",
        (),
        {
            "pool": type(
                "P",
                (),
                {"backends": [], "routed_counts": {}, "capacity_rejections": {}},
            )(),
            "_shardless_fallbacks": 0,
        },
    )()
    with patch("netllm_agent.telemetry.probe_omlx_telemetry", probe):
        payload = await svc.build_payload(fake_service, scopes={"omlx"})
    probe.assert_called_once()
    assert payload["omlx"]["available"] is True
    svc.unsubscribe()


def test_normalize_omlx_stats_payload() -> None:
    from netllm_discovery.local import _normalize_omlx_stats_payload

    data = json.loads((FIXTURES / "stats_session.json").read_text(encoding="utf-8"))
    out = _normalize_omlx_stats_payload(data)
    assert out["total_prompt_tokens"] == 1200
    assert out["avg_generation_tps"] == 41.0


def test_telemetry_persists_alltime(tmp_path: Path) -> None:
    stats_path = tmp_path / "stats.json"
    svc = TelemetryService(stats_path=stats_path)
    svc.record_usage(prompt_tokens=5, completion_tokens=3)
    assert stats_path.is_file()
    svc2 = TelemetryService(stats_path=stats_path)
    assert svc2._alltime.prompt_tokens == 5
    assert svc2._alltime.completion_tokens == 3


def test_a_pre_ui2_stats_file_drops_its_fabricated_durations(
    tmp_path: Path,
) -> None:
    """A stats.json written before UI-2 holds `latency*0.3` / `latency*0.7`.

    Recognisable in the wild by the ratio: a real file from a running mesh had
    137796.6 / 305841.7, i.e. 31%/69%. Those totals are dropped on load and
    throughput reads `None` until a real stream measures one. The request and
    token counts beside them are genuine and are kept — the history was real,
    only its derivation into a rate was invented.
    """
    stats_path = tmp_path / "stats.json"
    stats_path.write_text(
        json.dumps(
            {
                "requests": 83269,
                "prompt_tokens": 334786738,
                "completion_tokens": 15336451,
                "total_prefill_duration": 137796.6083361518,
                "total_generation_duration": 305841.7066232752,
                "started_at": 1784857333.612004,
            }
        ),
        encoding="utf-8",
    )
    svc = TelemetryService(stats_path=stats_path)

    assert svc._alltime.requests == 83269
    assert svc._alltime.prompt_tokens == 334786738
    assert svc._alltime.total_prefill_duration == 0.0
    assert svc._alltime.total_generation_duration == 0.0
    assert svc._alltime.avg_prefill_tps() is None
    assert svc._alltime.avg_generation_tps() is None


def test_history_tokens_do_not_inflate_the_first_measured_rate(
    tmp_path: Path,
) -> None:
    """The trap in dropping only the durations.

    The average divides cumulative tokens by cumulative duration, so zeroing
    the duration while keeping 334M historical tokens would divide them by the
    first stream's handful of seconds — a far more wrong number than the one
    being removed. Only tokens from requests that produced a measured duration
    count towards the rate.
    """
    stats_path = tmp_path / "stats.json"
    stats_path.write_text(
        json.dumps(
            {
                "requests": 1000,
                "prompt_tokens": 10_000_000,
                "completion_tokens": 500_000,
                "total_prefill_duration": 300.0,
                "total_generation_duration": 700.0,
            }
        ),
        encoding="utf-8",
    )
    svc = TelemetryService(stats_path=stats_path)
    # One measured streaming request: 100 prompt tokens over 2s of prefill.
    svc.record_usage(
        prompt_tokens=100,
        completion_tokens=50,
        prefill_duration=2.0,
        generation_duration=5.0,
    )
    assert svc._alltime.avg_prefill_tps() == pytest.approx(50.0)
    assert svc._alltime.avg_generation_tps() == pytest.approx(10.0)


def test_measured_durations_survive_a_restart(tmp_path: Path) -> None:
    """The marker is what distinguishes a migrated file from a pre-UI-2 one."""
    stats_path = tmp_path / "stats.json"
    svc = TelemetryService(stats_path=stats_path)
    svc.record_usage(
        prompt_tokens=100,
        completion_tokens=50,
        prefill_duration=2.0,
        generation_duration=5.0,
    )
    assert json.loads(stats_path.read_text())["durations_measured"] is True

    reloaded = TelemetryService(stats_path=stats_path)
    assert reloaded._alltime.avg_prefill_tps() == pytest.approx(50.0)


def test_unmeasured_requests_do_not_dilute_the_rate(tmp_path: Path) -> None:
    """A non-streaming request adds tokens with no observable prefill time."""
    svc = TelemetryService(stats_path=tmp_path / "stats.json")
    svc.record_usage(prompt_tokens=100, completion_tokens=50, prefill_duration=2.0)
    svc.record_usage(prompt_tokens=9000, completion_tokens=9000)  # non-streaming
    # 100/2, not 9100/2 — the unmeasured request contributed no prefill time.
    assert svc._alltime.avg_prefill_tps() == pytest.approx(50.0)
    assert svc._alltime.prompt_tokens == 9100  # display total still counts it


def test_normalize_omlx_activity_payload() -> None:
    from netllm_discovery.local import _normalize_omlx_activity_payload

    data = json.loads((FIXTURES / "activity.json").read_text(encoding="utf-8"))
    out = _normalize_omlx_activity_payload(data)
    assert out["prefill_tps"] == 120.5
    assert out["generation_tps"] == 45.2


@pytest.mark.asyncio
async def test_probe_omlx_telemetry_parses_admin() -> None:
    from netllm_discovery.local import probe_omlx_telemetry

    session = json.loads((FIXTURES / "stats_session.json").read_text(encoding="utf-8"))
    alltime = json.loads((FIXTURES / "stats_alltime.json").read_text(encoding="utf-8"))
    activity = json.loads((FIXTURES / "activity.json").read_text(encoding="utf-8"))

    class FakeResponse:
        def __init__(self, payload: dict, status: int = 200) -> None:
            self.status_code = status
            self._payload = payload

        def json(self) -> dict:
            return self._payload

    class FakeClient:
        async def get(self, url: str, **kwargs: object) -> FakeResponse:
            params = kwargs.get("params") or {}
            if url.endswith("/api/server-info") or url.endswith("/api/status"):
                return FakeResponse({"loaded_models": ["demo-model"]})
            if url.endswith("/api/stats") and params.get("scope") == "session":
                return FakeResponse(session)
            if url.endswith("/api/stats") and params.get("scope") == "alltime":
                return FakeResponse(alltime)
            if url.endswith("/api/activity"):
                return FakeResponse(activity)
            raise AssertionError(f"unexpected url {url} params={params}")

    backends = [
        type(
            "B",
            (),
            {
                "provider": "omlx",
                "enabled": True,
                "base_url": "http://127.0.0.1:8099/v1",
                "health": type("H", (), {"status": "online", "model_count": 1})(),
            },
        )()
    ]
    stats = await probe_omlx_telemetry(backends, FakeClient())  # type: ignore[arg-type]
    assert stats is not None
    assert stats["available"] is True
    assert stats["session"]["total_requests"] == 12
    assert stats["alltime"]["total_requests"] == 25600
    assert stats["live"]["generation_tps"] == 45.2


# --- F-09: all-time counters must not hit the disk on every request --------


def test_alltime_stats_write_is_debounced(tmp_path: Path) -> None:
    """record_usage used to mkdir + json.dumps + write_text on the event loop
    once per proxied request."""
    stats = tmp_path / "stats.json"
    service = TelemetryService(stats_path=stats)

    service.record_usage(prompt_tokens=1, completion_tokens=1)
    assert stats.is_file(), "the first record should persist immediately"
    first = stats.stat().st_mtime_ns

    for _ in range(50):
        service.record_usage(prompt_tokens=1, completion_tokens=1)

    assert stats.stat().st_mtime_ns == first, (
        "subsequent records within the debounce window must not rewrite the file"
    )
    # In-memory counters still advance for the live telemetry payload.
    assert service._alltime.requests == 51


async def test_close_flushes_pending_alltime_stats(tmp_path: Path) -> None:
    """A clean shutdown must not lose whatever the debounce is holding."""
    stats = tmp_path / "stats.json"
    service = TelemetryService(stats_path=stats)
    service.record_usage(prompt_tokens=1)
    for _ in range(5):
        service.record_usage(prompt_tokens=1)

    await service.close()

    persisted = json.loads(stats.read_text())
    assert persisted["requests"] == 6


# --- F-10: psutil is a declared dependency, so the host block is real ------


def test_host_block_is_populated() -> None:
    """_host_block imported psutil behind try/except but nothing declared it,
    so GET /netllm/v1/telemetry returned host: null on every shipped install
    — the web dashboard on Linux/Windows silently lost the feature."""
    block = TelemetryService._host_block()
    assert block is not None, "psutil must be a declared dependency of netllm-agent"
    assert set(block) >= {"cpu_percent", "memory_used_gb", "memory_total_gb"}


# --------------------------------------------------------------------------- #
# UI-1 — the windowed, dimensioned request ledger
# --------------------------------------------------------------------------- #

T0 = 1_800_000_000.0  # a fixed, span-aligned-ish epoch so the tests are exact


def _ledger() -> RequestLedger:
    return RequestLedger(started_at=T0)


def test_ledger_counts_each_span_independently() -> None:
    ledger = _ledger()
    # Chronological, because a ring records at "now" and time only moves
    # forward: 4 h ago, then 200 s ago, then now.
    ledger.record(backend_id="alpha", model="m", now=T0 - 4 * 3600)
    ledger.record(backend_id="alpha", model="m", now=T0 - 200)
    ledger.record(backend_id="alpha", model="m", now=T0)

    row = ledger.windows_payload(now=T0)["by_backend"]["alpha"]
    assert row["60"] == 1
    assert row["300"] == 2
    assert row["86400"] == 3


def test_ledger_buckets_age_out_at_the_span_boundary() -> None:
    """A request recorded at T is invisible at T + span + 1, for every span."""
    ledger = _ledger()
    ledger.record(backend_id="alpha", now=T0)
    payload = ledger.windows_payload(now=T0)
    spans = payload["spans_s"]
    assert spans, "spans are server-declared"

    for span in spans:
        row = ledger.windows_payload(now=T0 + span + 1)["by_backend"]["alpha"]
        assert row[str(span)] == 0, f"{span}s bucket still visible past its boundary"
    # And it was visible immediately before ageing out of the shortest span.
    row = ledger.windows_payload(now=T0 + spans[0] - 1)["by_backend"]["alpha"]
    assert row[str(spans[0])] == 1


def test_ledger_idle_time_reads_zero_rather_than_the_last_value() -> None:
    """The §1 defect: history.router_rps used to hold its last sample forever
    because it was only appended to from inside record_usage."""
    ledger = _ledger()
    for i in range(5):
        ledger.record(backend_id="alpha", now=T0 + i)

    busy = ledger.rps_series(now=T0 + 4)
    assert busy[-1] == 1.0

    idle = ledger.rps_series(now=T0 + 300)
    assert idle[-1] == 0.0
    assert sum(idle) == 0.0, "a fully idle minute must decay to zero, not freeze"
    assert len(idle) == 60


def test_ledger_series_is_second_resolution_and_ordered_oldest_first() -> None:
    ledger = _ledger()
    ledger.record(backend_id="alpha", now=T0 - 2)
    ledger.record(backend_id="alpha", now=T0)
    ledger.record(backend_id="alpha", now=T0)

    series = ledger.rps_series(now=T0)
    assert series[-1] == 2.0
    assert series[-3] == 1.0
    assert series[-2] == 0.0


def test_ledger_caps_cardinality_and_reports_truncation() -> None:
    """by_model is keyed on the client's requested model string, so an
    unbounded key space here is a memory-exhaustion vector."""
    ledger = _ledger()
    for i in range(10_000):
        ledger.record(model=f"junk-{i}", now=T0)

    payload = ledger.windows_payload(now=T0)
    by_model = payload["by_model"]
    assert len(by_model) <= LEDGER_MAX_KEYS + 1, "dimension grew past its cap"
    assert LEDGER_OVERFLOW_KEY in by_model
    assert payload["truncated"]["by_model"] > 0

    # Nothing is dropped: every request is still accounted somewhere.
    total = sum(row["300"] for row in by_model.values())
    assert total == 10_000


def test_ledger_source_rows_carry_surfaces_top_models_and_last_seen() -> None:
    ledger = _ledger()
    for _ in range(3):
        ledger.record(source_id="cursor", model="gemma4:27b", surface="openai", now=T0)
    ledger.record(source_id="cursor", model="qwen:7b", surface="anthropic", now=T0 + 1)

    row = ledger.windows_payload(now=T0 + 1)["by_source"]["cursor"]
    assert row["requests"]["60"] == 4
    assert row["surfaces"] == {"openai": 3, "anthropic": 1}
    assert row["top_models"][0] == {"model": "gemma4:27b", "count": 3}
    assert row["last_seen_at"] == pytest.approx(T0 + 1)


def test_ledger_source_model_breakdown_is_bounded() -> None:
    """by_source[].top_models is a bounded top-N, not a source x model matrix."""
    ledger = _ledger()
    for i in range(500):
        ledger.record(source_id="cursor", model=f"junk-{i}", now=T0)
    row = ledger.windows_payload(now=T0)["by_source"]["cursor"]
    assert len(row["top_models"]) <= 5


@pytest.mark.asyncio
async def test_ledger_survives_concurrent_recording() -> None:
    """The record path takes no lock (an asyncio.Lock around per-request
    accounting would serialise completions), so interleaving must be safe."""
    import asyncio

    ledger = _ledger()

    async def worker(tag: str) -> None:
        for _ in range(200):
            ledger.record(backend_id=tag, model="m", now=T0)
            await asyncio.sleep(0)

    await asyncio.gather(*(worker(f"b{i}") for i in range(8)))

    payload = ledger.windows_payload(now=T0)
    assert sum(row["300"] for row in payload["by_backend"].values()) == 1600
    assert payload["by_model"]["m"]["300"] == 1600


def test_ledger_record_overhead_stays_in_the_noise() -> None:
    """Perf guard. This runs on every completed request; a future change that
    quietly makes routing slower should fail here rather than in production.

    The bound is deliberately loose (100 us/record against a measured cost two
    orders of magnitude below it) so it cannot flake on a loaded CI box, while
    still catching anything that adds a sort, a scan or an allocation.
    """
    import time as _time
    import tracemalloc

    ledger = _ledger()
    # Warm the dimension dicts so the measured loop is the steady path.
    ledger.record(backend_id="alpha", model="m", source_id="s", policy_key="0:p")

    tracemalloc.start()
    before = tracemalloc.get_traced_memory()[0]
    started = _time.perf_counter()
    for _ in range(10_000):
        ledger.record(
            backend_id="alpha",
            model="m",
            source_id="s",
            policy_key="0:p",
            surface="openai",
            prompt_tokens=10,
            completion_tokens=20,
            latency_s=0.5,
            ttft_s=0.1,
        )
    elapsed = _time.perf_counter() - started
    grew = tracemalloc.get_traced_memory()[0] - before
    tracemalloc.stop()

    assert elapsed < 1.0, f"10k ledger records took {elapsed:.3f}s (>100us each)"
    # Preallocated buckets: 10k records must not grow the structure at all.
    assert grew < 64 * 1024, f"steady-state recording allocated {grew} bytes"


# --------------------------------------------------------------------------- #
# UI-2 — real TTFT, real percentiles
# --------------------------------------------------------------------------- #


def test_ttft_percentiles_track_a_known_distribution() -> None:
    ledger = _ledger()
    for _ in range(90):
        ledger.record(ttft_s=0.06, latency_s=1.0, now=T0)  # 60 ms
    for _ in range(10):
        ledger.record(ttft_s=1.0, latency_s=2.0, now=T0)  # 1000 ms

    payload = ledger.latency_payload(now=T0)
    assert payload["ttft_samples"] == 100
    assert 50.0 <= payload["ttft_p50_ms"] <= 100.0
    assert payload["ttft_p95_ms"] > payload["ttft_p50_ms"]
    assert payload["window_s"] > 0


def test_ttft_percentile_is_null_with_no_samples_and_real_with_one() -> None:
    ledger = _ledger()
    empty = ledger.latency_payload(now=T0)
    assert empty["ttft_samples"] == 0
    assert empty["ttft_p50_ms"] is None, "no data must be null, never 0.0"

    ledger.record(ttft_s=0.3, latency_s=1.0, now=T0)
    single = ledger.latency_payload(now=T0)
    assert single["ttft_samples"] == 1
    assert 200.0 <= single["ttft_p50_ms"] <= 400.0


def test_non_streaming_request_is_excluded_from_the_ttft_population() -> None:
    """A response that arrives in one piece has no observable TTFT. Folding
    its total latency in would make the percentile describe nothing."""
    ledger = _ledger()
    ledger.record(backend_id="alpha", latency_s=4.0, ttft_s=None, now=T0)

    assert ledger.latency_payload(now=T0)["ttft_samples"] == 0
    assert ledger.latency_payload(now=T0)["ttft_p50_ms"] is None
    # It still counts as a request and still feeds the backend latency figure.
    assert ledger.windows_payload(now=T0)["by_backend"]["alpha"]["60"] == 1
    backend = ledger.backend_latency_payload("alpha", now=T0)
    assert backend["samples"] == 1
    assert backend["p50_ms"] is not None


def test_backend_latency_is_null_for_a_backend_with_no_traffic() -> None:
    ledger = _ledger()
    payload = ledger.backend_latency_payload("never-routed", now=T0)
    assert payload == {
        "p50_ms": None,
        "p95_ms": None,
        "samples": 0,
        "window_s": payload["window_s"],
    }


def test_live_block_reports_measured_rates_and_null_without_measurement() -> None:
    ledger = _ledger()
    empty = ledger.live_payload(now=T0)
    assert empty["prefill_tps"] is None
    assert empty["generation_tps"] is None
    assert empty["requests_per_s"] == 0.0

    # 100 prompt tokens in 0.1 s of prefill; 200 completion tokens in 0.9 s.
    ledger.record(
        prompt_tokens=100, completion_tokens=200, ttft_s=0.1, latency_s=1.0, now=T0
    )
    live = ledger.live_payload(now=T0)
    assert live["prefill_tps"] == pytest.approx(1000.0)
    assert live["generation_tps"] == pytest.approx(200 / 0.9, rel=1e-3)
    assert live["requests_per_s"] > 0.0


def test_prefill_rate_is_absent_rather_than_a_rescaled_latency() -> None:
    """The UI-2 regression guard. accounting.py used to pass
    ``latency_s * 0.3`` / ``latency_s * 0.7`` as the durations, so
    avg_prefill_tps was ``prompt_tokens / (0.3 x total_latency)`` — a
    hardcoded constant shipped as a measurement."""
    svc = TelemetryService(stats_path=Path("/nonexistent/stats.json"))
    svc.record_usage(prompt_tokens=100, completion_tokens=50, latency_s=2.0)
    scope = svc._session.to_dict()
    assert scope["prompt_tokens"] == 100
    assert scope["avg_prefill_tps"] is None
    assert scope["avg_generation_tps"] is None

    svc.record_usage(
        prompt_tokens=100,
        completion_tokens=50,
        prefill_duration=0.5,
        generation_duration=1.5,
        latency_s=2.0,
        ttft_s=0.5,
    )
    scope = svc._session.to_dict()
    # 200, not 400. This assertion used to read 400 — total prompt tokens (200,
    # across both requests) over the measured 0.5s — which counted the first
    # request's tokens even though nothing timed their prefill. That is the
    # same error as the 0.3-of-latency split in miniature: attributing a
    # measurement to traffic that was never measured. Only the 100 tokens whose
    # prefill was actually observed belong in the rate.
    assert scope["avg_prefill_tps"] == pytest.approx(200.0)
    assert scope["prompt_tokens"] == 200  # the display total still counts both


# --------------------------------------------------------------------------- #
# UI-2 — where TTFT is measured, and where it is honestly absent
# --------------------------------------------------------------------------- #


class _StubPool:
    def mark_success(self, backend: object, latency_ms: float) -> None:
        pass


class _StubService:
    """The three members AttemptRecorder touches, and nothing else."""

    def __init__(self, stats_path: Path) -> None:
        self.pool = _StubPool()
        self.telemetry = TelemetryService(stats_path=stats_path)
        self._request_count = 0

    def _mark_shard_success(self, shard: object) -> None:
        pass


def _recorder(tmp_path: Path):
    from netllm_agent.service.accounting import AttemptRecorder

    service = _StubService(tmp_path / "stats.json")
    return AttemptRecorder(service), service  # type: ignore[arg-type]


def _backend():
    from netllm_core.pool import Backend

    return Backend(id="alpha", provider="lmstudio", base_url="http://alpha/v1")


@pytest.mark.parametrize(
    ("chunk", "expected"),
    [
        # OpenAI: the role-only opener is a protocol frame, not a token.
        ('data: {"choices":[{"delta":{"role":"assistant","content":""}}]}\n\n', False),
        ('data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n', True),
        ('data: {"choices":[{"delta":{"reasoning_content":"hm"}}]}\n\n', True),
        ('data: {"choices":[{"delta":{"tool_calls":[{"index":0}]}}]}\n\n', True),
        ("data: [DONE]\n\n", False),
        # Anthropic: message_start and ping precede the first token.
        ('data: {"type":"message_start","message":{"usage":{}}}\n\n', False),
        ('data: {"type":"ping"}\n\n', False),
        (
            'data: {"type":"content_block_delta",'
            '"delta":{"type":"text_delta","text":"Hi"}}\n\n',
            True,
        ),
        ("data: not-json\n\n", False),
        ("", False),
    ],
)
def test_only_content_bearing_frames_stop_the_ttft_clock(
    chunk: str, expected: bool
) -> None:
    from netllm_agent.service.accounting import _sse_carries_content

    assert _sse_carries_content(chunk) is expected


def test_streaming_request_records_measured_prefill_and_generation(
    tmp_path: Path,
) -> None:
    recorder, service = _recorder(tmp_path)
    started = time.monotonic()
    # A role-only opener must not stop the clock; the content frame must.
    recorder.observe_stream_chunk(
        'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n', started_at=started
    )
    assert recorder._ttft_s is None
    recorder.observe_stream_chunk(
        'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n', started_at=started
    )
    ttft = recorder._ttft_s
    assert ttft is not None and ttft >= 0.0

    # A later frame must not move the stamp.
    recorder.observe_stream_chunk(
        'data: {"choices":[{"delta":{"content":" there"}}]}\n\n', started_at=started
    )
    assert recorder._ttft_s == ttft

    recorder.success(
        backend=_backend(),
        model="m",
        latency_s=2.0,
        prompt_tokens=10,
        completion_tokens=20,
    )
    counters = service.telemetry._session
    assert counters.total_prefill_duration == pytest.approx(ttft)
    assert counters.total_generation_duration == pytest.approx(2.0 - ttft)
    assert service.telemetry.ledger.latency_payload()["ttft_samples"] == 1


def test_non_streaming_request_records_no_duration_at_all(tmp_path: Path) -> None:
    """The fabrication guard, at its source: with no observed first token the
    recorder must contribute zero duration, not a fraction of total latency."""
    recorder, service = _recorder(tmp_path)
    recorder.success(
        backend=_backend(),
        model="m",
        latency_s=2.0,
        prompt_tokens=10,
        completion_tokens=20,
    )
    counters = service.telemetry._session
    assert counters.requests == 1
    assert counters.total_prefill_duration == 0.0
    assert counters.total_generation_duration == 0.0
    assert counters.to_dict()["avg_prefill_tps"] is None
    assert service.telemetry.ledger.latency_payload()["ttft_samples"] == 0


def test_accounting_no_longer_scales_latency_by_a_constant() -> None:
    """Structural guard: the 0.3/0.7 split may not come back by copy-paste.

    Comment lines are exempt — the module documents the old expression so the
    correction stays legible to the next reader.
    """
    import re

    import netllm_agent.service.accounting as accounting

    source = Path(accounting.__file__).read_text(encoding="utf-8")
    code = [
        line for line in source.splitlines() if not line.lstrip().startswith(("#", "*"))
    ]
    offenders = [line for line in code if re.search(r"latency_s\s*\*\s*0\.", line)]
    assert not offenders, offenders


class _StubAdapter:
    """The five SurfaceAdapter members StreamSession._pump actually touches."""

    log_label = "chat"

    def __init__(self, service: object) -> None:
        self.service = service

    @staticmethod
    def extract_stream_usage(chunk: str) -> tuple[int, int]:
        return (0, 0)

    @staticmethod
    def restore_stream_line(plan: object, invocation: object, line: str) -> str:
        return line

    @staticmethod
    def classify_error(exc: Exception) -> bool:
        return True

    @staticmethod
    def mid_stream_error_frame(exc: Exception) -> list[str]:
        return []


@pytest.mark.asyncio
async def test_stream_session_threads_measured_ttft_into_telemetry(
    tmp_path: Path,
) -> None:
    """End to end through the real streaming loop: the recorder stamps TTFT at
    the first content frame and the ledger ends up with a real percentile and
    every dimension populated. This is the wiring the five proxy paths share."""
    import asyncio
    from types import SimpleNamespace

    from netllm_agent.service.accounting import AttemptRecorder
    from netllm_agent.service.engine import StreamSession

    service = _StubService(tmp_path / "stats.json")
    service.pool.acquire = lambda backend: None  # type: ignore[attr-defined]
    service.pool.release = lambda backend: None  # type: ignore[attr-defined]
    service._update_health_metrics = lambda: None  # type: ignore[attr-defined]
    service._source_release = lambda source_id: None  # type: ignore[attr-defined]

    plan = SimpleNamespace(
        model="gemma4:27b",
        shard=None,
        source=SimpleNamespace(id="cursor"),
        policy_key="0:local-openai",
        api_format="openai",
    )
    recorder = AttemptRecorder(service, plan)  # type: ignore[arg-type]

    async def upstream():
        await asyncio.sleep(0.01)
        yield 'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n'

    session = StreamSession(
        adapter=_StubAdapter(service),  # type: ignore[arg-type]
        plan=plan,  # type: ignore[arg-type]
        invocation=None,  # type: ignore[arg-type]
        backend=_backend(),
        recorder=recorder,
        stream=upstream(),
        # The role-only opener: present, and deliberately not a token.
        first='data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n',
        started_at=time.monotonic(),
        attempt=1,
    )
    chunks = [chunk async for chunk in session]
    assert len(chunks) == 2

    telemetry = service.telemetry
    latency = telemetry.ledger.latency_payload()
    assert latency["ttft_samples"] == 1
    # 10 ms of upstream silence before the first token, and the role frame did
    # not stop the clock.
    assert latency["ttft_p50_ms"] is not None
    assert latency["ttft_p50_ms"] >= 5.0

    windows = telemetry.ledger.windows_payload()
    assert windows["by_backend"]["alpha"]["60"] == 1
    assert windows["by_model"]["gemma4:27b"]["60"] == 1
    assert windows["by_policy"]["0:local-openai"]["60"] == 1
    assert windows["by_source"]["cursor"]["surfaces"] == {"openai": 1}
    assert telemetry._session.to_dict()["avg_prefill_tps"] is None  # no prompt tokens
