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
