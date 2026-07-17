from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import settings
from app.runtime.security import expected_internal_token
from tests.api.routes.test_agent_tasks import ready_runtime
from tests.api.routes.test_namespaces import create_namespace, namespace_headers


def test_create_complete_agent_declares_stable_model_preference(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
) -> None:
    namespace = create_namespace(db)
    _runtime, binding = ready_runtime(db, namespace.id)
    headers = namespace_headers(superuser_token_headers, namespace.id)
    response = client.post(
        f"{settings.API_V1_STR}/agents/complete",
        headers=headers,
        json={
            "slug": "v09-agent",
            "name": "v0.9 Agent",
            "preferred_model_definition_id": str(binding.model_definition_id),
            "execution_policy": {"permission_mode": "default"},
            "system_prompt": "Work carefully and report evidence.",
            "config": {},
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["draft"]["preferred_model_definition_id"] == str(
        binding.model_definition_id
    )
    assert body["draft"]["harness_profile_id"] is None
    assert body["draft"]["provider_config_id"] is None
    assert body["draft"]["model_id"] is None

    validation = client.post(
        f"{settings.API_V1_STR}/agents/{body['agent']['id']}/draft/validate",
        headers=headers,
    )
    assert validation.status_code == 200, validation.text
    assert validation.json()["status"] == "validated"
    release = client.post(
        f"{settings.API_V1_STR}/agents/{body['agent']['id']}/releases",
        headers={**headers, "Idempotency-Key": "v09-agent-release"},
        json={"draft_revision": 1, "version": "1.0.0"},
    )
    assert release.status_code == 201, release.text
    release_body = release.json()
    assert release_body["resolved_spec_schema_version"] == "2.0"

    precheck = client.post(
        f"{settings.API_V1_STR}/agent-releases/{release_body['id']}/activations/precheck",
        headers=headers,
        json={"runtime_instance_id": str(_runtime.id)},
    )
    assert precheck.status_code == 200, precheck.text
    precheck_body = precheck.json()
    assert precheck_body["deployable"] is True
    assert precheck_body["preference_status"] == "available"

    activation = client.post(
        f"{settings.API_V1_STR}/agent-releases/{release_body['id']}/activations",
        headers={**headers, "Idempotency-Key": "v09-agent-activation"},
        json={
            "runtime_instance_id": str(_runtime.id),
            "precheck_id": precheck_body["id"],
            "precheck_digest": precheck_body["precheck_digest"],
        },
    )
    assert activation.status_code == 202, activation.text
    assert activation.json()["runtime_instance_id"] == str(_runtime.id)

    internal_headers = {"X-Runtime-Token": expected_internal_token()}
    deployment = client.post(
        f"{settings.API_V1_STR}/internal/runtime/agent-deployments/claim",
        headers=internal_headers,
    )
    assert deployment.status_code == 200, deployment.text
    deployment_body = deployment.json()
    applied = client.post(
        f"{settings.API_V1_STR}/internal/runtime/agent-deployments/{deployment_body['deployment_id']}/result",
        headers=internal_headers,
        json={
            "status": "applied",
            "resolved_spec_digest": release_body["resolved_spec_digest"],
            "materialization_digest": deployment_body["materialization"][
                "resolved_spec_digest"
            ],
            "capability_fingerprint": deployment_body["capability_fingerprint"],
        },
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["status"] == "applied"

    runtime_agents = client.get(
        f"{settings.API_V1_STR}/runtime-agents", headers=headers
    )
    assert runtime_agents.status_code == 200, runtime_agents.text
    active = runtime_agents.json()["data"]
    assert len(active) == 1
    assert active[0]["runtime_instance_id"] == str(_runtime.id)
    assert active[0]["current_release_id"] == release_body["id"]


def test_agent_copy_preserves_preference_but_not_legacy_harness_fields(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
) -> None:
    namespace = create_namespace(db)
    _runtime, binding = ready_runtime(db, namespace.id)
    headers = namespace_headers(superuser_token_headers, namespace.id)
    source = client.post(
        f"{settings.API_V1_STR}/agents/complete",
        headers=headers,
        json={
            "slug": "copy-source-v09",
            "name": "Source",
            "preferred_model_definition_id": str(binding.model_definition_id),
            "system_prompt": "Source prompt",
        },
    ).json()["agent"]
    copied = client.post(
        f"{settings.API_V1_STR}/agents/{source['id']}/copy",
        headers=headers,
        json={"slug": "copy-target-v09", "name": "Target"},
    )
    assert copied.status_code == 201, copied.text
    draft = client.get(
        f"{settings.API_V1_STR}/agents/{copied.json()['id']}/draft",
        headers=headers,
    ).json()
    assert draft["preferred_model_definition_id"] == str(binding.model_definition_id)
    assert draft["harness_profile_id"] is None


def test_legacy_agent_and_harness_creation_are_read_only(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
) -> None:
    namespace = create_namespace(db)
    headers = namespace_headers(superuser_token_headers, namespace.id)
    assert (
        client.post(
            f"{settings.API_V1_STR}/agents",
            headers=headers,
            json={"slug": "legacy-agent", "name": "Legacy"},
        ).status_code
        == 410
    )
    assert (
        client.post(
            f"{settings.API_V1_STR}/harness-profiles",
            headers=headers,
            json={"name": "legacy-harness"},
        ).status_code
        == 410
    )


def test_complete_agent_rejects_legacy_execution_fields(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
) -> None:
    namespace = create_namespace(db)
    _runtime, binding = ready_runtime(db, namespace.id)
    response = client.post(
        f"{settings.API_V1_STR}/agents/complete",
        headers=namespace_headers(superuser_token_headers, namespace.id),
        json={
            "slug": "invalid-v09-agent",
            "name": "Invalid",
            "preferred_model_definition_id": str(binding.model_definition_id),
            "harness_profile_id": "00000000-0000-0000-0000-000000000001",
        },
    )
    assert response.status_code == 422
