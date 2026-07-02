import hashlib

import httpx

from node_runtime.model_route import DirectRoute, ModelRouteStore


class RuntimeConfigManager:
    def __init__(
        self, store: ModelRouteStore, *, client: httpx.AsyncClient | None = None
    ) -> None:
        self.store = store
        self.client = client

    async def apply(self, payload: dict) -> dict:
        if payload.get("route_mode") != "direct_anthropic":
            self.store.set_revision(int(payload["revision"]))
            return {
                "revision": int(payload["revision"]),
                "runtime_id": str(payload["runtime_id"]),
                "direct_compatibility_verified": None,
            }
        required = ("runtime_id", "base_url", "model_id", "api_key", "revision")
        if not all(payload.get(key) is not None for key in required):
            raise ValueError("direct runtime configuration is incomplete")
        owns_client = self.client is None
        client = self.client or httpx.AsyncClient(timeout=30)
        try:
            base_url = str(payload["base_url"]).rstrip("/")
            url = (
                f"{base_url}/v1/messages"
                if not base_url.endswith("/v1")
                else f"{base_url}/messages"
            )
            response = await client.post(
                url,
                headers={
                    "x-api-key": str(payload["api_key"]),
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": payload["model_id"],
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "Reply OK"}],
                },
            )
            body = (
                response.json()
                if response.headers.get("content-type", "").startswith(
                    "application/json"
                )
                else {}
            )
            if response.status_code >= 400 or body.get("type") != "message":
                raise ValueError(
                    "endpoint failed Anthropic Messages API compatibility check"
                )
            fingerprint = hashlib.sha256(
                f"{base_url}\0{payload['model_id']}\0{payload['api_key']}".encode()
            ).hexdigest()
            self.store.save(
                DirectRoute(
                    runtime_id=str(payload["runtime_id"]),
                    base_url=base_url,
                    model_id=str(payload["model_id"]),
                    api_key=str(payload["api_key"]),
                    verified=True,
                    fingerprint=fingerprint,
                )
            )
            self.store.set_revision(int(payload["revision"]))
            return {
                "revision": int(payload["revision"]),
                "runtime_id": str(payload["runtime_id"]),
                "direct_compatibility_verified": True,
                "fingerprint": fingerprint,
            }
        finally:
            if owns_client:
                await client.aclose()
