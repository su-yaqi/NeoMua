import pytest

from model_gateway.translation import (
    UnsupportedCapability,
    anthropic_to_openai,
    openai_to_anthropic_response,
    openai_chunk_to_anthropic_events,
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


def test_maps_openai_tool_call_to_anthropic_tool_use() -> None:
    response = openai_to_anthropic_response({
        "id": "chat-1", "model": "model-x", "usage": {"prompt_tokens": 2, "completion_tokens": 3},
        "choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
            {"id": "call-1", "type": "function", "function": {"name": "read", "arguments": "{\"path\":\"a\"}"}}
        ]}}],
    })
    assert response["content"][0] == {
        "type": "tool_use", "id": "call-1", "name": "read", "input": {"path": "a"}
    }
    assert response["stop_reason"] == "tool_use"


def test_maps_openai_text_delta_to_anthropic_sse_events() -> None:
    events = openai_chunk_to_anthropic_events({
        "id": "chat-1", "model": "model-x",
        "choices": [{"delta": {"content": "hello"}, "finish_reason": None}],
    }, started=False)
    assert any("message_start" in event for event in events)
    assert any('"text":"hello"' in event for event in events)
