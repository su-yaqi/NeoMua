import os
import uuid
import json
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from model_gateway.auth import GatewayAuthError, verify_gateway_scope
from model_gateway.translation import (
    UnsupportedCapability,
    anthropic_to_openai,
    openai_to_anthropic_response,
    openai_chunk_to_anthropic_events,
    validate_capabilities,
)

app = FastAPI(title="NeoMua Model Gateway")


async def resolve_runtime_route(runtime_id: uuid.UUID, model_id: str, token: str) -> dict[str, Any]:
    control_url = os.getenv("CONTROL_PLANE_URL", "http://backend:8000").rstrip("/")
    internal_token = os.getenv("INTERNAL_RUNTIME_TOKEN")
    if not internal_token:
        raise HTTPException(503, {"code": "gateway_not_configured", "message": "Internal runtime token is missing"})
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            f"{control_url}/api/v1/internal/runtime/routes/{runtime_id}",
            params={"model_id": model_id},
            headers={"X-Runtime-Token": internal_token, "Authorization": f"Bearer {token}"},
        )
    if response.status_code != 200:
        raise HTTPException(response.status_code, response.json().get("detail", "Route resolution failed"))
    return response.json()


def _api_key(route: dict[str, Any]) -> str:
    secrets = route.get("secret_inputs") or {}
    for key in ("api_key", "api_token", "token"):
        if secrets.get(key):
            return secrets[key]
    raise HTTPException(409, {"code": "credential_missing", "message": "No supported API credential"})


async def _anthropic_stream(url: str, headers: dict[str, str], body: dict[str, Any]):
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("POST", url, headers=headers, json=body) as response:
            if response.status_code >= 400:
                payload = await response.aread()
                raise HTTPException(response.status_code, payload.decode(errors="replace"))
            async for chunk in response.aiter_bytes():
                yield chunk


async def _openai_stream(url: str, headers: dict[str, str], body: dict[str, Any]):
    started = False
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("POST", url, headers=headers, json=body) as response:
            if response.status_code >= 400:
                payload = await response.aread()
                raise HTTPException(response.status_code, payload.decode(errors="replace"))
            async for line in response.aiter_lines():
                if not line.startswith("data: ") or line == "data: [DONE]":
                    continue
                chunk = json.loads(line[6:])
                for event in openai_chunk_to_anthropic_events(chunk, started=started):
                    yield event.encode()
                started = True


@app.post("/v1/messages", response_model=None)
async def messages(
    body: dict[str, Any],
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
    x_runtime_id: uuid.UUID | None = Header(default=None, alias="X-Runtime-ID"),
) -> JSONResponse | StreamingResponse:
    token = x_api_key
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
    if not token or x_runtime_id is None:
        raise HTTPException(401, "Gateway credential and runtime ID required")
    model_id = str(body.get("model", ""))
    try:
        verify_gateway_scope(token, x_runtime_id, model_id)
    except GatewayAuthError as exc:
        raise HTTPException(403, str(exc))
    route = await resolve_runtime_route(x_runtime_id, model_id, token)
    provider_kind = route["provider_kind"]
    try:
        validate_capabilities(body, provider_kind)
    except UnsupportedCapability as exc:
        raise HTTPException(422, {"code": "unsupported_model_capability", "message": str(exc)})
    base_url = route["base_url"].rstrip("/")
    api_key = _api_key(route)
    if provider_kind == "anthropic":
        url = f"{base_url}/v1/messages" if not base_url.endswith("/v1") else f"{base_url}/messages"
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        if body.get("stream"):
            return StreamingResponse(_anthropic_stream(url, headers, body), media_type="text/event-stream")
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(url, headers=headers, json=body)
        return JSONResponse(response.json(), status_code=response.status_code)
    translated = anthropic_to_openai(body)
    url = f"{base_url}/chat/completions" if base_url.endswith("/v1") else f"{base_url}/v1/chat/completions"
    if translated.get("stream"):
        return StreamingResponse(
            _openai_stream(url, {"Authorization": f"Bearer {api_key}"}, translated),
            media_type="text/event-stream",
        )
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(url, headers={"Authorization": f"Bearer {api_key}"}, json=translated)
    if response.status_code >= 400:
        return JSONResponse(response.json(), status_code=response.status_code)
    return JSONResponse(openai_to_anthropic_response(response.json()))


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}
