import time
import uuid

import jwt
from fastapi.testclient import TestClient

from model_gateway import app as app_module

app = app_module.app


def test_requires_gateway_token() -> None:
    response = TestClient(app).post(
        f"/tasks/{uuid.uuid4()}/v1/messages", json={"model": "x", "messages": []}
    )
    assert response.status_code == 401


def test_rejects_unsupported_thinking(monkeypatch) -> None:
    runtime_id = uuid.uuid4()
    task_id = uuid.uuid4()
    monkeypatch.setenv("GATEWAY_SIGNING_KEY", "a" * 32)
    token = jwt.encode(
        {
            "aud": "neomua-model-gateway",
            "runtime_id": str(runtime_id),
            "namespace_id": str(uuid.uuid4()),
            "task_id": str(task_id),
            "model_id": "x",
            "iat": int(time.time()),
            "exp": int(time.time()) + 60,
        },
        "a" * 32,
        algorithm="HS256",
    )

    async def route(*_args):
        return {
            "provider_kind": "openai_compatible",
            "base_url": "https://example.test/v1",
            "secret_inputs": {"api_key": "secret"},
        }

    monkeypatch.setattr(app_module, "resolve_runtime_route", route)
    response = TestClient(app).post(
        f"/tasks/{task_id}/v1/messages",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Runtime-ID": str(runtime_id),
        },
        json={"model": "x", "messages": [], "thinking": {"type": "enabled"}},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unsupported_model_capability"


def test_accepts_claude_x_api_key_header(monkeypatch) -> None:
    runtime_id = uuid.uuid4()
    task_id = uuid.uuid4()
    monkeypatch.setenv("GATEWAY_SIGNING_KEY", "a" * 32)
    token = jwt.encode(
        {
            "aud": "neomua-model-gateway",
            "runtime_id": str(runtime_id),
            "namespace_id": str(uuid.uuid4()),
            "task_id": str(task_id),
            "model_id": "x",
            "iat": int(time.time()),
            "exp": int(time.time()) + 60,
        },
        "a" * 32,
        algorithm="HS256",
    )

    async def route(*_args):
        return {
            "provider_kind": "openai_compatible",
            "base_url": "https://example.test/v1",
            "secret_inputs": {"api_key": "secret"},
        }

    monkeypatch.setattr(app_module, "resolve_runtime_route", route)
    response = TestClient(app).post(
        f"/tasks/{task_id}/v1/messages",
        headers={"x-api-key": token},
        json={"model": "x", "messages": [], "thinking": {"type": "enabled"}},
    )
    assert response.status_code == 422
