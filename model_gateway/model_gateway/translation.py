import json
from typing import Any


class UnsupportedCapability(ValueError):
    pass


class InvalidUpstreamResponse(ValueError):
    pass


def validate_capabilities(request: dict[str, Any], provider_kind: str) -> None:
    if provider_kind != "openai_compatible":
        return
    unsupported = {
        "thinking",
        "service_tier",
        "mcp_servers",
        "container",
        "context_management",
    }
    for field in unsupported:
        if request.get(field) is not None:
            raise UnsupportedCapability(f"{field} is not supported by this provider")
    for tool in request.get("tools") or []:
        if tool.get("type") not in {None, "custom"}:
            raise UnsupportedCapability(
                f"server tool type {tool.get('type')} is not supported by this provider"
            )


def anthropic_to_openai(request: dict[str, Any]) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    system = request.get("system")
    if isinstance(system, str):
        messages.append({"role": "system", "content": system})
    elif isinstance(system, list):
        if any(
            block.get("type") != "text" or block.get("cache_control")
            for block in system
        ):
            raise UnsupportedCapability(
                "system blocks cannot be converted without loss"
            )
        messages.append(
            {
                "role": "system",
                "content": "\n".join(block.get("text", "") for block in system),
            }
        )
    for message in request.get("messages", []):
        content = message.get("content")
        if isinstance(content, str):
            messages.append({"role": message["role"], "content": content})
            continue
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []
        for block in content or []:
            if block.get("cache_control"):
                raise UnsupportedCapability(
                    "cache_control cannot be converted without loss"
                )
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_result":
                result_content = block.get("content", "")
                if isinstance(result_content, list):
                    if any(item.get("type") != "text" for item in result_content):
                        raise UnsupportedCapability(
                            "non-text tool results are not supported"
                        )
                    result_content = "\n".join(
                        item.get("text", "") for item in result_content
                    )
                tool_results.append(
                    {
                        "role": "tool",
                        "tool_call_id": block["tool_use_id"],
                        "content": result_content,
                    }
                )
            elif block.get("type") == "tool_use":
                tool_calls.append(
                    {
                        "id": block["id"],
                        "type": "function",
                        "function": {
                            "name": block["name"],
                            "arguments": json.dumps(
                                block.get("input", {}), separators=(",", ":")
                            ),
                        },
                    }
                )
            else:
                raise UnsupportedCapability(
                    f"unsupported content block: {block.get('type')}"
                )
        if tool_calls:
            if message["role"] != "assistant":
                raise UnsupportedCapability("tool_use block must have assistant role")
            messages.append(
                {
                    "role": "assistant",
                    "content": "\n".join(text_parts) or None,
                    "tool_calls": tool_calls,
                }
            )
        elif text_parts:
            messages.append({"role": message["role"], "content": "\n".join(text_parts)})
        messages.extend(tool_results)
    result = {
        "model": request["model"],
        "messages": messages,
        "stream": request.get("stream", False),
    }
    for source, target in (
        ("max_tokens", "max_tokens"),
        ("temperature", "temperature"),
        ("top_p", "top_p"),
        ("stop_sequences", "stop"),
    ):
        if request.get(source) is not None:
            result[target] = request[source]
    if request.get("tools"):
        result["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {}),
                },
            }
            for tool in request["tools"]
        ]
    tool_choice = request.get("tool_choice")
    if tool_choice:
        choice_type = tool_choice.get("type")
        if choice_type in {"auto", "any", "none"}:
            result["tool_choice"] = {"any": "required"}.get(choice_type, choice_type)
        elif choice_type == "tool":
            result["tool_choice"] = {
                "type": "function",
                "function": {"name": tool_choice["name"]},
            }
        else:
            raise UnsupportedCapability("unsupported tool_choice")
    return result


