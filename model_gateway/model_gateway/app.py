import os
import uuid
import json
import logging
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from model_gateway.auth import GatewayAuthError, verify_gateway_scope
from model_gateway.translation import (
    UnsupportedCapability,
    InvalidUpstreamResponse,
    OpenAIStreamTranslator,
    anthropic_to_openai,
    openai_to_anthropic_response,
    validate_capabilities,
)

app = FastAPI(title="NeoMua Model Gateway")
logger = logging.getLogger(__name__)


async def resolve_runtime_route(
    runtime_id: uuid.UUID, task_id: uuid.UUID, model_id: str, token: str
) -> dict[str, Any]:
    control_url = os.getenv("CONTROL_PLANE_URL", "http://backend:8000").rstrip("/")
    internal_token = os.getenv("INTERNAL_RUNTIME_TOKEN")
    if not internal_token:
        raise HTTPException(
            503,
            {
                "code": "gateway_not_configured",
                "message": "Internal runtime token is missing",
            },
        )
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            f"{control_url}/api/v1/internal/runtime/routes/{runtime_id}/tasks/{task_id}",
            params={"model_id": model_id},
            headers={
                "X-Runtime-Token": internal_token,
                "Authorization": f"Bearer {token}",
            },
        )
    if response.status_code != 200:
        raise HTTPException(
            403 if response.status_code in {401, 403, 404} else 503,
            {
                "code": "route_resolution_failed",
                "message": "The task route is unavailable",
            },
        )
    return response.json()


def _api_key(route: dict[str, Any]) -> str:
    secrets = route.get("secret_inputs") or {}
    for key in ("api_key", "api_token", "token"):
        if secrets.get(key):
            return secrets[key]
    raise HTTPException(
        409, {"code": "credential_missing", "message": "No supported API credential"}
    )


async def _anthropic_stream(url: str, headers: dict[str, str], body: dict[str, Any]):
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("POST", url, headers=headers, json=body) as response:
            if response.status_code >= 400:
                await response.aread()
                logger.warning(
                    "Anthropic upstream rejected a streaming request",
                    extra={"status_code": response.status_code},
                )
                raise HTTPException(
                    502,
                    {
                        "code": "upstream_request_failed",
                        "message": "The model provider rejected the request",
                    },
                )
            async for chunk in response.aiter_bytes():
                yield chunk


async def _openai_stream(url: str, headers: dict[str, str], body: dict[str, Any]):
    translator = OpenAIStreamTranslator()
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("POST", url, headers=headers, json=body) as response:
            if response.status_code >= 400:
                await response.aread()
                logger.warning(
                    "OpenAI-compatible upstream rejected a streaming request",
                    extra={"status_code": response.status_code},
                )
                raise HTTPException(
                    502,
                    {
                        "code": "upstream_request_failed",
                        "message": "The model provider rejected the request",
                    },
                )
            async for line in response.aiter_lines():
                if not line.startswith("data: ") or line == "data: [DONE]":
                    continue
                try:
                    chunk = json.loads(line[6:])
                    for event in translator.feed(chunk):
                        yield event.encode()
                except (json.JSONDecodeError, UnsupportedCapability) as exc:
                    logger.warning("Invalid streaming response from model provider")
                    raise HTTPException(
                        502,
                        {
                            "code": "invalid_upstream_response",
                            "message": "The model provider returned an invalid response",
                        },
                    ) from exc


