import uuid

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import settings
from tests.api.routes.test_namespaces import create_namespace, namespace_headers


def test_admin_creates_platform_runtime_instance_with_desired_configuration(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
) -> None:
    namespace = create_namespace(db)
    headers = {
        **namespace_headers(superuser_token_headers, namespace.id),
        "Idempotency-Key": "platform-runtime-v09",
    }
    response = client.post(
        f"{settings.API_V1_STR}/runtimes/platform",
        headers=headers,
        json={
            "name": "Primary Codex",
            "installation_key": f"codex-{uuid.uuid4().hex}",
            "engine_type": "codex",
            "adapter_version": "1.0.0",
            "configuration": {
                "expected_revision": 0,
                "executable": "codex",
                "arguments": [],
                "working_directory_policy": "workspace",
                "environment_allowlist": [],
                "security_policy": {"permission_mode": "default"},
                "resource_limits": {},
            },
        },
    )
    assert response.status_code == 201, response.text
    runtime = response.json()
    assert runtime["engine_type"] == "codex"
    assert runtime["status"] == "discovered"
    assert runtime["current_capability_report_id"] is None

    detail = client.get(
        f"{settings.API_V1_STR}/runtimes/{runtime['id']}", headers=headers
    )
    assert detail.status_code == 200
    assert detail.json()["configurations"][0]["status"] == "desired"


def test_runtime_configuration_uses_revision_cas(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
) -> None:
    namespace = create_namespace(db)
    headers = {
        **namespace_headers(superuser_token_headers, namespace.id),
        "Idempotency-Key": "platform-runtime-cas",
    }
    created = client.post(
        f"{settings.API_V1_STR}/runtimes/platform",
        headers=headers,
        json={
            "name": "Primary Claude",
            "installation_key": f"claude-{uuid.uuid4().hex}",
            "engine_type": "claude_code",
            "configuration": {"expected_revision": 0, "executable": "claude"},
        },
    ).json()
    conflict = client.put(
        f"{settings.API_V1_STR}/runtimes/{created['id']}/configuration",
        headers=headers,
        json={"expected_revision": 0, "executable": "claude"},
    )
    assert conflict.status_code == 409
    updated = client.put(
        f"{settings.API_V1_STR}/runtimes/{created['id']}/configuration",
        headers=headers,
        json={"expected_revision": 1, "executable": "claude"},
    )
    assert updated.status_code == 201
    assert updated.json()["revision"] == 2


def test_runtime_profile_writes_are_read_only(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
) -> None:
    namespace = create_namespace(db)
    headers = namespace_headers(superuser_token_headers, namespace.id)
    assert (
        client.put(
            f"{settings.API_V1_STR}/runtimes/platform",
            headers=headers,
            json={"route_mode": "direct_anthropic", "model_id": "legacy"},
        ).status_code
        == 410
    )
    assert (
        client.post(
            f"{settings.API_V1_STR}/runtimes/platform/sessions",
            headers=headers,
            json={"runtime_agent_release_id": str(uuid.uuid4())},
        ).status_code
        == 410
    )
