from runtime_worker.events import normalize_message


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
