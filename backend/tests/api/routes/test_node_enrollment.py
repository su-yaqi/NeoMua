import base64

from fastapi.testclient import TestClient
from sqlmodel import Session

from app import crud
from app.core.config import settings
from app.models import NamespaceRole
from tests.api.routes.test_namespaces import create_namespace, namespace_headers
from tests.utils.user import authentication_token_from_email, create_random_user


def test_admin_sees_enrollment_token_only_on_creation(
    client: TestClient, db: Session, superuser_token_headers: dict[str, str]
) -> None:
    namespace = create_namespace(db)
    headers = namespace_headers(superuser_token_headers, namespace.id)
    created = client.post(
        f"{settings.API_V1_STR}/runtimes/nodes/enrollment-tokens", headers=headers
    )
    assert created.status_code == 201
    assert created.json()["token"].startswith("nmenr_")
    listed = client.get(
        f"{settings.API_V1_STR}/runtimes/nodes/enrollment-tokens", headers=headers
    )
    assert listed.status_code == 200
    assert "token" not in listed.json()["data"][0]
    assert "token_hash" not in listed.json()["data"][0]


def test_developer_cannot_create_enrollment_token(
    client: TestClient, db: Session
) -> None:
    namespace = create_namespace(db)
    developer = create_random_user(db)
    crud.ensure_namespace_membership(
        session=db, user_id=developer.id, namespace_id=namespace.id,
        role=NamespaceRole.DEVELOPER,
    )
    headers = namespace_headers(
        authentication_token_from_email(client=client, email=developer.email, db=db),
        namespace.id,
    )
    response = client.post(
        f"{settings.API_V1_STR}/runtimes/nodes/enrollment-tokens", headers=headers
    )
    assert response.status_code == 403


def test_node_enrollment_consumes_token_once(
    client: TestClient, db: Session, superuser_token_headers: dict[str, str]
) -> None:
    namespace = create_namespace(db)
    token = client.post(
        f"{settings.API_V1_STR}/runtimes/nodes/enrollment-tokens",
        headers=namespace_headers(superuser_token_headers, namespace.id),
    ).json()["token"]
    payload = {
        "token": token,
        "name": "build-node",
        "hostname": "host-1",
        "os_name": "linux",
        "architecture": "arm64",
        "agent_version": "0.1.0",
        "sdk_version": "0.2.110",
        "public_key": base64.b64encode(b"a" * 32).decode(),
    }
    enrolled = client.post(f"{settings.API_V1_STR}/node/enroll", json=payload)
    assert enrolled.status_code == 201
    assert enrolled.json()["node_id"]
    assert enrolled.json()["credential"]
    reused = client.post(f"{settings.API_V1_STR}/node/enroll", json=payload)
    assert reused.status_code == 409


def test_admin_lists_and_configures_enrolled_node_runtime(
    client: TestClient, db: Session, superuser_token_headers: dict[str, str]
) -> None:
    namespace = create_namespace(db)
    headers = namespace_headers(superuser_token_headers, namespace.id)
    token = client.post(
        f"{settings.API_V1_STR}/runtimes/nodes/enrollment-tokens", headers=headers
    ).json()["token"]
    enrolled = client.post(
        f"{settings.API_V1_STR}/node/enroll",
        json={
            "token": token, "name": "worker", "hostname": "host",
            "os_name": "linux", "architecture": "amd64",
            "agent_version": "0.1.0",
            "public_key": base64.b64encode(b"b" * 32).decode(),
        },
    ).json()
    listed = client.get(f"{settings.API_V1_STR}/runtimes/nodes", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["data"][0]["id"] == enrolled["node_id"]
    assert "public_key" not in listed.json()["data"][0]
    configured = client.put(
        f"{settings.API_V1_STR}/runtimes/nodes/{enrolled['node_id']}/runtime",
        headers=headers,
        json={
            "route_mode": "direct_anthropic", "model_id": "claude-node",
            "base_url": "https://anthropic-compatible.example",
            "secret_inputs": {"api_key": "node-secret"},
        },
    )
    assert configured.status_code == 200
    assert configured.json()["model_id"] == "claude-node"


def test_developer_can_list_but_cannot_configure_nodes(
    client: TestClient, db: Session
) -> None:
    namespace = create_namespace(db)
    developer = create_random_user(db)
    crud.ensure_namespace_membership(
        session=db, user_id=developer.id, namespace_id=namespace.id,
        role=NamespaceRole.DEVELOPER,
    )
    headers = namespace_headers(
        authentication_token_from_email(client=client, email=developer.email, db=db),
        namespace.id,
    )
    assert client.get(
        f"{settings.API_V1_STR}/runtimes/nodes", headers=headers
    ).status_code == 200
    response = client.put(
        f"{settings.API_V1_STR}/runtimes/nodes/{'0' * 8}-{'0' * 4}-{'0' * 4}-{'0' * 4}-{'0' * 12}/runtime",
        headers=headers,
        json={"route_mode": "direct_anthropic", "model_id": "x",
              "base_url": "https://example.test", "secret_inputs": {"api_key": "x"}},
    )
    assert response.status_code == 403