def openai_to_anthropic_response(response: dict[str, Any]) -> dict[str, Any]:
    try:
        choices = response["choices"]
        choice = choices[0]
        message = choice["message"]
        if not isinstance(choices, list) or not isinstance(choice, dict):
            raise TypeError
        if not isinstance(message, dict):
            raise TypeError
    except (KeyError, IndexError, TypeError) as exc:
        raise InvalidUpstreamResponse(
            "OpenAI-compatible response has no valid first choice"
        ) from exc
    content: list[dict[str, Any]] = []
    if message.get("content"):
        content.append({"type": "text", "text": message["content"]})
    for call in message.get("tool_calls") or []:
        try:
            function = call["function"]
            call_id = call["id"]
            function_name = function["name"]
            arguments = json.loads(function.get("arguments") or "{}")
            if not isinstance(arguments, dict):
                raise TypeError
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise InvalidUpstreamResponse(
                "OpenAI-compatible tool call is malformed"
            ) from exc
        content.append(
            {
                "type": "tool_use",
                "id": call_id,
                "name": function_name,
                "input": arguments,
            }
        )
    finish = choice.get("finish_reason")
    stop_reason = {
        "tool_calls": "tool_use",
        "length": "max_tokens",
        "stop": "end_turn",
    }.get(finish, "end_turn")
    usage = response.get("usage") or {}
    return {
        "id": response.get("id", "gateway-message"),
        "type": "message",
        "role": "assistant",
        "model": response.get("model", "unknown"),
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


def _sse(event_type: str, data: dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def openai_chunk_to_anthropic_events(
    chunk: dict[str, Any], *, started: bool
) -> list[str]:
    translator = OpenAIStreamTranslator()
    translator.started = started
    return translator.feed(chunk)


class OpenAIStreamTranslator:
    def __init__(self) -> None:
        self.started = False
        self.text_open = False
        self.tool_blocks: set[int] = set()

    def feed(self, chunk: dict[str, Any]) -> list[str]:
        events: list[str] = []
        if not self.started:
            events.append(
                _sse(
                    "message_start",
                    {
                        "type": "message_start",
                        "message": {
                            "id": chunk.get("id", "gateway-message"),
                            "type": "message",
                            "role": "assistant",
                            "model": chunk.get("model", "unknown"),
                            "content": [],
                            "stop_reason": None,
                            "stop_sequence": None,
                            "usage": {"input_tokens": 0, "output_tokens": 0},
                        },
                    },
                )
            )
            self.started = True
        choice = (chunk.get("choices") or [{}])[0]
        delta = choice.get("delta") or {}
        if delta.get("content"):
            if not self.text_open:
                events.append(
                    _sse(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": 0,
                            "content_block": {"type": "text", "text": ""},
                        },
                    )
                )
                self.text_open = True
            events.append(
                _sse(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": delta["content"]},
                    },
                )
            )
        for call in delta.get("tool_calls") or []:
            index = int(call.get("index", 0)) + 1
            function = call.get("function") or {}
            if index not in self.tool_blocks:
                if not call.get("id") or not function.get("name"):
                    raise UnsupportedCapability(
                        "tool stream started without id and function name"
                    )
                events.append(
                    _sse(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": index,
                            "content_block": {
                                "type": "tool_use",
                                "id": call["id"],
                                "name": function["name"],
                                "input": {},
                            },
                        },
                    )
                )
                self.tool_blocks.add(index)
            if function.get("arguments"):
                events.append(
                    _sse(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": index,
                            "delta": {
                                "type": "input_json_delta",
                                "partial_json": function["arguments"],
                            },
                        },
                    )
                )
        finish = choice.get("finish_reason")
        if finish:
            if self.text_open:
                events.append(
                    _sse(
                        "content_block_stop",
                        {
                            "type": "content_block_stop",
                            "index": 0,
                        },
                    )
                )
            for index in sorted(self.tool_blocks):
                events.append(
                    _sse(
                        "content_block_stop",
                        {
                            "type": "content_block_stop",
                            "index": index,
                        },
                    )
                )
            usage = chunk.get("usage") or {}
            events.extend(
                [
                    _sse(
                        "message_delta",
                        {
                            "type": "message_delta",
                            "delta": {
                                "stop_reason": {
                                    "tool_calls": "tool_use",
                                    "length": "max_tokens",
                                }.get(finish, "end_turn"),
                                "stop_sequence": None,
                            },
                            "usage": {
                                "output_tokens": usage.get("completion_tokens", 0)
                            },
                        },
                    ),
                    _sse("message_stop", {"type": "message_stop"}),
                ]
            )
        return events
