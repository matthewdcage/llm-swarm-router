"""Model alias routing for mixed-provider fleets."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from netllm_agent.app import create_app
from netllm_core.models import Backend, BackendHealth, NetllmConfig
from netllm_core.pool import RouterPool

_MOCK_ONLINE = {"status": "online", "models": ["whatever"], "model_count": 1}

ALIASES = {"llama3": ["llama3:8b-instruct-q4_K_M", "Meta-Llama-3-8B-Instruct-GGUF"]}


def _backend(bid: str, url: str, models: list[str], *, local: bool = True) -> Backend:
    return Backend(
        id=bid,
        base_url=url,
        local=local,
        health=BackendHealth(status="online", models=models),
    )


@patch("netllm_core.pool.probe_openai_compat_sync", return_value=_MOCK_ONLINE)
def test_backends_for_model_matches_aliases(_mock: object) -> None:
    pool = RouterPool(model_aliases=ALIASES)
    ollama = _backend("ollama", "http://a/v1", ["llama3:8b-instruct-q4_K_M"])
    lmstudio = _backend("lmstudio", "http://b/v1", ["Meta-Llama-3-8B-Instruct-GGUF"])
    other = _backend("other", "http://c/v1", ["qwen2"])
    pool.set_backends([ollama, lmstudio, other])
    matched = pool.backends_for_model("llama3")
    assert {b.id for b in matched} == {"ollama", "lmstudio"}


@patch("netllm_core.pool.probe_openai_compat_sync", return_value=_MOCK_ONLINE)
def test_backends_for_model_no_match_returns_empty(_mock: object) -> None:
    """No more spraying all backends when the model is unknown."""
    pool = RouterPool()
    pool.set_backends([_backend("a", "http://a/v1", ["qwen2"])])
    assert pool.backends_for_model("does-not-exist") == []


@patch(
    "netllm_core.pool.probe_openai_compat_sync",
    return_value={"status": "online", "models": [], "model_count": 0},
)
def test_backends_with_unknown_catalog_stay_candidates(_mock: object) -> None:
    pool = RouterPool()
    blank = Backend(
        id="cloud",
        base_url="http://cloud/v1",
        local=False,
        health=BackendHealth(status="online", models=[]),
    )
    pool.set_backends([blank])
    matched = pool.backends_for_model("anything")
    assert [b.id for b in matched] == ["cloud"]


def test_model_for_backend_resolves_alias() -> None:
    from netllm_agent.service import AgentService

    cfg = NetllmConfig()
    cfg.routing.model_aliases = ALIASES
    service = AgentService(cfg)
    ollama = _backend("ollama", "http://a/v1", ["llama3:8b-instruct-q4_K_M"])
    served_direct = _backend("direct", "http://b/v1", ["llama3"])
    unknown = _backend("blank", "http://c/v1", [])
    assert service._model_for_backend("llama3", ollama) == "llama3:8b-instruct-q4_K_M"
    assert service._model_for_backend("llama3", served_direct) == "llama3"
    assert service._model_for_backend("llama3", unknown) == "llama3"


def test_model_for_backend_exact_alias_beats_prefix_match() -> None:
    """A backend serving several tags of one base must resolve to the
    exact alias from config, not whichever tag prefix-matches first."""
    from netllm_agent.service import AgentService

    cfg = NetllmConfig()
    cfg.routing.model_aliases = ALIASES
    service = AgentService(cfg)
    multi_tag = _backend(
        "ollama",
        "http://a/v1",
        ["llama3:70b", "llama3:8b-instruct-q4_K_M"],
    )
    assert (
        service._model_for_backend("llama3", multi_tag) == "llama3:8b-instruct-q4_K_M"
    )


@patch("netllm_agent.service.scan_local_providers", new_callable=AsyncMock)
@patch("netllm_core.pool.probe_openai_compat_sync")
@patch("netllm_sdk_openai.client.AsyncOpenAI")
def test_canonical_request_rewrites_payload_and_response(
    mock_openai_cls: object,
    mock_probe: object,
    mock_scan: AsyncMock,
) -> None:
    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    cfg.routing.model_aliases = ALIASES

    mock_scan.return_value = [
        {
            "id": "ollama",
            "status": "online",
            "base_url": "http://127.0.0.1:11434/v1",
            "model_count": 1,
            "models": ["llama3:8b-instruct-q4_K_M"],
        }
    ]
    mock_probe.return_value = {
        "status": "online",
        "models": ["llama3:8b-instruct-q4_K_M"],
        "model_count": 1,
    }

    sent_models: list[str] = []
    mock_client = MagicMock()

    def make_client(*_args: object, **_kwargs: object) -> MagicMock:
        return mock_client

    mock_openai_cls.side_effect = make_client

    async def fake_create(**kwargs: object) -> MagicMock:
        sent_models.append(str(kwargs.get("model")))
        mock_response = MagicMock()
        mock_response.model_dump.return_value = {
            "id": "cmpl-1",
            "object": "chat.completion",
            "model": kwargs.get("model"),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        return mock_response

    mock_client.chat.completions.create = fake_create

    with TestClient(create_app(cfg)) as client:
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "llama3",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert resp.status_code == 200
    assert sent_models == ["llama3:8b-instruct-q4_K_M"]
    assert resp.json()["model"] == "llama3"


@patch("netllm_agent.service.scan_local_providers", new_callable=AsyncMock)
@patch("netllm_core.pool.probe_openai_compat_sync")
def test_unknown_model_returns_404_with_catalog(
    mock_probe: object,
    mock_scan: AsyncMock,
) -> None:
    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False

    mock_scan.return_value = [
        {
            "id": "ollama",
            "status": "online",
            "base_url": "http://127.0.0.1:11434/v1",
            "model_count": 1,
            "models": ["qwen2"],
        }
    ]
    mock_probe.return_value = {
        "status": "online",
        "models": ["qwen2"],
        "model_count": 1,
    }

    with TestClient(create_app(cfg)) as client:
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-imaginary",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert "gpt-imaginary" in detail
    assert "qwen2" in detail


@patch("netllm_agent.service.scan_local_providers", new_callable=AsyncMock)
@patch("netllm_core.pool.probe_openai_compat_sync")
def test_models_listing_includes_canonical_alias(
    mock_probe: object,
    mock_scan: AsyncMock,
) -> None:
    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    cfg.routing.model_aliases = ALIASES

    mock_scan.return_value = [
        {
            "id": "ollama",
            "status": "online",
            "base_url": "http://127.0.0.1:11434/v1",
            "model_count": 1,
            "models": ["llama3:8b-instruct-q4_K_M"],
        }
    ]
    mock_probe.return_value = {
        "status": "online",
        "models": ["llama3:8b-instruct-q4_K_M"],
        "model_count": 1,
    }

    with TestClient(create_app(cfg)) as client:
        data = client.get("/v1/models").json()
    ids = {m["id"] for m in data["data"]}
    assert "llama3:8b-instruct-q4_K_M" in ids
    assert "llama3" in ids


async def _gen(chunks: list[str]):
    for c in chunks:
        yield c


def test_restore_stream_model_rewrites_chunks() -> None:
    import asyncio
    import json

    from netllm_agent.service import AgentService

    chunk = (
        'data: {"id":"c1","object":"chat.completion.chunk",'
        '"model":"llama3:8b-instruct-q4_K_M","choices":[]}\n\n'
    )

    async def run() -> list[str]:
        out = []
        async for c in AgentService._restore_stream_model(
            _gen([chunk, "data: [DONE]\n\n"]), "llama3"
        ):
            out.append(c)
        return out

    out = asyncio.run(run())
    assert json.loads(out[0].split("\n")[0][len("data: ") :])["model"] == "llama3"
    assert out[1] == "data: [DONE]\n\n"


def test_restore_stream_model_handles_multi_line_chunks() -> None:
    import asyncio
    import json

    from netllm_agent.service import AgentService

    multi = (
        'data: {"id":"c1","model":"llama3:8b-instruct-q4_K_M","choices":[]}\n\n'
        'data: {"id":"c2","model":"llama3:8b-instruct-q4_K_M","choices":[]}\n\n'
    )

    async def run() -> list[str]:
        out = []
        async for c in AgentService._restore_stream_model(_gen([multi]), "llama3"):
            out.append(c)
        return out

    out = asyncio.run(run())
    data_lines = [ln for ln in out[0].split("\n") if ln.startswith("data: ")]
    assert len(data_lines) == 2
    for ln in data_lines:
        assert json.loads(ln[len("data: ") :])["model"] == "llama3"