@app.post("/tasks/{task_id}/v1/messages", response_model=None)
async def messages(
    task_id: uuid.UUID,
    body: dict[str, Any],
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
    x_runtime_id: uuid.UUID | None = Header(default=None, alias="X-Runtime-ID"),
) -> JSONResponse | StreamingResponse:
    token = x_api_key
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
    if not token:
        raise HTTPException(401, "Gateway credential required")
    model_id = str(body.get("model", ""))
    try:
        claims = verify_gateway_scope(token, x_runtime_id, task_id, model_id)
        runtime_id = uuid.UUID(claims["runtime_id"])
    except (GatewayAuthError, KeyError, ValueError) as exc:
        raise HTTPException(403, str(exc))
    route = await resolve_runtime_route(runtime_id, task_id, model_id, token)
    provider_kind = route["provider_kind"]
    try:
        validate_capabilities(body, provider_kind)
        translated = anthropic_to_openai(body) if provider_kind != "anthropic" else None
    except UnsupportedCapability as exc:
        raise HTTPException(
            422, {"code": "unsupported_model_capability", "message": str(exc)}
        )
    base_url = route["base_url"].rstrip("/")
    api_key = _api_key(route)
    if provider_kind == "anthropic":
        url = (
            f"{base_url}/v1/messages"
            if not base_url.endswith("/v1")
            else f"{base_url}/messages"
        )
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        if body.get("stream"):
            return StreamingResponse(
                _anthropic_stream(url, headers, body), media_type="text/event-stream"
            )
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(url, headers=headers, json=body)
        if response.status_code >= 400:
            logger.warning(
                "Anthropic upstream rejected a request",
                extra={"status_code": response.status_code},
            )
            return JSONResponse(
                {
                    "code": "upstream_request_failed",
                    "message": "The model provider rejected the request",
                },
                status_code=502,
            )
        try:
            payload = response.json()
        except ValueError:
            return JSONResponse(
                {
                    "code": "invalid_upstream_response",
                    "message": "The model provider returned an invalid response",
                },
                status_code=502,
            )
        return JSONResponse(payload)
    assert translated is not None
    url = (
        f"{base_url}/chat/completions"
        if base_url.endswith("/v1")
        else f"{base_url}/v1/chat/completions"
    )
    if translated.get("stream"):
        return StreamingResponse(
            _openai_stream(url, {"Authorization": f"Bearer {api_key}"}, translated),
            media_type="text/event-stream",
        )
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            url, headers={"Authorization": f"Bearer {api_key}"}, json=translated
        )
    if response.status_code >= 400:
        logger.warning(
            "OpenAI-compatible upstream rejected a request",
            extra={"status_code": response.status_code},
        )
        return JSONResponse(
            {
                "code": "upstream_request_failed",
                "message": "The model provider rejected the request",
            },
            status_code=502,
        )
    try:
        return JSONResponse(openai_to_anthropic_response(response.json()))
    except (ValueError, InvalidUpstreamResponse):
        logger.warning("Invalid non-streaming response from model provider")
        return JSONResponse(
            {
                "code": "invalid_upstream_response",
                "message": "The model provider returned an invalid response",
            },
            status_code=502,
        )


@app.get("/health/live")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/health")
def legacy_health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/health/ready")
async def readiness() -> dict[str, bool]:
    control_url = os.getenv("CONTROL_PLANE_URL", "http://backend:8000").rstrip("/")
    internal_token = os.getenv("INTERNAL_RUNTIME_TOKEN")
    signing_key = os.getenv("GATEWAY_SIGNING_KEY")
    if not internal_token or not signing_key:
        raise HTTPException(503, {"code": "gateway_not_configured"})
    async with httpx.AsyncClient(timeout=5) as client:
        response = await client.get(
            f"{control_url}/api/v1/internal/runtime/signing-probe",
            headers={"X-Runtime-Token": internal_token},
        )
    if response.status_code != 200:
        raise HTTPException(503, {"code": "control_plane_unready"})
    try:
        import jwt

        jwt.decode(
            response.json()["token"],
            signing_key,
            algorithms=["HS256"],
            audience="neomua-model-gateway-readiness",
            options={"require": ["exp", "iat", "aud"]},
        )
    except (KeyError, ValueError, jwt.PyJWTError) as exc:
        raise HTTPException(503, {"code": "gateway_signing_key_mismatch"}) from exc
    return {"ok": True}
