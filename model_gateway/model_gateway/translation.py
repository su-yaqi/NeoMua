import json
from typing import Any


class UnsupportedCapability(ValueError):
    pass


def validate_capabilities(request: dict[str, Any], provider_kind: str) -> None:
    if provider_kind == "openai_compatible" and request.get("thinking"):
        raise UnsupportedCapability("thinking is not supported by this provider")
    if provider_kind == "openai_compatible" and request.get("service_tier"):
        raise UnsupportedCapability("service_tier is not supported by this provider")


def anthropic_to_openai(request: dict[str, Any]) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    for message in request.get("messages", []):
        content = message.get("content")
        if isinstance(content, str):
            messages.append({"role": message["role"], "content": content})
            continue
        text_parts: list[str] = []
        for block in content or []:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_result":
                messages.append({"role": "tool", "tool_call_id": block["tool_use_id"], "content": block.get("content", "")})
            else:
                raise UnsupportedCapability(f"unsupported content block: {block.get('type')}")
        if text_parts:
            messages.append({"role": message["role"], "content": "\n".join(text_parts)})
    result = {"model": request["model"], "messages": messages, "stream": request.get("stream", False)}
    if request.get("tools"):
        result["tools"] = [{"type": "function", "function": {"name": tool["name"], "description": tool.get("description", ""), "parameters": tool.get("input_schema", {})}} for tool in request["tools"]]
    return result


def openai_to_anthropic_response(response: dict[str, Any]) -> dict[str, Any]:
    choice = response["choices"][0]
    message = choice["message"]
    content: list[dict[str, Any]] = []
    if message.get("content"):
        content.append({"type": "text", "text": message["content"]})
    for call in message.get("tool_calls") or []:
        function = call["function"]
        content.append({
            "type": "tool_use", "id": call["id"], "name": function["name"],
            "input": json.loads(function.get("arguments") or "{}"),
        })
    finish = choice.get("finish_reason")
    stop_reason = {"tool_calls": "tool_use", "length": "max_tokens", "stop": "end_turn"}.get(finish, "end_turn")
    usage = response.get("usage") or {}
    return {
        "id": response.get("id", "gateway-message"), "type": "message", "role": "assistant",
        "model": response.get("model", "unknown"), "content": content,
        "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": usage.get("prompt_tokens", 0), "output_tokens": usage.get("completion_tokens", 0)},
    }


def _sse(event_type: str, data: dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def openai_chunk_to_anthropic_events(
    chunk: dict[str, Any], *, started: bool
) -> list[str]:
    events: list[str] = []
    if not started:
        events.extend([
            _sse("message_start", {"type": "message_start", "message": {
                "id": chunk.get("id", "gateway-message"), "type": "message",
                "role": "assistant", "model": chunk.get("model", "unknown"),
                "content": [], "stop_reason": None, "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            }}),
            _sse("content_block_start", {"type": "content_block_start", "index": 0,
                "content_block": {"type": "text", "text": ""}}),
        ])
    choice = (chunk.get("choices") or [{}])[0]
    delta = choice.get("delta") or {}
    if delta.get("content"):
        events.append(_sse("content_block_delta", {"type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": delta["content"]}}))
    for call in delta.get("tool_calls") or []:
        index = int(call.get("index", 0)) + 1
        function = call.get("function") or {}
        if call.get("id"):
            events.append(_sse("content_block_start", {"type": "content_block_start", "index": index,
                "content_block": {"type": "tool_use", "id": call["id"], "name": function.get("name", "tool"), "input": {}}}))
        if function.get("arguments"):
            events.append(_sse("content_block_delta", {"type": "content_block_delta", "index": index,
                "delta": {"type": "input_json_delta", "partial_json": function["arguments"]}}))
    finish = choice.get("finish_reason")
    if finish:
        events.extend([
            _sse("content_block_stop", {"type": "content_block_stop", "index": 0}),
            _sse("message_delta", {"type": "message_delta", "delta": {
                "stop_reason": {"tool_calls": "tool_use", "length": "max_tokens"}.get(finish, "end_turn"),
                "stop_sequence": None}, "usage": {"output_tokens": 0}}),
            _sse("message_stop", {"type": "message_stop"}),
        ])
    return events
