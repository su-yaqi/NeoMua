from fastapi.testclient import TestClient
from sqlmodel import Session

from app import crud
from app.core.config import settings
from app.models import NamespaceRole
from tests.api.routes.test_namespaces import create_namespace, namespace_headers
from tests.utils.user import authentication_token_from_email, create_random_user


def _platform_runtime(client: TestClient, headers: dict[str, str]) -> str:
    return client.put(
        f"{settings.API_V1_STR}/runtimes/platform", headers=headers,
        json={
            "route_mode": "direct_anthropic", "model_id": "claude-test",
            "base_url": "https://anthropic-compatible.example",
            "secret_inputs": {"api_key": "secret"},
        },
    ).json()["id"]


def test_developer_can_dispatch_ordinary_but_not_admin_task(
    client: TestClient, db: Session, superuser_token_headers: dict[str, str]
) -> None:
    namespace = create_namespace(db)
    developer = create_random_user(db)
    crud.ensure_namespace_membership(
        session=db, user_id=developer.id, namespace_id=namespace.id,
        role=NamespaceRole.DEVELOPER,
    )
    admin_headers = namespace_headers(superuser_token_headers, namespace.id)
    runtime_id = _platform_runtime(client, admin_headers)
    headers = namespace_headers(
        authentication_token_from_email(client=client, email=developer.email, db=db),
        namespace.id,
    )
    ordinary = client.post(
        f"{settings.API_V1_STR}/runtime-tasks", headers={**headers, "Idempotency-Key": "ordinary-1"},
        json={"runtime_profile_id": runtime_id, "prompt": "inspect repository", "task_kind": "ordinary"},
    )
    assert ordinary.status_code == 202
    assert ordinary.json()["snapshot"]["model_id"] == "claude-test"
    admin = client.post(
        f"{settings.API_V1_STR}/runtime-tasks", headers=headers,
        json={"runtime_profile_id": runtime_id, "prompt": "rotate credential", "task_kind": "admin"},
    )
    assert admin.status_code == 403


def test_task_idempotency_key_rejects_different_request(
    client: TestClient, db: Session, superuser_token_headers: dict[str, str]
) -> None:
    namespace = create_namespace(db)
    headers = namespace_headers(superuser_token_headers, namespace.id)
    runtime_id = _platform_runtime(client, headers)
    request_headers = {**headers, "Idempotency-Key": "same-key"}
    first = client.post(
        f"{settings.API_V1_STR}/runtime-tasks", headers=request_headers,
        json={"runtime_profile_id": runtime_id, "prompt": "first"},
    )
    duplicate = client.post(
        f"{settings.API_V1_STR}/runtime-tasks", headers=request_headers,
        json={"runtime_profile_id": runtime_id, "prompt": "first"},
    )
    conflict = client.post(
        f"{settings.API_V1_STR}/runtime-tasks", headers=request_headers,
        json={"runtime_profile_id": runtime_id, "prompt": "different"},
    )
    assert duplicate.json()["id"] == first.json()["id"]
    assert conflict.status_code == 409
