import uuid

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import settings
from app.runtime.models import AgentTask
from app.runtime.policy import TaskStatus
from app.runtime.security import expected_internal_token, issue_gateway_token
from tests.api.routes.test_namespaces import create_namespace, namespace_headers


def test_internal_endpoint_rejects_user_jwt(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    response = client.post(
        f"{settings.API_V1_STR}/internal/runtime/events",
        headers=superuser_token_headers,
        json={"task_id": "00000000-0000-0000-0000-000000000000", "events": []},
    )
    assert response.status_code == 403


def test_gateway_can_resolve_direct_runtime_without_exposing_secret_to_browser(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
) -> None:
    namespace = create_namespace(db)
    created = client.put(
        f"{settings.API_V1_STR}/runtimes/platform",
        headers=namespace_headers(superuser_token_headers, namespace.id),
        json={
            "route_mode": "direct_anthropic",
            "model_id": "claude-test",
            "base_url": "https://example.test",
            "secret_inputs": {"api_key": "secret-value"},
        },
    ).json()
    task = AgentTask(
        namespace_id=namespace.id,
        runtime_profile_id=uuid.UUID(created["id"]),
        prompt="route probe",
        snapshot={"model_id": "claude-test"},
        status=TaskStatus.RUNNING,
    )
    db.add(task)
    db.commit()
    task_id = task.id
    token = issue_gateway_token(
        namespace.id, uuid.UUID(created["id"]), task_id, "claude-test"
    )
    response = client.get(
        f"{settings.API_V1_STR}/internal/runtime/routes/{created['id']}/tasks/{task_id}",
        params={"model_id": "claude-test"},
        headers={
            "X-Runtime-Token": expected_internal_token(),
            "Authorization": f"Bearer {token}",
        },
    )
    assert response.status_code == 200
    assert response.json()["secret_inputs"]["api_key"] == "secret-value"


def test_worker_claims_queued_platform_task(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
    monkeypatch,
) -> None:
    namespace = create_namespace(db)
    headers = namespace_headers(superuser_token_headers, namespace.id)
    client.put(
        f"{settings.API_V1_STR}/runtimes/platform",
        headers=headers,
        json={
            "route_mode": "direct_anthropic",
            "model_id": "claude-test",
            "base_url": "https://example.test",
            "secret_inputs": {"api_key": "secret-value"},
        },
    )

    async def compatible(*_args):
        return {"type": "message"}

    monkeypatch.setattr(
        "app.api.routes.runtimes.check_anthropic_compatibility", compatible
    )
    assert (
        client.post(
            f"{settings.API_V1_STR}/runtimes/platform/validate", headers=headers
        ).status_code
        == 200
    )
    session_id = client.post(
        f"{settings.API_V1_STR}/runtimes/platform/sessions", headers=headers, json={}
    ).json()["id"]
    task_id = client.post(
        f"{settings.API_V1_STR}/runtimes/sessions/{session_id}/messages",
        headers=headers,
        json={"prompt": "hello"},
    ).json()["id"]
    response = client.post(
        f"{settings.API_V1_STR}/internal/runtime/tasks/claim",
        headers={"X-Runtime-Token": expected_internal_token()},
        json={"worker_id": "platform-worker-1"},
    )
    assert response.status_code == 200
    assert response.json()["task_id"] == task_id
    assert response.json()["command"]["env"]["ANTHROPIC_API_KEY"] == "secret-value"
    event_response = client.post(
        f"{settings.API_V1_STR}/internal/runtime/events",
        headers={"X-Runtime-Token": expected_internal_token()},
        json={
            "task_id": task_id,
            "events": [
                {
                    "sequence": 2,
                    "event_type": "result",
                    "payload": {"result": "done", "session_id": "sdk-session-1"},
                }
            ],
        },
    )
    assert event_response.status_code == 200
    db.expire_all()
    assert db.get(AgentTask, uuid.UUID(task_id)).status == TaskStatus.SUCCEEDED
    next_task_id = client.post(
        f"{settings.API_V1_STR}/runtimes/sessions/{session_id}/messages",
        headers=headers,
        json={"prompt": "follow up"},
    ).json()["id"]
    next_claim = client.post(
        f"{settings.API_V1_STR}/internal/runtime/tasks/claim",
        headers={"X-Runtime-Token": expected_internal_token()},
        json={"worker_id": "platform-worker-1"},
    )
    assert next_claim.json()["task_id"] == next_task_id
    assert next_claim.json()["command"]["sdk_session_id"] == "sdk-session-1"
