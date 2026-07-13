from dataclasses import fields, is_dataclass
from typing import Any

MAX_SERIALIZATION_DEPTH = 20
MAX_SERIALIZATION_NODES = 10_000
MAX_FALLBACK_LENGTH = 1_024


class _SerializationBudget:
    def __init__(self, max_nodes: int) -> None:
        self.remaining = max_nodes
        self.visited: set[int] = set()


def _safe_string(value: Any) -> str:
    try:
        rendered = str(value)
    except Exception:
        rendered = f"<{type(value).__name__}: unprintable>"
    if len(rendered) > MAX_FALLBACK_LENGTH:
        return f"{rendered[:MAX_FALLBACK_LENGTH]}...[truncated]"
    return rendered


def _value(
    value: Any,
    *,
    _depth: int = 0,
    _budget: _SerializationBudget | None = None,
) -> Any:
    """Convert an SDK object graph to a bounded JSON-compatible value."""
    budget = _budget or _SerializationBudget(MAX_SERIALIZATION_NODES)
    budget.remaining -= 1
    if budget.remaining < 0:
        return "[NODE_LIMIT_EXCEEDED]"
    if _depth > MAX_SERIALIZATION_DEPTH:
        return "[DEPTH_LIMIT_EXCEEDED]"
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        return _safe_string(value)

    identity = id(value)
    if identity in budget.visited:
        return "[CIRCULAR_REFERENCE]"
    budget.visited.add(identity)
    try:
        if is_dataclass(value) and not isinstance(value, type):
            return {
                field.name: _value(
                    getattr(value, field.name),
                    _depth=_depth + 1,
                    _budget=budget,
                )
                for field in fields(value)
            }
        if isinstance(value, (list, tuple, set, frozenset)):
            return [_value(item, _depth=_depth + 1, _budget=budget) for item in value]
        if isinstance(value, dict):
            return {
                _safe_string(key): _value(item, _depth=_depth + 1, _budget=budget)
                for key, item in value.items()
            }
        if hasattr(value, "__dict__"):
            return {
                key: _value(item, _depth=_depth + 1, _budget=budget)
                for key, item in vars(value).items()
                if not key.startswith("_")
            }
        return _safe_string(value)
    finally:
        budget.visited.discard(identity)


def normalize_message(message: Any, sequence: int) -> dict[str, Any]:
    if hasattr(message, "result"):
        event_type = "result"
        payload = {
            "session_id": getattr(message, "session_id", None),
            "result": message.result,
        }
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
        value_dict = value if isinstance(value, dict) else {}
        block_type = getattr(block, "type", None)
        if block_type is None and isinstance(value, dict):
            block_type = value.get("type")
        if block_type == "tool_use":
            payload = {
                "id": getattr(block, "id", value_dict.get("id")),
                "name": getattr(block, "name", value_dict.get("name")),
                "input": _value(getattr(block, "input", value_dict.get("input", {}))),
            }
            event_type = "tool_call"
        elif block_type == "tool_result":
            payload = {
                "tool_use_id": getattr(
                    block, "tool_use_id", value_dict.get("tool_use_id")
                ),
                "content": _value(getattr(block, "content", value_dict.get("content"))),
                "is_error": getattr(
                    block, "is_error", value_dict.get("is_error", False)
                ),
            }
            event_type = "tool_result"
        elif block_type == "text":
            payload = {"text": getattr(block, "text", value_dict.get("text", ""))}
            event_type = "assistant_message"
        elif block_type == "thinking":
            payload = {
                "kind": "thinking",
                "thinking": getattr(block, "thinking", value_dict.get("thinking", "")),
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
