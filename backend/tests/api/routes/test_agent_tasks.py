import uuid

from fastapi.testclient import TestClient
from sqlmodel import Session

from app import crud
from app.core.config import settings
from app.models import NamespaceRole
from app.runtime.models import RuntimeProfile
from tests.api.routes.test_namespaces import create_namespace, namespace_headers
from tests.utils.agent_release import create_active_agent_binding
from tests.utils.user import authentication_token_from_email, create_random_user


def _platform_runtime(client: TestClient, headers: dict[str, str], monkeypatch) -> str:
    runtime_id = client.put(
        f"{settings.API_V1_STR}/runtimes/platform",
        headers=headers,
        json={
            "route_mode": "direct_anthropic",
            "model_id": "claude-test",
            "base_url": "https://anthropic-compatible.example",
            "secret_inputs": {"api_key": "secret"},
        },
    ).json()["id"]

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
    return runtime_id


def test_developer_can_dispatch_ordinary_but_not_admin_task(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
    monkeypatch,
) -> None:
    namespace = create_namespace(db)
    developer = create_random_user(db)
    crud.ensure_namespace_membership(
        session=db,
        user_id=developer.id,
        namespace_id=namespace.id,
        role=NamespaceRole.DEVELOPER,
    )
    admin_headers = namespace_headers(superuser_token_headers, namespace.id)
    runtime_id = _platform_runtime(client, admin_headers, monkeypatch)
    runtime = db.get(RuntimeProfile, uuid.UUID(runtime_id))
    assert runtime is not None
    binding = create_active_agent_binding(
        db,
        namespace_id=namespace.id,
        runtime_profile_id=runtime.id,
        provider_config_id=runtime.provider_config_id,
        model_id=runtime.model_id,
    )
    headers = namespace_headers(
        authentication_token_from_email(client=client, email=developer.email, db=db),
        namespace.id,
    )
    ordinary = client.post(
        f"{settings.API_V1_STR}/runtime-tasks",
        headers={**headers, "Idempotency-Key": "ordinary-1"},
        json={
            "runtime_profile_id": runtime_id,
            "runtime_agent_release_id": str(binding.id),
            "prompt": "inspect repository",
            "task_kind": "ordinary",
        },
    )
    assert ordinary.status_code == 202
    assert ordinary.json()["snapshot"]["model_id"] == "claude-test"
    admin = client.post(
        f"{settings.API_V1_STR}/runtime-tasks",
        headers=headers,
        json={
            "runtime_profile_id": runtime_id,
            "runtime_agent_release_id": str(binding.id),
            "prompt": "rotate credential",
            "task_kind": "admin",
        },
    )
    assert admin.status_code == 403


def test_task_idempotency_key_rejects_different_request(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
    monkeypatch,
) -> None:
    namespace = create_namespace(db)
    headers = namespace_headers(superuser_token_headers, namespace.id)
    runtime_id = _platform_runtime(client, headers, monkeypatch)
    runtime = db.get(RuntimeProfile, uuid.UUID(runtime_id))
    assert runtime is not None
    binding = create_active_agent_binding(
        db,
        namespace_id=namespace.id,
        runtime_profile_id=runtime.id,
        provider_config_id=runtime.provider_config_id,
        model_id=runtime.model_id,
    )
    request_headers = {**headers, "Idempotency-Key": "same-key"}
    first = client.post(
        f"{settings.API_V1_STR}/runtime-tasks",
        headers=request_headers,
        json={
            "runtime_profile_id": runtime_id,
            "runtime_agent_release_id": str(binding.id),
            "prompt": "first",
        },
    )
    duplicate = client.post(
        f"{settings.API_V1_STR}/runtime-tasks",
        headers=request_headers,
        json={
            "runtime_profile_id": runtime_id,
            "runtime_agent_release_id": str(binding.id),
            "prompt": "first",
        },
    )
    conflict = client.post(
        f"{settings.API_V1_STR}/runtime-tasks",
        headers=request_headers,
        json={
            "runtime_profile_id": runtime_id,
            "runtime_agent_release_id": str(binding.id),
            "prompt": "different",
        },
    )
    assert duplicate.json()["id"] == first.json()["id"]
    assert conflict.status_code == 409
