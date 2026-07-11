from dataclasses import dataclass

from runtime_worker.events import normalize_message, normalize_messages


class ResultMessage:
    session_id = "session-1"
    result = "done"


class AssistantMessage:
    content = [{"type": "text", "text": "hello"}]


def test_normalize_result_message() -> None:
    event = normalize_message(ResultMessage(), sequence=4)
    assert event == {
        "sequence": 4,
        "event_type": "result",
        "payload": {"session_id": "session-1", "result": "done"},
    }


def test_normalize_assistant_message() -> None:
    event = normalize_message(AssistantMessage(), sequence=2)
    assert event["event_type"] == "assistant_message"
    assert event["payload"]["content"][0]["text"] == "hello"


class ToolBlock:
    type = "tool_use"
    id = "tool-1"
    name = "Read"
    input = {"file_path": "/workspace/README.md"}


class ToolMessage:
    content = [ToolBlock()]


def test_normalize_tool_call_as_distinct_event() -> None:
    events = normalize_messages(ToolMessage(), start_sequence=3)
    assert events == [
        {
            "sequence": 3,
            "event_type": "tool_call",
            "payload": {
                "id": "tool-1",
                "name": "Read",
                "input": {"file_path": "/workspace/README.md"},
            },
        }
    ]


def test_normalize_message_bounds_cycles_and_depth() -> None:
    cycle: list[object] = []
    cycle.append(cycle)

    class CyclicMessage:
        content = cycle

    event = normalize_message(CyclicMessage(), sequence=1)
    assert event["payload"]["content"] == ["[CIRCULAR_REFERENCE]"]

    @dataclass
    class Link:
        child: object | None = None

    root = Link()
    current = root
    for _ in range(30):
        child = Link()
        current.child = child
        current = child

    class DeepMessage:
        content = root

    assert "DEPTH_LIMIT_EXCEEDED" in str(
        normalize_message(DeepMessage(), sequence=2)
    )
