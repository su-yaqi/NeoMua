import uuid

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import crud
from app.core.config import settings
from app.models import LlmModelDefinition, NamespaceRole
from app.runtime.catalog import canonical_digest
from app.runtime.models import (
    AgentEvent,
    AgentEventType,
    RuntimeCapabilityReport,
    RuntimeConfigurationRevision,
    RuntimeConfigurationStatus,
    RuntimeEngineType,
    RuntimeInstance,
    RuntimeInstanceStatus,
    RuntimeLocationType,
    RuntimeModelBinding,
    RuntimeModelBindingStatus,
    RuntimeModelRouteType,
)
from app.runtime.repository import append_and_apply_event
from tests.api.routes.test_namespaces import create_namespace, namespace_headers
from tests.utils.user import authentication_token_from_email, create_random_user


def ready_runtime(db: Session, namespace_id: uuid.UUID) -> tuple[RuntimeInstance, RuntimeModelBinding]:
    definition = LlmModelDefinition(
        namespace_id=namespace_id,
        provider_family="anthropic",
        model_key=f"model-{uuid.uuid4().hex}",
        display_name="Stable model",
    )
    runtime = RuntimeInstance(
        namespace_id=namespace_id,
        location_type=RuntimeLocationType.PLATFORM,
        name="Platform Claude",
        installation_key=f"platform-{uuid.uuid4().hex}",
        engine_type=RuntimeEngineType.CLAUDE_CODE,
        engine_version="1.0.0",
        adapter_version="1.0.0",
        status=RuntimeInstanceStatus.AVAILABLE,
        enabled=True,
    )
    db.add_all([definition, runtime])
    db.flush()
    configuration = RuntimeConfigurationRevision(
        runtime_instance_id=runtime.id,
        revision=1,
        executable="claude",
        working_directory_policy="workspace",
        security_policy={"permission_modes": ["default"]},
        configuration_digest=canonical_digest({"runtime": str(runtime.id)}),
        status=RuntimeConfigurationStatus.APPLIED,
    )
    db.add(configuration)
    db.flush()
    capability = RuntimeCapabilityReport(
        runtime_instance_id=runtime.id,
        generation=1,
        engine_version="1.0.0",
        adapter_version="1.0.0",
        configuration_digest=configuration.configuration_digest,
        capabilities={"tools": []},
        discovered_models=[],
        capability_fingerprint=canonical_digest({"capability": str(runtime.id)}),
    )
    binding = RuntimeModelBinding(
        namespace_id=namespace_id,
        runtime_instance_id=runtime.id,
        model_definition_id=definition.id,
        route_type=RuntimeModelRouteType.RUNTIME_NATIVE,
        route_key="native",
        engine_model_id=definition.model_key,
        status=RuntimeModelBindingStatus.AVAILABLE,
        validation_fingerprint=canonical_digest({"binding": str(runtime.id)}),
    )
    db.add_all([capability, binding])
    db.flush()
    runtime.desired_configuration_revision_id = configuration.id
    runtime.applied_configuration_revision_id = configuration.id
    runtime.current_capability_report_id = capability.id
    db.add(runtime)
    db.commit()
    return runtime, binding


def test_v09_task_freezes_exact_model_and_emits_binding_evidence(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
) -> None:
    namespace = create_namespace(db)
    runtime, binding = ready_runtime(db, namespace.id)
    response = client.post(
        f"{settings.API_V1_STR}/runtime-tasks",
        headers={
            **namespace_headers(superuser_token_headers, namespace.id),
            "Idempotency-Key": "v09-exact-task",
        },
        json={
            "runtime_instance_id": str(runtime.id),
            "runtime_model_binding_id": str(binding.id),
            "model_selection_mode": "exact",
            "prompt": "Inspect the repository",
        },
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["snapshot"]["runtime_model_binding_id"] == str(binding.id)
    assert body["model_usage"]["runtime_model_binding_id"] == str(binding.id)

    task_id = uuid.UUID(body["id"])
    append_and_apply_event(
        db,
        task_id,
        1,
        AgentEventType.ASSISTANT_MESSAGE,
        {"text": "Done"},
    )
    db.commit()
    event = db.exec(
        select(AgentEvent).where(AgentEvent.task_id == task_id, AgentEvent.sequence == 1)
    ).one()
    assert event.payload["model_execution"]["runtime_model_binding_id"] == str(binding.id)


def test_runtime_profile_task_write_is_rejected(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
) -> None:
    namespace = create_namespace(db)
    response = client.post(
        f"{settings.API_V1_STR}/runtime-tasks",
        headers={
            **namespace_headers(superuser_token_headers, namespace.id),
            "Idempotency-Key": "legacy-task",
        },
        json={
            "runtime_profile_id": str(uuid.uuid4()),
            "runtime_agent_release_id": str(uuid.uuid4()),
            "prompt": "Legacy",
        },
    )
    assert response.status_code == 422
    assert "read-only" in response.text


def test_developer_cannot_dispatch_admin_v09_task(
    client: TestClient,
    db: Session,
) -> None:
    namespace = create_namespace(db)
    runtime, binding = ready_runtime(db, namespace.id)
    developer = create_random_user(db)
    crud.ensure_namespace_membership(
        session=db,
        user_id=developer.id,
        namespace_id=namespace.id,
        role=NamespaceRole.DEVELOPER,
    )
    headers = namespace_headers(
        authentication_token_from_email(client=client, email=developer.email, db=db),
        namespace.id,
    )
    response = client.post(
        f"{settings.API_V1_STR}/runtime-tasks",
        headers={**headers, "Idempotency-Key": "v09-admin-task"},
        json={
            "runtime_instance_id": str(runtime.id),
            "runtime_model_binding_id": str(binding.id),
            "model_selection_mode": "exact",
            "prompt": "Admin operation",
            "task_kind": "admin",
        },
    )
    assert response.status_code == 403
