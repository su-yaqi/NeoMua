import uuid

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import settings
from tests.api.routes.test_agent_tasks import ready_runtime
from tests.api.routes.test_namespaces import create_namespace, namespace_headers


def test_mcp_target_uses_runtime_instance_and_rejects_runtime_profile(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
) -> None:
    namespace = create_namespace(db)
    runtime, _binding = ready_runtime(db, namespace.id)
    headers = namespace_headers(superuser_token_headers, namespace.id)
    server = client.post(
        f"{settings.API_V1_STR}/mcp-servers",
        headers=headers,
        json={"slug": f"mcp-{uuid.uuid4().hex}", "name": "Runtime MCP"},
    )
    assert server.status_code == 201, server.text
    revision = client.post(
        f"{settings.API_V1_STR}/mcp-servers/{server.json()['id']}/revisions",
        headers=headers,
        json={
            "transport": "streamable_http",
            "config": {"endpoint": "https://mcp.example.test/tools"},
        },
    )
    assert revision.status_code == 201, revision.text
    target = client.post(
        f"{settings.API_V1_STR}/mcp-revisions/{revision.json()['id']}/targets",
        headers=headers,
        json={"runtime_instance_id": str(runtime.id)},
    )
    assert target.status_code == 201, target.text
    assert target.json()["runtime_instance_id"] == str(runtime.id)
    assert target.json()["runtime_profile_id"] is None

    legacy = client.post(
        f"{settings.API_V1_STR}/mcp-revisions/{revision.json()['id']}/targets",
        headers=headers,
        json={"runtime_profile_id": str(uuid.uuid4())},
    )
    assert legacy.status_code == 422
    assert "read-only" in legacy.text
