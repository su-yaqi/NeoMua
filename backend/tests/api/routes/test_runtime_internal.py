import uuid

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.conversation_management.models import (
    ConversationAgent,
    ConversationAgentRole,
    ConversationMessage,
    MessageTargetType,
)
from app.core.config import settings
from app.runtime.models import (
    AgentTask,
    RuntimeJob,
    RuntimeJobKind,
    RuntimeJobStatus,
    RuntimeProfile,
)
from app.runtime.policy import TaskStatus
from app.runtime.security import expected_internal_token, issue_gateway_token
from tests.api.routes.test_namespaces import create_namespace, namespace_headers
from tests.utils.agent_release import create_active_agent_binding


def test_internal_endpoint_rejects_user_jwt(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    response = client.post(
        f"{settings.API_V1_STR}/internal/runtime/events",
        headers=superuser_token_headers,
        json={"task_id": "00000000-0000-0000-0000-000000000000", "events": []},
    )
    assert response.status_code == 403


def test_runtime_worker_reports_platform_harness_capabilities(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
) -> None:
    namespace = create_namespace(db)
    created = client.put(
        f"{settings.API_V1_STR}/runtimes/platform",
        headers=namespace_headers(superuser_token_headers, namespace.id),
        json={
            "route_mode": "direct_anthropic",
            "model_id": "claude-test",
            "base_url": "https://example.test",
            "secret_inputs": {"api_key": "secret-value"},
        },
    ).json()
    capabilities = {
        "claude_code": {
            "cli_version": "2.1.191",
            "sdk_version": "0.2.110",
            "harness_version": "0.1.0",
        }
    }
    response = client.post(
        f"{settings.API_V1_STR}/internal/runtime/capabilities",
        headers={"X-Runtime-Token": expected_internal_token()},
        json={"worker_id": "worker-1", "harness_capabilities": capabilities},
    )
    assert response.status_code == 200
    assert response.json() == {"updated": 1}
    db.expire_all()
    runtime = db.get(RuntimeProfile, uuid.UUID(created["id"]))
    assert runtime is not None
    assert runtime.harness_capabilities == capabilities


def test_gateway_can_resolve_direct_runtime_without_exposing_secret_to_browser(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
) -> None:
    namespace = create_namespace(db)
    created = client.put(
        f"{settings.API_V1_STR}/runtimes/platform",
        headers=namespace_headers(superuser_token_headers, namespace.id),
        json={
            "route_mode": "direct_anthropic",
            "model_id": "claude-test",
            "base_url": "https://example.test",
            "secret_inputs": {"api_key": "secret-value"},
        },
    ).json()
    task = AgentTask(
        namespace_id=namespace.id,
        runtime_profile_id=uuid.UUID(created["id"]),
        prompt="route probe",
        snapshot={"model_id": "claude-test"},
        status=TaskStatus.RUNNING,
    )
    db.add(task)
    db.commit()
    task_id = task.id
    token = issue_gateway_token(
        namespace.id, uuid.UUID(created["id"]), task_id, "claude-test"
    )
    response = client.get(
        f"{settings.API_V1_STR}/internal/runtime/routes/{created['id']}/tasks/{task_id}",
        params={"model_id": "claude-test"},
        headers={
            "X-Runtime-Token": expected_internal_token(),
            "Authorization": f"Bearer {token}",
        },
    )
    assert response.status_code == 200
    assert response.json()["secret_inputs"]["api_key"] == "secret-value"


def test_worker_claims_queued_platform_task(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
    monkeypatch,
) -> None:
    namespace = create_namespace(db)
    headers = namespace_headers(superuser_token_headers, namespace.id)
    runtime_response = client.put(
        f"{settings.API_V1_STR}/runtimes/platform",
        headers=headers,
        json={
            "route_mode": "direct_anthropic",
            "model_id": "claude-test",
            "base_url": "https://example.test",
            "secret_inputs": {"api_key": "secret-value"},
        },
    ).json()

    async def compatible(*_args):
        return {"type": "message"}

    monkeypatch.setattr(
        "app.api.routes.runtimes.check_anthropic_compatibility", compatible
    )
    assert (
        client.post(
            f"{settings.API_V1_STR}/runtimes/platform/validate", headers=headers
        ).status_code
        == 200
    )
    runtime = db.get(RuntimeProfile, uuid.UUID(runtime_response["id"]))
    assert runtime is not None
    binding = create_active_agent_binding(
        db,
        namespace_id=namespace.id,
        runtime_profile_id=runtime.id,
        provider_config_id=runtime.provider_config_id,
        model_id=runtime.model_id,
    )
    session_id = client.post(
        f"{settings.API_V1_STR}/runtimes/platform/sessions",
        headers=headers,
        json={"runtime_agent_release_id": str(binding.id)},
    ).json()["id"]
    task_id = client.post(
        f"{settings.API_V1_STR}/runtimes/sessions/{session_id}/messages",
        headers=headers,
        json={"prompt": "hello"},
    ).json()["id"]
    response = client.post(
        f"{settings.API_V1_STR}/internal/runtime/tasks/claim",
        headers={"X-Runtime-Token": expected_internal_token()},
        json={"worker_id": "platform-worker-1"},
    )
    assert response.status_code == 200
    assert response.json()["task_id"] == task_id
    assert response.json()["command"]["env"]["ANTHROPIC_API_KEY"] == "secret-value"
    event_response = client.post(
        f"{settings.API_V1_STR}/internal/runtime/events",
        headers={"X-Runtime-Token": expected_internal_token()},
        json={
            "task_id": task_id,
            "events": [
                {
                    "sequence": 2,
                    "event_type": "result",
                    "payload": {"result": "done", "session_id": "sdk-session-1"},
                }
            ],
        },
    )
    assert event_response.status_code == 200
    db.expire_all()
    assert db.get(AgentTask, uuid.UUID(task_id)).status == TaskStatus.SUCCEEDED
    next_task_id = client.post(
        f"{settings.API_V1_STR}/runtimes/sessions/{session_id}/messages",
        headers=headers,
        json={"prompt": "follow up"},
    ).json()["id"]
    next_claim = client.post(
        f"{settings.API_V1_STR}/internal/runtime/tasks/claim",
        headers={"X-Runtime-Token": expected_internal_token()},
        json={"worker_id": "platform-worker-1"},
    )
    assert next_claim.json()["task_id"] == next_task_id
    assert next_claim.json()["command"]["sdk_session_id"] == "sdk-session-1"


def test_platform_runtime_job_enforces_lease_revision_and_owner(
    client: TestClient,
    db: Session,
) -> None:
    namespace = create_namespace(db)
    runtime = RuntimeProfile(
        namespace_id=namespace.id,
        runtime_type="platform",
        route_mode="platform_gateway",
        model_id="job-model",
        config={"compatibility_verified": True},
    )
    db.add(runtime)
    db.flush()
    job = RuntimeJob(
        namespace_id=namespace.id,
        runtime_profile_id=runtime.id,
        kind=RuntimeJobKind.WORKFLOW_VALIDATOR,
        payload={"component_key": "project_delivery.has_clarification"},
        idempotency_key=f"test-runtime-job:{uuid.uuid4()}",
    )
    db.add(job)
    db.commit()
    internal_headers = {"X-Runtime-Token": expected_internal_token()}
    claimed = client.post(
        f"{settings.API_V1_STR}/internal/runtime/jobs/claim",
        headers=internal_headers,
        json={"worker_id": "runtime-worker-a"},
    )
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["job_id"] == str(job.id)
    wrong_owner = client.post(
        f"{settings.API_V1_STR}/internal/runtime/jobs/{job.id}/result",
        headers=internal_headers,
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
        headers=internal_headers,
        json={"worker_id": "runtime-worker-a", "revision": job.revision},
    )
    assert lease.status_code == 200
    completed = client.post(
        f"{settings.API_V1_STR}/internal/runtime/jobs/{job.id}/result",
        headers=internal_headers,
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
    assert persisted.result == {"passed": True, "details": {"proof": "runtime"}}


def test_roundtable_delegation_is_main_only_fixed_and_visible_to_main(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
) -> None:
    namespace = create_namespace(db)
    headers = namespace_headers(superuser_token_headers, namespace.id)
    runtime = RuntimeProfile(
        namespace_id=namespace.id,
        runtime_type="platform",
        route_mode="platform_gateway",
        model_id="roundtable-model",
        config={"compatibility_verified": True},
    )
    db.add(runtime)
    db.commit()
    db.refresh(runtime)
    main_binding = create_active_agent_binding(
        db,
        namespace_id=namespace.id,
        runtime_profile_id=runtime.id,
        provider_config_id=None,
        model_id=runtime.model_id,
    )
    collaborator_binding = create_active_agent_binding(
        db,
        namespace_id=namespace.id,
        runtime_profile_id=runtime.id,
        provider_config_id=None,
        model_id=runtime.model_id,
    )
    created = client.post(
        f"{settings.API_V1_STR}/conversations",
        headers={**headers, "Idempotency-Key": "roundtable-core-loop"},
        json={
            "title": "P1 roundtable",
            "mode": "agent",
            "runtime_id": str(runtime.id),
            "main_agent": {"runtime_agent_release_id": str(main_binding.id)},
            "collaborators": [
                {"runtime_agent_release_id": str(collaborator_binding.id)}
            ],
        },
    )
    assert created.status_code == 201, created.text
    conversation_id = created.json()["id"]
    participants = db.exec(
        select(ConversationAgent).where(
            ConversationAgent.conversation_id == uuid.UUID(conversation_id)
        )
    ).all()
    main = next(
        item for item in participants if item.role == ConversationAgentRole.MAIN
    )
    collaborator = next(
        item for item in participants if item.role == ConversationAgentRole.COLLABORATOR
    )
    started = client.post(
        f"{settings.API_V1_STR}/conversations/{conversation_id}/messages",
        headers={**headers, "Idempotency-Key": "roundtable-user-message-1"},
        json={"content": "请主持分析", "target_type": "main"},
    )
    assert started.status_code == 202, started.text
    source_task_id = started.json()["message"]["task_id"]
    source_task = db.get(AgentTask, uuid.UUID(source_task_id))
    assert source_task is not None
    assert source_task.snapshot["roundtable_role"] == "main"
    assert source_task.snapshot["roundtable_participants"] == [
        {
            "conversation_agent_id": str(collaborator.id),
            "agent_id": str(collaborator.agent_id),
            "agent_release_id": str(collaborator.agent_release_id),
        }
    ]
    source_task.status = TaskStatus.RUNNING
    db.add(source_task)
    db.commit()

    public_forge = client.post(
        f"{settings.API_V1_STR}/conversations/{conversation_id}/delegations",
        headers=headers,
        json={
            "source_message_id": started.json()["message"]["id"],
            "source_conversation_agent_id": str(main.id),
            "target_conversation_agent_id": str(collaborator.id),
            "content": "伪造委派",
        },
    )
    assert public_forge.status_code == 405

    internal_headers = {"X-Runtime-Token": expected_internal_token()}
    outside_fixed_set = client.post(
        f"{settings.API_V1_STR}/internal/runtime/agent-delegations",
        headers=internal_headers,
        json={
            "source_task_id": source_task_id,
            "source_task_revision": source_task.revision,
            "target_conversation_agent_id": str(uuid.uuid4()),
            "content": "越界目标",
        },
    )
    assert outside_fixed_set.status_code == 422
    delegated = client.post(
        f"{settings.API_V1_STR}/internal/runtime/agent-delegations",
        headers=internal_headers,
        json={
            "source_task_id": source_task_id,
            "source_task_revision": source_task.revision,
            "target_conversation_agent_id": str(collaborator.id),
            "content": "请给出独立结论",
        },
    )
    assert delegated.status_code == 202, delegated.text
    delegation_id = delegated.json()["id"]
    delegated_task = db.exec(
        select(AgentTask)
        .where(AgentTask.session_id == collaborator.agent_session_id)
        .order_by(AgentTask.created_at.desc())
    ).first()
    assert delegated_task is not None
    delegated_task.status = TaskStatus.RUNNING
    db.add(delegated_task)
    db.commit()
    collaborator_cannot_delegate = client.post(
        f"{settings.API_V1_STR}/internal/runtime/agent-delegations",
        headers=internal_headers,
        json={
            "source_task_id": str(delegated_task.id),
            "source_task_revision": delegated_task.revision,
            "target_conversation_agent_id": str(main.id),
            "content": "协作者试图反向委派",
        },
    )
    assert collaborator_cannot_delegate.status_code == 403
    delegated_task.status = TaskStatus.SUCCEEDED
    delegated_task.final_result = {"conclusion": "协作者完整结果"}
    db.add(delegated_task)
    db.commit()

    completed = client.get(
        f"{settings.API_V1_STR}/internal/runtime/agent-delegations/{delegation_id}",
        headers=internal_headers,
        params={
            "source_task_id": source_task_id,
            "source_task_revision": source_task.revision,
        },
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "completed"
    assert completed.json()["result"] == {"conclusion": "协作者完整结果"}
    reply = db.exec(
        select(ConversationMessage).where(
            ConversationMessage.conversation_id == uuid.UUID(conversation_id),
            ConversationMessage.target_type == MessageTargetType.MAIN,
            ConversationMessage.idempotency_key == f"delegation-reply:{delegation_id}",
        )
    ).one()
    assert reply.payload == {"conclusion": "协作者完整结果"}

    source_task.status = TaskStatus.SUCCEEDED
    source_task.final_result = {"summary": "主 Agent 已综合"}
    db.add(source_task)
    db.commit()
    reconciled_messages = client.get(
        f"{settings.API_V1_STR}/conversations/{conversation_id}/messages",
        headers=headers,
    )
    assert reconciled_messages.status_code == 200

    resumed = client.post(
        f"{settings.API_V1_STR}/conversations/{conversation_id}/messages",
        headers={**headers, "Idempotency-Key": "roundtable-user-message-2"},
        json={"content": "继续主持", "target_type": "main"},
    )
    assert resumed.status_code == 202, resumed.text
    resumed_task = db.get(AgentTask, uuid.UUID(resumed.json()["message"]["task_id"]))
    assert resumed_task is not None
    assert "协作者完整结果" in resumed_task.prompt
    events = client.get(
        f"{settings.API_V1_STR}/conversations/{conversation_id}/events",
        headers=headers,
    )
    assert events.status_code == 200, events.text
    event_types = [event["event_type"] for event in events.json()["data"]]
    assert "delegation_created" in event_types
    assert "delegation_updated" in event_types
    assert event_types.count("message_created") >= 4
