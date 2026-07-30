import httpx
import pytest

from app.runtime.compatibility import (
    AnthropicCompatibilityError,
    check_anthropic_compatibility,
)


@pytest.mark.anyio
async def test_anthropic_compatibility_requires_message_response() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, json={"type": "message", "content": []}, request=request
        )
    )
    result = await check_anthropic_compatibility(
        "https://example.test", "secret", "claude", transport=transport
    )
    assert result["type"] == "message"


@pytest.mark.anyio
async def test_non_anthropic_response_is_rejected() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"choices": []}, request=request)
    )
    with pytest.raises(AnthropicCompatibilityError):
        await check_anthropic_compatibility(
            "https://example.test", "secret", "model", transport=transport
        )
