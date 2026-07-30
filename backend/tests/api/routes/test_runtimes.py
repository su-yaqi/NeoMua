import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import settings
from app.runtime.models import (
    NodeInstallationReceipt,
    RuntimeCapabilityReport,
    RuntimeConfigurationOrigin,
    RuntimeConfigurationRevision,
    RuntimeConfigurationStatus,
    RuntimeDiscoveryObservation,
    RuntimeEngineType,
    RuntimeInstance,
    RuntimeInstanceStatus,
    RuntimeLocationType,
    RuntimeManagementType,
    RuntimeModelBinding,
    RuntimeModelBindingStatus,
    RuntimeNode,
    RuntimeNodeMode,
)
from app.runtime.platform_builtin import (
    process_platform_reconcile_jobs,
    reconcile_platform_model_bindings,
)
from app.runtime.security import expected_internal_token
from tests.api.routes.test_namespaces import create_namespace, namespace_headers


def test_platform_runtime_is_built_in_and_requires_no_runtime_configuration(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
) -> None:
    namespace = create_namespace(db)
    headers = namespace_headers(superuser_token_headers, namespace.id)
    response = client.get(f"{settings.API_V1_STR}/runtimes", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 1
    runtime = response.json()["data"][0]
    assert runtime["location_type"] == "platform"
    assert runtime["management_type"] == "platform_builtin"
    assert runtime["engine_type"] == "claude_agent_sdk"
    assert runtime["enabled"] is True
    assert runtime["status"] == "discovered"
    assert runtime["current_capability_report_id"] is None

    detail = client.get(
        f"{settings.API_V1_STR}/runtimes/{runtime['id']}", headers=headers
    )
    assert detail.status_code == 200
    configuration = detail.json()["configurations"][0]
    assert configuration["status"] == "desired"
    assert configuration["origin"] == "system_builtin"
    assert configuration["adapter_execution_ref"] == "platform:claude-agent-sdk"

    repeated = client.get(f"{settings.API_V1_STR}/runtimes", headers=headers)
    assert repeated.status_code == 200
    assert repeated.json()["count"] == 1


def test_platform_runtime_lifecycle_is_not_user_mutable(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
) -> None:
    namespace = create_namespace(db)
    headers = namespace_headers(superuser_token_headers, namespace.id)
    listed = client.get(f"{settings.API_V1_STR}/runtimes", headers=headers).json()
    runtime = listed["data"][0]
    created = client.post(
        f"{settings.API_V1_STR}/runtimes/platform",
        headers={**headers, "Idempotency-Key": "platform-runtime-v10-forbidden"},
        json={
            "name": "User-created platform runtime",
            "installation_key": f"claude-{uuid.uuid4().hex}",
            "engine_type": "claude_code",
            "configuration": {"expected_revision": 0, "executable": "claude"},
        },
    )
    assert created.status_code == 410
    assert created.json()["detail"] == "platform_runtime_system_managed"

    update = client.put(
        f"{settings.API_V1_STR}/runtimes/{runtime['id']}/configuration",
        headers=headers,
        json={"expected_revision": 1, "executable": "claude"},
    )
    assert update.status_code == 409
    assert update.json()["detail"] == "runtime_configuration_system_managed"


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


def test_validated_platform_model_route_becomes_available_without_runtime_config(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
    monkeypatch,
) -> None:
    namespace = create_namespace(db)
    headers = namespace_headers(superuser_token_headers, namespace.id)

    class FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, list[dict[str, str]]]:
            return {"data": []}

    monkeypatch.setattr("httpx.get", lambda *args, **kwargs: FakeResponse())
    exact_calls: list[str] = []

    def fake_post(url: str, **_kwargs):
        exact_calls.append(url)
        return FakeResponse()

    monkeypatch.setattr("httpx.post", fake_post)
    provider = client.post(
        f"{settings.API_V1_STR}/llm/provider-configs",
        headers=headers,
        json={
            "config_name": "platform-anthropic",
            "provider_slug": "anthropic",
            "base_url": "https://api.anthropic.test/v1",
            "enabled": True,
            "secret_inputs": {"api_token": "sk-platform-test"},
            "extra_config": {},
            "manual_models": [
                {"model_id": "claude-platform", "display_name": "Claude Platform"}
            ],
            "enabled_model_ids": ["claude-platform"],
            "validate_on_create": True,
        },
    )
    assert provider.status_code == 200, provider.text
    assert provider.json()["validation_status"] == "success"

    runtime = client.get(f"{settings.API_V1_STR}/runtimes", headers=headers).json()[
        "data"
    ][0]
    bindings = client.get(
        f"{settings.API_V1_STR}/runtimes/{runtime['id']}/model-bindings",
        headers=headers,
    ).json()["data"]
    assert bindings[0]["origin"] == "llm_config_reconciled"
    assert bindings[0]["status"] == "declared"
    assert bindings[0]["last_error"]["code"] == "provider_model_route_not_validated"
    assert exact_calls == []
    readiness = client.get(
        f"{settings.API_V1_STR}/llm/provider-configs/{provider.json()['id']}/runtime-readiness",
        headers=headers,
    )
    assert readiness.status_code == 200, readiness.text
    assert readiness.json()["status"] == "reconciling"
    assert readiness.json()["models"][0]["validation_status"] == "not_started"

    internal_headers = {"X-Runtime-Token": expected_internal_token()}
    claimed = client.post(
        f"{settings.API_V1_STR}/internal/runtime/configurations/claim",
        headers=internal_headers,
        json={"worker_id": "platform-worker-v10"},
    )
    assert claimed.status_code == 200, claimed.text
    claim = claimed.json()
    applied = client.post(
        f"{settings.API_V1_STR}/internal/runtime/configurations/result",
        headers=internal_headers,
        json={
            "worker_id": "platform-worker-v10",
            "runtime_instance_id": claim["runtime_instance_id"],
            "configuration_revision_id": claim["configuration_revision_id"],
            "configuration_digest": claim["configuration_digest"],
            "status": "applied",
            "engine_version": "2.1.191",
            "adapter_version": "1.0.0",
            "capabilities": {
                "tools": ["Read", "Edit", "Bash"],
                "release_digest": settings.RUNTIME_WORKER_RELEASE_DIGEST,
            },
            "discovered_models": [],
        },
    )
    assert applied.status_code == 200, applied.text
    db.expire_all()
    assert process_platform_reconcile_jobs(db) >= 1
    db.commit()
    assert exact_calls == ["https://api.anthropic.test/v1/messages"]

    available = client.get(
        f"{settings.API_V1_STR}/runtimes/{runtime['id']}/model-bindings",
        headers=headers,
    ).json()["data"][0]
    assert available["status"] == "available"
    assert available["validation_fingerprint"]
    assert available["validated_capability_fingerprint"]

    readiness = client.get(
        f"{settings.API_V1_STR}/llm/provider-configs/{provider.json()['id']}/runtime-readiness",
        headers=headers,
    )
    assert readiness.status_code == 200, readiness.text
    readiness_body = readiness.json()
    assert readiness_body["status"] == "ready"
    assert readiness_body["release_trusted"] is True
    assert readiness_body["reconcile_status"] == "succeeded"
    assert readiness_body["models"][0]["validation_status"] == "succeeded"
    assert readiness_body["models"][0]["ready"] is True

    now = datetime.now(timezone.utc)
    service_node = RuntimeNode(
        namespace_id=namespace.id,
        name="service-binding-node",
        hostname="service-binding-host",
        os_name="linux",
        architecture="amd64",
        agent_version="0.1.0",
        harness_capabilities={},
        public_key="test",
        key_fingerprint=uuid.uuid4().hex,
        management_mode=RuntimeNodeMode.SERVICE,
        connection_id=uuid.uuid4(),
        last_seen_at=now,
    )
    db.add(service_node)
    db.flush()
    receipt = NodeInstallationReceipt(
        node_id=service_node.id,
        receipt_digest="1" * 64,
        manifest_digest="2" * 64,
        components=[],
        logical_installation_ref="service:claude-agent-sdk",
        device_signature="test",
        first_applied_at=now,
        last_verified_at=now,
    )
    db.add(receipt)
    db.flush()
    service_node.current_installation_receipt_id = receipt.id
    service_runtime = RuntimeInstance(
        namespace_id=namespace.id,
        runtime_node_id=service_node.id,
        location_type=RuntimeLocationType.NODE,
        management_type=RuntimeManagementType.SERVICE_MANAGED,
        lifecycle_source_key=f"{service_node.id}:service:claude-agent-sdk",
        name="Service Claude Agent SDK",
        installation_key="service:claude-agent-sdk",
        engine_type=RuntimeEngineType.CLAUDE_AGENT_SDK,
        engine_version="2.1.191",
        adapter_version="1.0.0",
        status=RuntimeInstanceStatus.UNAVAILABLE,
        enabled=True,
    )
    db.add(service_runtime)
    db.flush()
    configuration = RuntimeConfigurationRevision(
        runtime_instance_id=service_runtime.id,
        revision=1,
        origin=RuntimeConfigurationOrigin.SERVICE_MANIFEST,
        adapter_execution_ref="service:claude-agent-sdk",
        executable="claude",
        arguments=[],
        working_directory_policy="workspace",
        environment_allowlist=[],
        security_policy={"permission_modes": ["default", "plan"]},
        resource_limits={"max_timeout_seconds": 3600},
        configuration_digest="3" * 64,
        status=RuntimeConfigurationStatus.APPLIED,
        applied_at=now,
    )
    db.add(configuration)
    db.flush()
    capability = RuntimeCapabilityReport(
        runtime_instance_id=service_runtime.id,
        generation=1,
        engine_version="2.1.191",
        adapter_version="1.0.0",
        configuration_digest=configuration.configuration_digest,
        capabilities={"distribution_manifest_digest": receipt.manifest_digest},
        discovered_models=[],
        capability_fingerprint="4" * 64,
        reported_at=now,
    )
    db.add(capability)
    db.flush()
    observation = RuntimeDiscoveryObservation(
        node_id=service_node.id,
        runtime_instance_id=service_runtime.id,
        generation=1,
        installation_key=service_runtime.installation_key,
        status="available",
        evidence={},
        evidence_digest="5" * 64,
    )
    db.add(observation)
    db.flush()
    service_runtime.desired_configuration_revision_id = configuration.id
    service_runtime.applied_configuration_revision_id = configuration.id
    service_runtime.current_capability_report_id = capability.id
    service_runtime.current_discovery_observation_id = observation.id
    db.add(service_node)
    db.add(service_runtime)
    reconcile_platform_model_bindings(db, namespace.id)
    db.commit()

    service_binding = db.exec(
        select(RuntimeModelBinding).where(
            RuntimeModelBinding.runtime_instance_id == service_runtime.id
        )
    ).one()
    assert service_binding.status == RuntimeModelBindingStatus.AVAILABLE
    assert service_binding.validated_capability_fingerprint == "4" * 64
    db.refresh(service_runtime)
    assert service_runtime.status == RuntimeInstanceStatus.AVAILABLE

    paused = client.post(
        f"{settings.API_V1_STR}/runtimes/{service_runtime.id}/pause",
        headers=headers,
    )
    assert paused.status_code == 200, paused.text
    assert paused.json()["enabled"] is False
    resumed = client.post(
        f"{settings.API_V1_STR}/runtimes/{service_runtime.id}/resume",
        headers=headers,
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["enabled"] is True

    validation_fingerprint = available["validation_fingerprint"]
    for _ in range(2):
        repeated = client.get(f"{settings.API_V1_STR}/runtimes", headers=headers)
        assert repeated.status_code == 200, repeated.text
        repeated_binding = client.get(
            f"{settings.API_V1_STR}/runtimes/{runtime['id']}/model-bindings",
            headers=headers,
        ).json()["data"][0]
        assert repeated_binding["validation_fingerprint"] == validation_fingerprint

    replaced = client.patch(
        f"{settings.API_V1_STR}/llm/provider-configs/{provider.json()['id']}",
        headers=headers,
        json={
            "enabled": True,
            "secret_inputs": {"api_token": "sk-platform-replaced"},
        },
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["enabled"] is False
    assert replaced.json()["validation_status"] == "unverified"
    assert replaced.json()["last_validated_at"] is None

    invalidated = client.get(
        f"{settings.API_V1_STR}/runtimes/{runtime['id']}/model-bindings",
        headers=headers,
    ).json()["data"][0]
    assert invalidated["status"] == "declared"
    assert invalidated["validation_fingerprint"] is None
    assert invalidated["last_error"]["code"] == "provider_model_not_validated"
