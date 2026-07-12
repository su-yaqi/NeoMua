import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings


def _ns_headers(headers: dict[str, str], namespace_id: str) -> dict[str, str]:
    h = dict(headers)
    h["X-Namespace-Id"] = namespace_id
    return h


@pytest.fixture
def namespace_setup(client: TestClient, superuser_token_headers: dict[str, str]):
    # Create a namespace via the platform endpoint (POST /platform/namespaces).
    r = client.post(
        f"{settings.API_V1_STR}/platform/namespaces",
        json={
            "name": f"ns-agents-{uuid.uuid4().hex[:6]}",
            "code": f"ns-agents-{uuid.uuid4().hex[:6]}",
            "is_active": True,
        },
        headers=superuser_token_headers,
    )
    assert r.status_code == 200, r.text
    ns = r.json()
    return ns["id"]


def test_create_agent_and_draft(
    client: TestClient, superuser_token_headers, namespace_setup
):
    headers = _ns_headers(superuser_token_headers, namespace_setup)
    r = client.post(
        f"{settings.API_V1_STR}/agents",
        json={"slug": "my-agent", "name": "My Agent"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    agent = r.json()
    assert agent["slug"] == "my-agent"

    # Draft auto-created at revision 1.
    r = client.get(f"{settings.API_V1_STR}/agents/{agent['id']}/draft", headers=headers)
    assert r.status_code == 200
    draft = r.json()
    assert draft["revision"] == 1
    assert draft["validation_status"] == "unvalidated"


def test_duplicate_slug_409(
    client: TestClient, superuser_token_headers, namespace_setup
):
    headers = _ns_headers(superuser_token_headers, namespace_setup)
    body = {"slug": "dup", "name": "First"}
    r = client.post(f"{settings.API_V1_STR}/agents", json=body, headers=headers)
    assert r.status_code == 201
    r = client.post(f"{settings.API_V1_STR}/agents", json=body, headers=headers)
    assert r.status_code == 409


def test_slug_immutable(
    client: TestClient, superuser_token_headers, namespace_setup
):
    headers = _ns_headers(superuser_token_headers, namespace_setup)
    r = client.post(
        f"{settings.API_V1_STR}/agents",
        json={"slug": "s1", "name": "S1"},
        headers=headers,
    )
    agent_id = r.json()["id"]
    # AgentUpdate schema has no slug field. Pydantic ignores extra fields by
    # default (it does NOT raise 422), so the PATCH succeeds with the slug
    # silently dropped. Slug immutability is therefore verified by confirming
    # the PATCH succeeds and the GET still returns the original slug.
    r = client.patch(
        f"{settings.API_V1_STR}/agents/{agent_id}",
        json={"slug": "s2", "name": "S2"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    # Verify slug unchanged.
    r = client.get(f"{settings.API_V1_STR}/agents/{agent_id}", headers=headers)
    assert r.json()["slug"] == "s1"


def test_cas_draft_conflict_409(
    client: TestClient, superuser_token_headers, namespace_setup
):
    headers = _ns_headers(superuser_token_headers, namespace_setup)
    agent = client.post(
        f"{settings.API_V1_STR}/agents",
        json={"slug": "cas", "name": "CAS"},
        headers=headers,
    ).json()
    # Save at revision 1.
    r = client.put(
        f"{settings.API_V1_STR}/agents/{agent['id']}/draft",
        json={"expected_revision": 1, "system_prompt": "first"},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["revision"] == 2
    # Second save at stale revision 1 -> 409.
    r = client.put(
        f"{settings.API_V1_STR}/agents/{agent['id']}/draft",
        json={"expected_revision": 1, "system_prompt": "second"},
        headers=headers,
    )
    assert r.status_code == 409
    body = r.json()
    assert body["detail"]["current_revision"] == 2


def test_draft_save_invalidates_validation(
    client: TestClient, superuser_token_headers, namespace_setup
):
    headers = _ns_headers(superuser_token_headers, namespace_setup)
    agent = client.post(
        f"{settings.API_V1_STR}/agents",
        json={"slug": "inv", "name": "Inv"},
        headers=headers,
    ).json()

    # Set up a fully-valid draft so validation SUCCEEDS (status "validated").
    # A valid draft needs: an enabled harness profile, an enabled provider
    # config with an enabled model, and a non-empty system prompt. Only a
    # successful validation transitions to "stale" after an edit; a draft that
    # fails validation stays "error" (see draft_validation_status).
    profile = client.post(
        f"{settings.API_V1_STR}/harness-profiles",
        json={"name": "inv-prof"},
        headers=headers,
    ).json()
    config = client.post(
        f"{settings.API_V1_STR}/llm/provider-configs",
        json={
            "config_name": "inv-cfg",
            "provider_slug": "deepseek",
            "base_url": "https://api.deepseek.com/v1",
            "enabled": True,
            "secret_inputs": {"api_token": "sk-test-secret-1234"},
            "extra_config": {},
            "manual_models": [{"model_id": "deepseek-chat"}],
            "enabled_model_ids": ["deepseek-chat"],
        },
        headers=headers,
    ).json()
    client.put(
        f"{settings.API_V1_STR}/agents/{agent['id']}/draft",
        json={
            "expected_revision": 1,
            "harness_profile_id": profile["id"],
            "provider_config_id": config["id"],
            "model_id": "deepseek-chat",
            "system_prompt": "You are helpful.",
        },
        headers=headers,
    )
    # Validate -> succeeds (validated_revision set, status "validated").
    r = client.post(
        f"{settings.API_V1_STR}/agents/{agent['id']}/draft/validate",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    draft = client.get(
        f"{settings.API_V1_STR}/agents/{agent['id']}/draft", headers=headers
    ).json()
    assert draft["validation_result"] is not None
    assert draft["validation_status"] == "validated"
    validated_revision = draft["revision"]
    # Save -> bumps revision, invalidates validated_revision.
    client.put(
        f"{settings.API_V1_STR}/agents/{agent['id']}/draft",
        json={
            "expected_revision": validated_revision,
            "system_prompt": "changed",
        },
        headers=headers,
    )
    draft = client.get(
        f"{settings.API_V1_STR}/agents/{agent['id']}/draft", headers=headers
    ).json()
    assert draft["validated_revision"] is None
    assert draft["validation_status"] == "stale"


def test_config_rejects_bypass_permissions(
    client: TestClient, superuser_token_headers, namespace_setup
):
    headers = _ns_headers(superuser_token_headers, namespace_setup)
    r = client.post(
        f"{settings.API_V1_STR}/harness-profiles",
        json={
            "name": "p1",
            "config": {"permission_mode": "bypassPermissions"},
        },
        headers=headers,
    )
    assert r.status_code == 422
    errs = r.json()["detail"]["errors"]
    assert any(e["code"] == "bypass_permissions_forbidden" for e in errs)


def test_config_rejects_denylisted_env(
    client: TestClient, superuser_token_headers, namespace_setup
):
    headers = _ns_headers(superuser_token_headers, namespace_setup)
    r = client.post(
        f"{settings.API_V1_STR}/harness-profiles",
        json={
            "name": "p2",
            "config": {"allowed_env_names": ["LD_PRELOAD"]},
        },
        headers=headers,
    )
    assert r.status_code == 422
    errs = r.json()["detail"]["errors"]
    assert any(e["code"] == "env_denied" for e in errs)


def test_config_rejects_secret_key(
    client: TestClient, superuser_token_headers, namespace_setup
):
    headers = _ns_headers(superuser_token_headers, namespace_setup)
    r = client.post(
        f"{settings.API_V1_STR}/harness-profiles",
        json={"name": "p3", "config": {"api_key": "sk-xxx"}},
        headers=headers,
    )
    assert r.status_code == 422
    errs = r.json()["detail"]["errors"]
    assert any(e["code"] == "secret_value_forbidden" for e in errs)


def test_config_rejects_shell_string(
    client: TestClient, superuser_token_headers, namespace_setup
):
    headers = _ns_headers(superuser_token_headers, namespace_setup)
    r = client.post(
        f"{settings.API_V1_STR}/harness-profiles",
        json={"name": "p4", "config": {"some_flag": "ls; rm -rf /"}},
        headers=headers,
    )
    assert r.status_code == 422
    errs = r.json()["detail"]["errors"]
    assert any(e["code"] == "shell_metachar_forbidden" for e in errs)


def test_unknown_harness_type_rejected(
    client: TestClient, superuser_token_headers, namespace_setup
):
    headers = _ns_headers(superuser_token_headers, namespace_setup)
    r = client.post(
        f"{settings.API_V1_STR}/harness-profiles",
        json={"name": "p5", "harness_type": "codex_cli"},
        headers=headers,
    )
    assert r.status_code == 422
    errs = r.json()["detail"]["errors"]
    assert any(e["code"] == "unsupported_harness" for e in errs)


def test_profile_referenced_cannot_delete(
    client: TestClient, superuser_token_headers, namespace_setup
):
    headers = _ns_headers(superuser_token_headers, namespace_setup)
    profile = client.post(
        f"{settings.API_V1_STR}/harness-profiles",
        json={"name": "pref"},
        headers=headers,
    ).json()
    agent = client.post(
        f"{settings.API_V1_STR}/agents",
        json={"slug": "a1", "name": "A1"},
        headers=headers,
    ).json()
    client.put(
        f"{settings.API_V1_STR}/agents/{agent['id']}/draft",
        json={"expected_revision": 1, "harness_profile_id": profile["id"]},
        headers=headers,
    )
    r = client.delete(
        f"{settings.API_V1_STR}/harness-profiles/{profile['id']}", headers=headers
    )
    assert r.status_code == 409


def test_cross_namespace_agent_404(
    client: TestClient, superuser_token_headers
):
    # Create namespace A + agent; query from namespace B.
    r = client.post(
        f"{settings.API_V1_STR}/platform/namespaces",
        json={"name": f"nsA-{uuid.uuid4().hex[:6]}", "code": f"nsa-{uuid.uuid4().hex[:6]}", "is_active": True},
        headers=superuser_token_headers,
    )
    ns_a = r.json()["id"]
    r = client.post(
        f"{settings.API_V1_STR}/platform/namespaces",
        json={"name": f"nsB-{uuid.uuid4().hex[:6]}", "code": f"nsb-{uuid.uuid4().hex[:6]}", "is_active": True},
        headers=superuser_token_headers,
    )
    ns_b = r.json()["id"]
    agent = client.post(
        f"{settings.API_V1_STR}/agents",
        json={"slug": "cross", "name": "Cross"},
        headers=_ns_headers(superuser_token_headers, ns_a),
    ).json()
    r = client.get(
        f"{settings.API_V1_STR}/agents/{agent['id']}",
        headers=_ns_headers(superuser_token_headers, ns_b),
    )
    assert r.status_code == 404


def test_environment_catalog(
    client: TestClient, superuser_token_headers, namespace_setup
):
    headers = _ns_headers(superuser_token_headers, namespace_setup)
    r = client.get(f"{settings.API_V1_STR}/harnesses/environment-catalog", headers=headers)
    assert r.status_code == 200
    cat = r.json()
    assert "LD_PRELOAD" in cat["denylist"]
    assert "ANTHROPIC_MODEL" in cat["allowlist"]


def test_harness_catalog(
    client: TestClient, superuser_token_headers, namespace_setup
):
    headers = _ns_headers(superuser_token_headers, namespace_setup)
    r = client.get(f"{settings.API_V1_STR}/harnesses/catalog", headers=headers)
    assert r.status_code == 200
    harnesses = r.json()["harnesses"]
    assert any(h["type"] == "claude_code" and h["supported"] for h in harnesses)


def test_archived_agent_cannot_be_edited(
    client: TestClient, superuser_token_headers, namespace_setup
):
    headers = _ns_headers(superuser_token_headers, namespace_setup)
    agent = client.post(
        f"{settings.API_V1_STR}/agents",
        json={"slug": "arc", "name": "Arc"},
        headers=headers,
    ).json()
    client.patch(
        f"{settings.API_V1_STR}/agents/{agent['id']}",
        json={"status": "archived"},
        headers=headers,
    )
    r = client.put(
        f"{settings.API_V1_STR}/agents/{agent['id']}/draft",
        json={"expected_revision": 1, "system_prompt": "x"},
        headers=headers,
    )
    assert r.status_code == 409


def test_developer_cannot_write(
    client: TestClient, superuser_token_headers, namespace_setup
):
    # Create a developer user INSIDE the namespace via the namespace user-create
    # endpoint (POST /namespaces/{id}/users creates a new user with email+
    # password+role, it does NOT bind an existing user). Then log in as that
    # developer and assert read=200, write=403.
    from tests.utils.user import user_authentication_headers
    from tests.utils.utils import random_email, random_lower_string

    dev_email = random_email()
    dev_password = random_lower_string()
    r = client.post(
        f"{settings.API_V1_STR}/namespaces/{namespace_setup}/users",
        json={
            "email": dev_email,
            "password": dev_password,
            "full_name": "Dev User",
            "is_active": True,
            "role": "developer",
        },
        headers=_ns_headers(superuser_token_headers, namespace_setup),
    )
    assert r.status_code == 200, r.text
    dev_headers = _ns_headers(
        user_authentication_headers(client=client, email=dev_email, password=dev_password),
        namespace_setup,
    )
    # Read OK.
    r = client.get(f"{settings.API_V1_STR}/agents", headers=dev_headers)
    assert r.status_code == 200
    # Write forbidden (require_namespace_admin -> 403).
    r = client.post(
        f"{settings.API_V1_STR}/agents",
        json={"slug": "dev", "name": "Dev"},
        headers=dev_headers,
    )
    assert r.status_code == 403
