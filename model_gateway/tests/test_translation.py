import pytest

from model_gateway.translation import (
    UnsupportedCapability,
    anthropic_to_openai,
    validate_capabilities,
)


def test_rejects_thinking_for_openai_compatible() -> None:
    with pytest.raises(UnsupportedCapability, match="thinking"):
        validate_capabilities({"thinking": {"type": "enabled"}}, "openai_compatible")


def test_maps_tool_result_to_tool_message() -> None:
    request = {
        "model": "test-model",
        "messages": [{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call-1", "content": "ok"}
        ]}],
    }
    translated = anthropic_to_openai(request)
    assert translated["messages"][0] == {
        "role": "tool", "tool_call_id": "call-1", "content": "ok"
    }
