from dataclasses import asdict, is_dataclass
from typing import Any


def _value(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, list):
        return [_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _value(item) for key, item in value.items()}
    if hasattr(value, "__dict__"):
        return {key: _value(item) for key, item in vars(value).items() if not key.startswith("_")}
    return value


def normalize_message(message: Any, sequence: int) -> dict[str, Any]:
    if hasattr(message, "result"):
        event_type = "result"
        payload = {"session_id": getattr(message, "session_id", None), "result": message.result}
    elif hasattr(message, "content"):
        event_type = "assistant_message"
        payload = {"content": _value(message.content)}
    else:
        event_type = "status"
        payload = _value(message)
    return {"sequence": sequence, "event_type": event_type, "payload": payload}


def normalize_messages(message: Any, start_sequence: int) -> list[dict[str, Any]]:
    if hasattr(message, "result"):
        return [normalize_message(message, start_sequence)]
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return [normalize_message(message, start_sequence)]
    events: list[dict[str, Any]] = []
    for block in content:
        value = _value(block)
        block_type = getattr(block, "type", None)
        if block_type is None and isinstance(value, dict):
            block_type = value.get("type")
        if block_type == "tool_use":
            payload = {
                "id": getattr(block, "id", value.get("id")),
                "name": getattr(block, "name", value.get("name")),
                "input": getattr(block, "input", value.get("input", {})),
            }
            event_type = "tool_call"
        elif block_type == "tool_result":
            payload = {
                "tool_use_id": getattr(
                    block, "tool_use_id", value.get("tool_use_id")
                ),
                "content": getattr(block, "content", value.get("content")),
                "is_error": getattr(block, "is_error", value.get("is_error", False)),
            }
            event_type = "tool_result"
        elif block_type == "text":
            payload = {"text": getattr(block, "text", value.get("text", ""))}
            event_type = "assistant_message"
        elif block_type == "thinking":
            payload = {
                "kind": "thinking",
                "thinking": getattr(block, "thinking", value.get("thinking", "")),
            }
            event_type = "assistant_message"
        else:
            payload = {"content": value}
            event_type = "assistant_message"
        events.append(
            {
                "sequence": start_sequence + len(events),
                "event_type": event_type,
                "payload": payload,
            }
        )
    return events or [normalize_message(message, start_sequence)]
