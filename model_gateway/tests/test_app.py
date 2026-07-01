from fastapi.testclient import TestClient

from model_gateway.app import app


def test_requires_gateway_token() -> None:
    response = TestClient(app).post("/v1/messages", json={"model": "x", "messages": []})
    assert response.status_code == 401


def test_rejects_unsupported_thinking(monkeypatch) -> None:
    monkeypatch.setenv("GATEWAY_SERVICE_TOKEN", "test-token")
    response = TestClient(app).post(
        "/v1/messages",
        headers={"Authorization": "Bearer test-token", "X-Provider-Kind": "openai_compatible"},
        json={"model": "x", "messages": [], "thinking": {"type": "enabled"}},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unsupported_model_capability"
