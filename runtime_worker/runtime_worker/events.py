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
