import pytest

from model_gateway.translation import (
    UnsupportedCapability,
    OpenAIStreamTranslator,
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
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "call-1", "content": "ok"}
                ],
            }
        ],
    }
    translated = anthropic_to_openai(request)
    assert translated["messages"][0] == {
        "role": "tool",
        "tool_call_id": "call-1",
        "content": "ok",
    }


def test_maps_openai_tool_call_to_anthropic_tool_use() -> None:
    response = openai_to_anthropic_response(
        {
            "id": "chat-1",
            "model": "model-x",
            "usage": {"prompt_tokens": 2, "completion_tokens": 3},
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": "read",
                                    "arguments": '{"path":"a"}',
                                },
                            }
                        ],
                    },
                }
            ],
        }
    )
    assert response["content"][0] == {
        "type": "tool_use",
        "id": "call-1",
        "name": "read",
        "input": {"path": "a"},
    }
    assert response["stop_reason"] == "tool_use"


def test_maps_anthropic_tool_history_and_generation_parameters() -> None:
    translated = anthropic_to_openai(
        {
            "model": "model",
            "system": "Be precise",
            "max_tokens": 100,
            "temperature": 0.2,
            "stop_sequences": ["STOP"],
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call-1",
                            "name": "read",
                            "input": {"path": "a"},
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call-1",
                            "content": "ok",
                        },
                    ],
                },
            ],
        }
    )
    assert translated["messages"][0] == {"role": "system", "content": "Be precise"}
    assert translated["messages"][1]["tool_calls"][0]["function"]["name"] == "read"
    assert translated["messages"][2]["role"] == "tool"
    assert translated["max_tokens"] == 100
    assert translated["stop"] == ["STOP"]


def test_maps_openai_text_delta_to_anthropic_sse_events() -> None:
    events = openai_chunk_to_anthropic_events(
        {
            "id": "chat-1",
            "model": "model-x",
            "choices": [{"delta": {"content": "hello"}, "finish_reason": None}],
        },
        started=False,
    )
    assert any("message_start" in event for event in events)
    assert any('"text":"hello"' in event for event in events)


def test_streamed_tool_call_has_one_start_and_matching_stop() -> None:
    translator = OpenAIStreamTranslator()
    first = translator.feed(
        {
            "id": "chat-1",
            "model": "model-x",
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call-1",
                                "function": {"name": "read", "arguments": '{"path":'},
                            }
                        ]
                    },
                    "finish_reason": None,
                }
            ],
        }
    )
    second = translator.feed(
        {
            "id": "chat-1",
            "model": "model-x",
            "choices": [
                {
                    "delta": {
                        "tool_calls": [{"index": 0, "function": {"arguments": '"a"}'}}]
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        }
    )
    combined = "".join(first + second)
    assert combined.count("content_block_start") == 2  # event name + payload type
    assert '"index":1' in combined
    assert "content_block_stop" in combined
    assert '"stop_reason":"tool_use"' in combined
