from fastapi.testclient import TestClient
from sqlmodel import Session

from app import crud
from app.core.config import settings
from app.models import NamespaceRole
from tests.api.routes.test_llm_provider_configs import create_provider_config
from tests.api.routes.test_namespaces import create_namespace, namespace_headers
from tests.utils.user import authentication_token_from_email, create_random_user


def test_superuser_can_create_direct_platform_runtime(
    client: TestClient, db: Session, superuser_token_headers: dict[str, str]
) -> None:
    namespace = create_namespace(db)
    response = client.put(
        f"{settings.API_V1_STR}/runtimes/platform",
        headers=namespace_headers(superuser_token_headers, namespace.id),
        json={
            "route_mode": "direct_anthropic",
            "model_id": "claude-test",
            "base_url": "https://anthropic-compatible.example/v1",
            "permission_mode": "default",
            "secret_inputs": {"api_key": "test-secret-value"},
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["secret_masked"] == "****"
    assert "secret_ciphertext" not in response.json()


def test_bypass_permissions_is_rejected(
    client: TestClient, db: Session, superuser_token_headers: dict[str, str]
) -> None:
    namespace = create_namespace(db)
    response = client.put(
        f"{settings.API_V1_STR}/runtimes/platform",
        headers=namespace_headers(superuser_token_headers, namespace.id),
        json={
            "route_mode": "direct_anthropic",
            "model_id": "x",
            "base_url": "https://example.test",
            "permission_mode": "bypassPermissions",
            "secret_inputs": {"api_key": "test-secret-value"},
        },
    )
    assert response.status_code == 422


def test_cross_namespace_provider_config_is_rejected(
    client: TestClient, db: Session, superuser_token_headers: dict[str, str]
) -> None:
    selected = create_namespace(db)
    other = create_namespace(db)
    provider = create_provider_config(
        client, namespace_headers(superuser_token_headers, other.id)
    )
    response = client.put(
        f"{settings.API_V1_STR}/runtimes/platform",
        headers=namespace_headers(superuser_token_headers, selected.id),
        json={
            "route_mode": "platform_gateway",
            "provider_config_id": provider["id"],
            "model_id": "model-x",
        },
    )
    assert response.status_code == 404


def test_developer_can_read_but_cannot_update_runtime(
    client: TestClient, db: Session, superuser_token_headers: dict[str, str]
) -> None:
    developer = create_random_user(db)
    namespace = create_namespace(db)
    crud.ensure_namespace_membership(
        session=db,
        user_id=developer.id,
        namespace_id=namespace.id,
        role=NamespaceRole.DEVELOPER,
    )
    admin_headers = namespace_headers(superuser_token_headers, namespace.id)
    client.put(
        f"{settings.API_V1_STR}/runtimes/platform",
        headers=admin_headers,
        json={
            "route_mode": "direct_anthropic",
            "model_id": "x",
            "base_url": "https://example.test",
            "secret_inputs": {"api_key": "test-secret"},
        },
    )
    developer_headers = namespace_headers(
        authentication_token_from_email(client=client, email=developer.email, db=db),
        namespace.id,
    )
    assert (
        client.get(
            f"{settings.API_V1_STR}/runtimes/platform", headers=developer_headers
        ).status_code
        == 200
    )
    response = client.put(
        f"{settings.API_V1_STR}/runtimes/platform",
        headers=developer_headers,
        json={
            "route_mode": "direct_anthropic",
            "model_id": "y",
            "base_url": "https://example.test",
            "secret_inputs": {"api_key": "test-secret"},
        },
    )
    assert response.status_code == 403


def test_developer_can_create_session_and_message_task(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
    monkeypatch,
) -> None:
    developer = create_random_user(db)
    namespace = create_namespace(db)
    crud.ensure_namespace_membership(
        session=db,
        user_id=developer.id,
        namespace_id=namespace.id,
        role=NamespaceRole.DEVELOPER,
    )
    admin_headers = namespace_headers(superuser_token_headers, namespace.id)
    client.put(
        f"{settings.API_V1_STR}/runtimes/platform",
        headers=admin_headers,
        json={
            "route_mode": "direct_anthropic",
            "model_id": "claude-test",
            "base_url": "https://example.test",
            "secret_inputs": {"api_key": "test-secret"},
        },
    )

    async def compatible(*_args):
        return {"type": "message"}

    monkeypatch.setattr(
        "app.api.routes.runtimes.check_anthropic_compatibility", compatible
    )
    assert (
        client.post(
            f"{settings.API_V1_STR}/runtimes/platform/validate", headers=admin_headers
        ).status_code
        == 200
    )
    headers = namespace_headers(
        authentication_token_from_email(client=client, email=developer.email, db=db),
        namespace.id,
    )
    session_response = client.post(
        f"{settings.API_V1_STR}/runtimes/platform/sessions", headers=headers, json={}
    )
    assert session_response.status_code == 201
    task_response = client.post(
        f"{settings.API_V1_STR}/runtimes/sessions/{session_response.json()['id']}/messages",
        headers=headers,
        json={"prompt": "hello"},
    )
    assert task_response.status_code == 202
    assert task_response.json()["status"] == "queued"


def test_developer_can_read_task_events_and_cancel_queued_task(
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
            "secret_inputs": {"api_key": "secret"},
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
    task = client.post(
        f"{settings.API_V1_STR}/runtimes/sessions/{session_id}/messages",
        headers=headers,
        json={"prompt": "hello"},
    ).json()
    events = client.get(
        f"{settings.API_V1_STR}/runtimes/tasks/{task['id']}/events", headers=headers
    )
    assert events.status_code == 200
    assert events.json()[0]["event_type"] == "user_message"
    cancelled = client.post(
        f"{settings.API_V1_STR}/runtimes/tasks/{task['id']}/cancel", headers=headers
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
