import httpx
import pytest

from node_runtime.model_route import ModelRouteStore
from node_runtime.runtime_config import RuntimeConfigManager


@pytest.mark.anyio
async def test_direct_config_is_saved_only_after_anthropic_protocol_success(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/messages"
        assert request.headers["x-api-key"] == "secret"
        return httpx.Response(200, json={"type": "message", "content": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        store = ModelRouteStore(tmp_path / "routes.json")
        manager = RuntimeConfigManager(store, client=client)
        result = await manager.apply({
            "revision": 2, "runtime_id": "runtime-1", "route_mode": "direct_anthropic",
            "base_url": "https://anthropic.example", "model_id": "claude-a",
            "api_key": "secret",
        })
    assert result["direct_compatibility_verified"] is True
    assert store.get("runtime-1").verified is True


@pytest.mark.anyio
async def test_failed_protocol_check_does_not_save_secret(tmp_path) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(404))
    ) as client:
        store = ModelRouteStore(tmp_path / "routes.json")
        manager = RuntimeConfigManager(store, client=client)
        with pytest.raises(ValueError, match="compatibility"):
            await manager.apply({
                "revision": 2, "runtime_id": "runtime-1", "route_mode": "direct_anthropic",
                "base_url": "https://not-compatible.example", "model_id": "model",
                "api_key": "secret",
            })
    assert store.get("runtime-1") is None
