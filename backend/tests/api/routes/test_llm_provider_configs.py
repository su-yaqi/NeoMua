import uuid

from fastapi.testclient import TestClient
from sqlmodel import Session

from app import crud
from app.core.config import settings
from app.models import NamespaceRole
from tests.api.routes.test_namespaces import create_namespace, namespace_headers
from tests.utils.user import authentication_token_from_email, create_random_user
from tests.utils.utils import random_lower_string


def create_provider_config(
    client: TestClient,
    headers: dict[str, str],
    *,
    provider_slug: str = "deepseek",
    config_name: str | None = None,
    base_url: str = "https://api.deepseek.com/v1",
    secret_inputs: dict[str, str] | None = None,
) -> dict:
    response = client.post(
        f"{settings.API_V1_STR}/llm/provider-configs",
        headers=headers,
        json={
            "config_name": config_name or f"配置-{random_lower_string()}",
            "provider_slug": provider_slug,
            "base_url": base_url,
            "enabled": True,
            "secret_inputs": secret_inputs or {"api_token": "sk-test-secret-1234"},
            "extra_config": {},
            "manual_models": [],
            "enabled_model_ids": [],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_superuser_can_read_llm_provider_catalog(
    client: TestClient, db: Session, superuser_token_headers: dict[str, str]
) -> None:
    namespace = create_namespace(db)

    response = client.get(
        f"{settings.API_V1_STR}/llm/providers/catalog",
        headers=namespace_headers(superuser_token_headers, namespace.id),
    )

    assert response.status_code == 200
    body = response.json()
    slugs = {item["provider_slug"] for item in body["data"]}
    assert "deepseek" in slugs
    assert "openrouter" in slugs
    assert "custom" in slugs
    assert body["count"] >= 20


def test_superuser_with_unknown_namespace_cannot_access_llm_provider_catalog(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    response = client.get(
        f"{settings.API_V1_STR}/llm/providers/catalog",
        headers=namespace_headers(superuser_token_headers, uuid.uuid4()),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Namespace not found"


def test_namespace_admin_can_create_provider_config_with_masked_secret(
    client: TestClient, db: Session
) -> None:
    admin_user = create_random_user(db)
    namespace = create_namespace(db, admin_user_id=admin_user.id)
    headers = namespace_headers(
        authentication_token_from_email(client=client, email=admin_user.email, db=db),
        namespace.id,
    )

    response = client.post(
        f"{settings.API_V1_STR}/llm/provider-configs",
        headers=headers,
        json={
            "config_name": f"deepseek-{random_lower_string()}",
            "provider_slug": "deepseek",
            "base_url": "https://api.deepseek.com/v1",
            "enabled": True,
            "secret_inputs": {"api_token": "sk-namespace-secret-9876"},
            "extra_config": {},
            "manual_models": [],
            "enabled_model_ids": [],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["provider_slug"] == "deepseek"
    assert body["secret_masked"] == "****"
    assert "namespace-secret" not in body["secret_masked"]
    assert body["validation_status"] == "unverified"


def test_non_admin_cannot_manage_provider_configs(
    client: TestClient, db: Session
) -> None:
    user = create_random_user(db)
    namespace = create_namespace(db)
    crud.ensure_namespace_membership(
        session=db,
        user_id=user.id,
        namespace_id=namespace.id,
        role=NamespaceRole.USER,
    )
    headers = namespace_headers(
        authentication_token_from_email(client=client, email=user.email, db=db),
        namespace.id,
    )

    response = client.get(
        f"{settings.API_V1_STR}/llm/provider-configs",
        headers=headers,
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Namespace admin privilege required"


def test_update_provider_config_without_secret_keeps_existing_mask(
    client: TestClient, db: Session, superuser_token_headers: dict[str, str]
) -> None:
    namespace = create_namespace(db)
    headers = namespace_headers(superuser_token_headers, namespace.id)
    created = create_provider_config(
        client,
        headers,
        config_name=f"keep-mask-{random_lower_string()}",
        secret_inputs={"api_token": "sk-original-secret-1111"},
    )

    response = client.patch(
        f"{settings.API_V1_STR}/llm/provider-configs/{created['id']}",
        headers=headers,
        json={
            "config_name": f"updated-{random_lower_string()}",
            "enabled": False,
            "extra_config": {"label": "updated"},
            "manual_models": [],
            "enabled_model_ids": [],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["secret_masked"] == created["secret_masked"]
    assert body["enabled"] is False


def test_validate_oauth_provider_config_returns_unsupported(
    client: TestClient, db: Session, superuser_token_headers: dict[str, str]
) -> None:
    namespace = create_namespace(db)
    headers = namespace_headers(superuser_token_headers, namespace.id)
    created = create_provider_config(
        client,
        headers,
        provider_slug="openai-codex",
        config_name=f"codex-{random_lower_string()}",
        base_url="https://chatgpt.com/backend-api/codex",
        secret_inputs={},
    )

    response = client.post(
        f"{settings.API_V1_STR}/llm/provider-configs/{created['id']}/validate",
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["validation_status"] == "unsupported"
    assert "不支持" in body["validation_message"]


def test_sync_models_for_custom_provider_uses_discovered_and_manual_models(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
    monkeypatch,
) -> None:
    namespace = create_namespace(db)
    headers = namespace_headers(superuser_token_headers, namespace.id)
    created = create_provider_config(
        client,
        headers,
        provider_slug="custom",
        config_name=f"custom-{random_lower_string()}",
        base_url="https://llm.example.com/v1",
        secret_inputs={"api_token": "sk-custom-secret-2222"},
    )

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, list[dict[str, str]]]:
            return {
                "data": [
                    {"id": "deepseek-v4-pro"},
                    {"id": "deepseek-v4-flash"},
                ]
            }

    def fake_get(url: str, headers: dict[str, str] | None = None, timeout: int = 0):
        assert url == "https://llm.example.com/v1/models"
        assert timeout > 0
        assert headers is not None
        assert headers["Authorization"] == "Bearer sk-custom-secret-2222"
        return FakeResponse()

    monkeypatch.setattr("httpx.get", fake_get)

    response = client.post(
        f"{settings.API_V1_STR}/llm/provider-configs/{created['id']}/sync-models",
        headers=headers,
        json={
            "manual_models": [
                {"model_id": "manual-model", "display_name": "Manual Model"}
            ],
            "enabled_model_ids": ["deepseek-v4-pro", "manual-model"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["validation_status"] == "success"
    models = {item["model_id"]: item for item in body["models"]}
    assert models["deepseek-v4-pro"]["source_type"] == "discovered"
    assert models["deepseek-v4-pro"]["is_enabled"] is True
    assert models["manual-model"]["source_type"] == "manual"
    assert models["manual-model"]["is_enabled"] is True


def test_validate_draft_provider_does_not_persist_config(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
    monkeypatch,
) -> None:
    namespace = create_namespace(db)
    headers = namespace_headers(superuser_token_headers, namespace.id)

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, list[dict[str, str]]]:
            return {"data": [{"id": "draft-model"}]}

    monkeypatch.setattr("httpx.get", lambda *args, **kwargs: FakeResponse())

    response = client.post(
        f"{settings.API_V1_STR}/llm/provider-configs/draft/validate",
        headers=headers,
        json={
            "provider_slug": "custom",
            "base_url": "https://draft.example.com/v1",
            "secret_inputs": {"api_token": "sk-draft-secret"},
            "extra_config": {},
            "manual_models": [],
            "enabled_model_ids": [],
        },
    )

    assert response.status_code == 200
    assert response.json()["validation_status"] == "success"

    configs_response = client.get(
        f"{settings.API_V1_STR}/llm/provider-configs",
        headers=headers,
    )
    assert configs_response.status_code == 200
    assert configs_response.json()["count"] == 0


def test_sync_draft_models_returns_preview_without_persisting_config(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
    monkeypatch,
) -> None:
    namespace = create_namespace(db)
    headers = namespace_headers(superuser_token_headers, namespace.id)

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, list[dict[str, str]]]:
            return {"data": [{"id": "draft-discovered"}]}

    monkeypatch.setattr("httpx.get", lambda *args, **kwargs: FakeResponse())

    response = client.post(
        f"{settings.API_V1_STR}/llm/provider-configs/draft/sync-models",
        headers=headers,
        json={
            "provider_slug": "custom",
            "base_url": "https://draft.example.com/v1",
            "secret_inputs": {"api_token": "sk-draft-secret"},
            "extra_config": {},
            "manual_models": [
                {"model_id": "draft-manual", "display_name": "Draft Manual"}
            ],
            "enabled_model_ids": ["draft-discovered", "draft-manual"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["validation_status"] == "success"
    models = {item["model_id"]: item for item in body["models"]}
    assert models["draft-discovered"]["source_type"] == "discovered"
    assert models["draft-discovered"]["is_enabled"] is True
    assert models["draft-manual"]["source_type"] == "manual"

    configs_response = client.get(
        f"{settings.API_V1_STR}/llm/provider-configs",
        headers=headers,
    )
    assert configs_response.json()["count"] == 0


def test_create_after_draft_sync_refetches_and_persists_models(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
    monkeypatch,
) -> None:
    namespace = create_namespace(db)
    headers = namespace_headers(superuser_token_headers, namespace.id)

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, list[dict[str, str]]]:
            return {"data": [{"id": "persisted-discovered"}]}

    monkeypatch.setattr("httpx.get", lambda *args, **kwargs: FakeResponse())

    response = client.post(
        f"{settings.API_V1_STR}/llm/provider-configs",
        headers=headers,
        json={
            "config_name": f"draft-sync-{random_lower_string()}",
            "provider_slug": "custom",
            "base_url": "https://draft.example.com/v1",
            "enabled": True,
            "secret_inputs": {"api_token": "sk-draft-secret"},
            "extra_config": {},
            "manual_models": [],
            "enabled_model_ids": ["persisted-discovered"],
            "validate_on_create": True,
            "sync_models_on_create": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["validation_status"] == "success"
    models = {item["model_id"]: item for item in body["models"]}
    assert models["persisted-discovered"]["source_type"] == "discovered"
    assert models["persisted-discovered"]["is_enabled"] is True


def test_create_after_draft_sync_is_atomic_when_refetch_fails(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
    monkeypatch,
) -> None:
    namespace = create_namespace(db)
    headers = namespace_headers(superuser_token_headers, namespace.id)

    def fail_get(*_args, **_kwargs):
        raise RuntimeError("provider temporarily unavailable")

    monkeypatch.setattr("httpx.get", fail_get)

    response = client.post(
        f"{settings.API_V1_STR}/llm/provider-configs",
        headers=headers,
        json={
            "config_name": f"draft-sync-failure-{random_lower_string()}",
            "provider_slug": "custom",
            "base_url": "https://draft.example.com/v1",
            "enabled": True,
            "secret_inputs": {"api_token": "sk-draft-secret"},
            "extra_config": {},
            "manual_models": [],
            "enabled_model_ids": ["persisted-discovered"],
            "sync_models_on_create": True,
        },
    )

    assert response.status_code == 502

    configs_response = client.get(
        f"{settings.API_V1_STR}/llm/provider-configs",
        headers=headers,
    )
    assert configs_response.json()["count"] == 0
