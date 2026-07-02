from typing import Any

import httpx


class AnthropicCompatibilityError(ValueError):
    pass


async def check_anthropic_compatibility(
    base_url: str,
    api_key: str,
    model_id: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    base = base_url.rstrip("/")
    url = f"{base}/messages" if base.endswith("/v1") else f"{base}/v1/messages"
    async with httpx.AsyncClient(timeout=30, transport=transport) as client:
        try:
            response = await client.post(
                url,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": model_id,
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "Reply OK"}],
                },
            )
        except httpx.HTTPError as exc:
            raise AnthropicCompatibilityError(
                f"Anthropic compatibility request failed: {exc}"
            ) from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise AnthropicCompatibilityError(
            "Endpoint did not return Anthropic JSON"
        ) from exc
    if response.status_code >= 400 or payload.get("type") != "message":
        raise AnthropicCompatibilityError(
            f"Endpoint failed Anthropic Messages API check ({response.status_code})"
        )
    return payload
