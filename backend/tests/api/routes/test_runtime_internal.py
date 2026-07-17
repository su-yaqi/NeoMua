import uuid

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import settings
from app.models import LlmProviderConfig, LlmProviderModel
from app.runtime.catalog import canonical_digest
from app.runtime.models import (
    AgentTask,
    AgentTaskModelUsage,
    RuntimeJob,
    RuntimeJobKind,
    RuntimeJobStatus,
    RuntimeModelBinding,
    RuntimeModelBindingStatus,
    RuntimeModelRouteType,
    RuntimeProfile,
)
from app.runtime.policy import TaskStatus
from app.runtime.security import expected_internal_token
from tests.api.routes.test_agent_tasks import ready_runtime
from tests.api.routes.test_namespaces import create_namespace, namespace_headers


def _internal_headers() -> dict[str, str]:
    return {"X-Runtime-Token": expected_internal_token()}


def _create_task(
    client: TestClient,
    headers: dict[str, str],
    *,
    runtime_instance_id: uuid.UUID,
    runtime_model_binding_id: uuid.UUID,
    idempotency_key: str,
) -> dict:
    response = client.post(
        f"{settings.API_V1_STR}/runtime-tasks",
        headers={**headers, "Idempotency-Key": idempotency_key},
        json={
            "runtime_instance_id": str(runtime_instance_id),
            "runtime_model_binding_id": str(runtime_model_binding_id),
            "model_selection_mode": "exact",
            "prompt": "Run the v0.9 task",
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


def test_internal_endpoint_rejects_user_jwt(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    response = client.post(
        f"{settings.API_V1_STR}/internal/runtime/events",
        headers=superuser_token_headers,
        json={"task_id": "00000000-0000-0000-0000-000000000000", "events": []},
    )
    assert response.status_code == 403


def test_legacy_harness_capability_write_is_gone(
    client: TestClient,
) -> None:
    response = client.post(
        f"{settings.API_V1_STR}/internal/runtime/capabilities",
        headers=_internal_headers(),
        json={"worker_id": "worker-1", "harness_capabilities": {}},
    )
    assert response.status_code == 410
    assert "RuntimeInstance" in response.text


def test_worker_claims_v09_task_and_records_model_preparation_evidence(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
) -> None:
    namespace = create_namespace(db)
    runtime, binding = ready_runtime(db, namespace.id)
    task = _create_task(
        client,
        namespace_headers(superuser_token_headers, namespace.id),
        runtime_instance_id=runtime.id,
        runtime_model_binding_id=binding.id,
        idempotency_key="v09-worker-claim",
    )

    claimed = client.post(
        f"{settings.API_V1_STR}/internal/runtime/tasks/claim",
        headers=_internal_headers(),
        json={"worker_id": "platform-worker-1"},
    )
    assert claimed.status_code == 200, claimed.text
    claim = claimed.json()
    assert claim["task_id"] == task["id"]
    assert claim["requires_model_preparation"] is True
    assert claim["command"]["engine_type"] == "claude_code"
    assert claim["command"]["model"] == binding.engine_model_id

    prepared = client.post(
        f"{settings.API_V1_STR}/internal/runtime/tasks/{task['id']}/model-prepared",
        headers=_internal_headers(),
        json={
            "worker_id": "platform-worker-1",
            "revision": claim["revision"],
            **claim["model_preparation"],
        },
    )
    assert prepared.status_code == 200, prepared.text
    assert prepared.json() == {"route_type": "runtime_native", "environment": {}}

    db.expire_all()
    usage = db.exec(
        select(AgentTaskModelUsage).where(
            AgentTaskModelUsage.task_id == uuid.UUID(task["id"])
        )
    ).one()
    assert usage.evidenced_at is not None
    assert usage.runtime_evidence == claim["model_preparation"]


def test_gateway_resolves_only_evidenced_v09_provider_binding(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
) -> None:
    namespace = create_namespace(db)
    headers = namespace_headers(superuser_token_headers, namespace.id)
    provider_response = client.post(
        f"{settings.API_V1_STR}/llm/provider-configs",
        headers=headers,
        json={
            "config_name": f"anthropic-{uuid.uuid4().hex}",
            "provider_slug": "anthropic",
            "base_url": "https://api.anthropic.test",
            "enabled": True,
            "secret_inputs": {"api_token": "secret-value"},
            "extra_config": {},
            "manual_models": [
                {"model_id": "claude-v09-test", "display_name": "Claude v0.9"}
            ],
            "enabled_model_ids": ["claude-v09-test"],
        },
    )
    assert provider_response.status_code == 200, provider_response.text
    provider = db.get(LlmProviderConfig, uuid.UUID(provider_response.json()["id"]))
    assert provider is not None
    provider_model = db.exec(
        select(LlmProviderModel).where(
            LlmProviderModel.provider_config_id == provider.id,
            LlmProviderModel.model_id == "claude-v09-test",
        )
    ).one()
    assert provider_model.model_definition_id is not None

    runtime, native_binding = ready_runtime(db, namespace.id)
    provider_binding = RuntimeModelBinding(
        namespace_id=namespace.id,
        runtime_instance_id=runtime.id,
        model_definition_id=provider_model.model_definition_id,
        provider_config_id=provider.id,
        provider_model_id=provider_model.id,
        route_type=RuntimeModelRouteType.PROVIDER_CONFIG,
        route_key=str(provider.id),
        engine_model_id=provider_model.model_id,
        status=RuntimeModelBindingStatus.AVAILABLE,
        validation_fingerprint=canonical_digest({"provider": str(provider.id)}),
    )
    db.add(provider_binding)
    db.delete(native_binding)
    db.commit()
    db.refresh(provider_binding)

    task = _create_task(
        client,
        headers,
        runtime_instance_id=runtime.id,
        runtime_model_binding_id=provider_binding.id,
        idempotency_key="v09-provider-route",
    )
    claimed = client.post(
        f"{settings.API_V1_STR}/internal/runtime/tasks/claim",
        headers=_internal_headers(),
        json={"worker_id": "platform-worker-gateway"},
    )
    assert claimed.status_code == 200, claimed.text
    claim = claimed.json()
    prepared = client.post(
        f"{settings.API_V1_STR}/internal/runtime/tasks/{task['id']}/model-prepared",
        headers=_internal_headers(),
        json={
            "worker_id": "platform-worker-gateway",
            "revision": claim["revision"],
            **claim["model_preparation"],
        },
    )
    assert prepared.status_code == 200, prepared.text
    environment = prepared.json()["environment"]
    route = client.get(
        f"{settings.API_V1_STR}/internal/runtime/routes/{runtime.id}/tasks/{task['id']}",
        params={"model_id": provider_binding.engine_model_id},
        headers={
            **_internal_headers(),
            "Authorization": f"Bearer {environment['ANTHROPIC_API_KEY']}",
        },
    )
    assert route.status_code == 200, route.text
    assert route.json()["secret_inputs"] == {"api_token": "secret-value"}


def test_platform_runtime_job_enforces_lease_revision_and_owner(
    client: TestClient,
    db: Session,
) -> None:
    namespace = create_namespace(db)
    runtime, _ = ready_runtime(db, namespace.id)
    job = RuntimeJob(
        namespace_id=namespace.id,
        runtime_instance_id=runtime.id,
        kind=RuntimeJobKind.WORKFLOW_VALIDATOR,
        payload={"component_key": "project_delivery.has_clarification"},
        idempotency_key=f"test-runtime-job:{uuid.uuid4()}",
    )
    db.add(job)
    db.commit()
    claimed = client.post(
        f"{settings.API_V1_STR}/internal/runtime/jobs/claim",
        headers=_internal_headers(),
        json={"worker_id": "runtime-worker-a"},
    )
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["job_id"] == str(job.id)
    wrong_owner = client.post(
        f"{settings.API_V1_STR}/internal/runtime/jobs/{job.id}/result",
        headers=_internal_headers(),
        json={
            "worker_id": "runtime-worker-b",
            "revision": job.revision,
            "status": "succeeded",
            "result": {"passed": True, "details": {}},
        },
    )
    assert wrong_owner.status_code == 409
    lease = client.post(
        f"{settings.API_V1_STR}/internal/runtime/jobs/{job.id}/lease",
        headers=_internal_headers(),
        json={"worker_id": "runtime-worker-a", "revision": job.revision},
    )
    assert lease.status_code == 200
    completed = client.post(
        f"{settings.API_V1_STR}/internal/runtime/jobs/{job.id}/result",
        headers=_internal_headers(),
        json={
            "worker_id": "runtime-worker-a",
            "revision": job.revision,
            "status": "succeeded",
            "result": {"passed": True, "details": {"proof": "runtime"}},
        },
    )
    assert completed.status_code == 200
    db.expire_all()
    persisted = db.get(RuntimeJob, job.id)
    assert persisted is not None
    assert persisted.status == RuntimeJobStatus.SUCCEEDED


def test_legacy_runtime_profile_task_is_not_claimed(
    client: TestClient,
    db: Session,
) -> None:
    namespace = create_namespace(db)
    legacy_runtime = RuntimeProfile(
        namespace_id=namespace.id,
        runtime_type="platform",
        route_mode="platform_gateway",
        model_id="legacy-model",
        config={"compatibility_verified": True},
    )
    db.add(legacy_runtime)
    db.flush()
    legacy_task = AgentTask(
        namespace_id=namespace.id,
        runtime_profile_id=legacy_runtime.id,
        prompt="legacy queued task",
        snapshot={"model_id": "legacy-model"},
        status=TaskStatus.QUEUED,
    )
    db.add(legacy_task)
    db.commit()

    response = client.post(
        f"{settings.API_V1_STR}/internal/runtime/tasks/claim",
        headers=_internal_headers(),
        json={"worker_id": "v09-worker"},
    )
    assert response.status_code == 204
    db.expire_all()
    assert db.get(AgentTask, legacy_task.id).status == TaskStatus.QUEUED
