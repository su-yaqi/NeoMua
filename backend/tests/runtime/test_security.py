import uuid

import pytest

from app.runtime.security import (
    GatewayScopeError,
    issue_gateway_token,
    redact_event_payload,
    verify_gateway_token,
)


def test_gateway_token_is_bound_to_runtime_and_model() -> None:
    namespace_id, runtime_id, task_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    token = issue_gateway_token(namespace_id, runtime_id, task_id, "model-a")
    claims = verify_gateway_token(token, runtime_id=runtime_id, model_id="model-a")
    assert claims["namespace_id"] == str(namespace_id)
    with pytest.raises(GatewayScopeError):
        verify_gateway_token(token, runtime_id=runtime_id, model_id="model-b")


def test_event_payload_redacts_nested_credentials() -> None:
    assert redact_event_payload(
        {
            "tool": {"api_key": "secret", "args": [{"password": "value"}]},
            "text": "keep",
        }
    ) == {
        "tool": {"api_key": "[REDACTED]", "args": [{"password": "[REDACTED]"}]},
        "text": "keep",
    }
