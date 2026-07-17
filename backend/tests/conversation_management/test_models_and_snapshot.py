from fastapi.testclient import TestClient
from sqlalchemy import UniqueConstraint
from sqlmodel import Session

from app.conversation_management.models import ConversationAgent
from app.core.config import settings
from tests.api.routes.test_agent_tasks import ready_runtime
from tests.api.routes.test_namespaces import create_namespace, namespace_headers


def test_conversation_allows_same_agent_identity_in_multiple_roles() -> None:
    unique_column_sets = {
        tuple(column.name for column in constraint.columns)
        for constraint in ConversationAgent.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("conversation_id", "agent_id") not in unique_column_sets


def test_v09_chat_freezes_exact_runtime_model_binding(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
) -> None:
    namespace = create_namespace(db)
    runtime, binding = ready_runtime(db, namespace.id)
    headers = {
        **namespace_headers(superuser_token_headers, namespace.id),
        "Idempotency-Key": "v09-chat",
    }
    response = client.post(
        f"{settings.API_V1_STR}/conversations",
        headers=headers,
        json={
            "title": "Exact model Chat",
            "mode": "chat",
            "runtime_instance_id": str(runtime.id),
            "chat_model_selection": {
                "mode": "exact",
                "runtime_model_binding_id": str(binding.id),
            },
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["runtime_instance_id"] == str(runtime.id)
    execution = body["configuration"]["execution_bindings"]
    assert len(execution) == 1
    assert execution[0]["role_key"] == "chat"
    assert execution[0]["runtime_model_binding_id"] == str(binding.id)


def test_v09_chat_configuration_revision_is_exact_and_cas_guarded(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
) -> None:
    namespace = create_namespace(db)
    runtime, binding = ready_runtime(db, namespace.id)
    headers = namespace_headers(superuser_token_headers, namespace.id)
    created = client.post(
        f"{settings.API_V1_STR}/conversations",
        headers={**headers, "Idempotency-Key": "v09-chat-cas"},
        json={
            "title": "CAS Chat",
            "mode": "chat",
            "runtime_instance_id": str(runtime.id),
            "chat_model_selection": {
                "mode": "exact",
                "runtime_model_binding_id": str(binding.id),
            },
        },
    ).json()
    conflict = client.post(
        f"{settings.API_V1_STR}/conversations/{created['id']}/configuration-revisions",
        headers=headers,
        json={
            "expected_revision": 99,
            "chat_model_selection": {
                "mode": "exact",
                "runtime_model_binding_id": str(binding.id),
            },
        },
    )
    assert conflict.status_code == 409


def test_legacy_conversation_creation_is_read_only(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
) -> None:
    namespace = create_namespace(db)
    response = client.post(
        f"{settings.API_V1_STR}/conversations",
        headers={
            **namespace_headers(superuser_token_headers, namespace.id),
            "Idempotency-Key": "legacy-chat",
        },
        json={
            "title": "Legacy",
            "mode": "chat",
            "runtime_id": "00000000-0000-0000-0000-000000000001",
            "provider_config_id": "00000000-0000-0000-0000-000000000002",
            "model_id": "legacy-model",
        },
    )
    assert response.status_code == 410
