import time
import uuid

import jwt
import pytest

from model_gateway.auth import GatewayAuthError, verify_gateway_scope


def test_token_cannot_change_model(monkeypatch) -> None:
    monkeypatch.setenv("GATEWAY_SIGNING_KEY", "a" * 32)
    runtime_id = uuid.uuid4()
    token = jwt.encode(
        {"aud": "neomua-model-gateway", "runtime_id": str(runtime_id),
         "namespace_id": str(uuid.uuid4()), "task_id": str(uuid.uuid4()),
         "model_id": "model-a", "exp": int(time.time()) + 60},
        "a" * 32, algorithm="HS256",
    )
    with pytest.raises(GatewayAuthError, match="scope"):
        verify_gateway_scope(token, runtime_id, "model-b")
