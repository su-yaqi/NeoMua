import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlmodel import col, select

from app.agent_management.capability_models import (
    AgentRelease,
    McpPlatformSecret,
    McpTargetBinding,
    McpTargetStatus,
    RuntimeAgentRelease,
)
from app.api.deps import SessionDep
from app.conversation_management.models import (
    AgentDelegation,
    Conversation,
)
from app.conversation_management.service import (
    create_runtime_delegation,
    reconcile_agent_messages,
)
from app.core.config import settings
from app.llm_provider_service import open_secret_payload
from app.models import LlmProviderConfig, LlmProviderModel
from app.runtime.gateway import UnsupportedGatewayProvider, gateway_provider_kind
from app.runtime.models import (
    AgentEventType,
    AgentSession,
    AgentTask,
    AgentTaskModelUsage,
    RuntimeCapabilityReport,
    RuntimeConfigurationRevision,
    RuntimeInstance,
    RuntimeJob,
    RuntimeJobStatus,
    RuntimeLocationType,
    RuntimeModelBinding,
    RuntimeModelBindingStatus,
    RuntimeModelRouteType,
)
from app.runtime.policy import TaskStatus
from app.runtime.repository import EventSequenceConflict, append_and_apply_event
from app.runtime.security import (
    GatewayScopeError,
    issue_gateway_token,
    require_internal_runtime,
    verify_gateway_token,
)
from app.runtime.skill_sync import (
    release_skill_blockers,
    release_skills_committing,
    release_skills_ready,
)

router = APIRouter(
    prefix="/internal/runtime",
    tags=["runtime-internal"],
    dependencies=[Depends(require_internal_runtime)],
)


class EventInput(BaseModel):
    sequence: int
    event_type: AgentEventType
    payload: dict[str, Any]


class EventBatch(BaseModel):
    task_id: uuid.UUID
    events: list[EventInput]


class ClaimInput(BaseModel):
    worker_id: str


class LeaseInput(BaseModel):
    worker_id: str
    revision: int


class RuntimeDelegationInput(BaseModel):
    source_task_id: uuid.UUID
    source_task_revision: int
    target_conversation_agent_id: uuid.UUID
    content: str


class RuntimeJobResultInput(BaseModel):
    worker_id: str
    revision: int
    status: RuntimeJobStatus
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class ModelPreparedInput(BaseModel):
    worker_id: str
    revision: int
    runtime_instance_id: uuid.UUID
    runtime_model_binding_id: uuid.UUID
    engine_type: str
    engine_version: str | None = None
    adapter_version: str
    engine_model_id: str
    route_type: str
    route_reference: str
    runtime_configuration_digest: str
    capability_fingerprint: str
    effective_spec_digest: str


@router.get("/signing-probe")
def signing_probe() -> dict[str, str]:
    now = datetime.now(timezone.utc)
    return {
        "token": jwt.encode(
            {
                "aud": "neomua-model-gateway-readiness",
                "iat": now,
                "exp": now + timedelta(seconds=30),
            },
            settings.SECRET_KEY,
            algorithm="HS256",
        )
    }


@router.post("/capabilities", status_code=410)
def report_legacy_platform_capabilities() -> None:
    raise HTTPException(
        410,
        "Harness capability reporting is read-only in v0.9; report each RuntimeInstance capability instead",
    )


@router.post("/events")
def append_events(body: EventBatch, session: SessionDep) -> dict[str, int]:
    for item in body.events:
        try:
            append_and_apply_event(
                session, body.task_id, item.sequence, item.event_type, item.payload
            )
        except LookupError as exc:
            session.rollback()
            raise HTTPException(404, "Task not found") from exc
        except EventSequenceConflict as exc:
            session.commit()
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            session.rollback()
            raise HTTPException(409, str(exc)) from exc
    session.commit()
    return {"accepted": len(body.events)}


@router.post("/agent-delegations", status_code=202)
def create_runtime_agent_delegation(
    body: RuntimeDelegationInput, session: SessionDep
) -> dict[str, Any]:
    source_task = session.get(AgentTask, body.source_task_id)
    if source_task is None:
        raise HTTPException(404, "Source Agent task not found")
    delegation = create_runtime_delegation(
        session,
        source_task,
        body.source_task_revision,
        body.target_conversation_agent_id,
        body.content,
    )
    session.commit()
    session.refresh(delegation)
    return {"id": str(delegation.id), "status": delegation.status.value}


@router.get("/agent-delegations/{delegation_id}")
def get_runtime_agent_delegation(
    delegation_id: uuid.UUID,
    source_task_id: uuid.UUID,
    source_task_revision: int,
    session: SessionDep,
) -> dict[str, Any]:
    source_task = session.get(AgentTask, source_task_id)
    delegation = session.get(AgentDelegation, delegation_id)
    if source_task is None or delegation is None:
        raise HTTPException(404, "Delegation not found")
    if source_task.revision != source_task_revision or delegation.input_payload.get(
        "source_task_id"
    ) != str(source_task.id):
        raise HTTPException(409, "Delegation task scope mismatch")
    conversation = session.get(Conversation, delegation.conversation_id)
    if conversation is None:
        raise HTTPException(404, "Conversation not found")
    reconcile_agent_messages(session, conversation)
    session.commit()
    session.refresh(delegation)
    return {
        "id": str(delegation.id),
        "status": delegation.status.value,
        "result": delegation.result_payload,
        "error": delegation.error,
        "task_id": str(delegation.task_id) if delegation.task_id else None,
    }


@router.post("/jobs/claim")
def claim_platform_runtime_job(body: ClaimInput, session: SessionDep) -> dict[str, Any]:
    job = session.exec(
        select(RuntimeJob)
        .join(
            RuntimeInstance,
            col(RuntimeJob.runtime_instance_id) == RuntimeInstance.id,
        )
        .where(
            RuntimeJob.status == RuntimeJobStatus.QUEUED,
            RuntimeInstance.location_type == RuntimeLocationType.PLATFORM,
        )
        .order_by(col(RuntimeJob.created_at))
        .with_for_update(skip_locked=True)
    ).first()
    if job is None:
        raise HTTPException(204)
    job.status = RuntimeJobStatus.DISPATCHED
    job.claimed_by = body.worker_id
    job.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    job.updated_at = datetime.now(timezone.utc)
    session.add(job)
    session.commit()
    return {
        "job_id": str(job.id),
        "revision": job.revision,
        "kind": job.kind.value,
        "payload": job.payload,
        "side_effecting": job.side_effecting,
    }


@router.post("/jobs/{job_id}/lease")
def renew_platform_runtime_job_lease(
    job_id: uuid.UUID, body: LeaseInput, session: SessionDep
) -> dict[str, str]:
    job = session.exec(
        select(RuntimeJob).where(RuntimeJob.id == job_id).with_for_update()
    ).first()
    if (
        job is None
        or job.claimed_by != body.worker_id
        or job.revision != body.revision
        or job.status not in {RuntimeJobStatus.DISPATCHED, RuntimeJobStatus.RUNNING}
    ):
        raise HTTPException(409, "Runtime job lease scope mismatch")
    job.status = RuntimeJobStatus.RUNNING
    job.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    job.updated_at = datetime.now(timezone.utc)
    session.add(job)
    session.commit()
    return {"status": job.status.value}


@router.post("/jobs/{job_id}/result")
def complete_platform_runtime_job(
    job_id: uuid.UUID, body: RuntimeJobResultInput, session: SessionDep
) -> dict[str, str]:
    job = session.exec(
        select(RuntimeJob).where(RuntimeJob.id == job_id).with_for_update()
    ).first()
    if (
        job is None
        or job.claimed_by != body.worker_id
        or job.revision != body.revision
        or job.status not in {RuntimeJobStatus.DISPATCHED, RuntimeJobStatus.RUNNING}
        or body.status
        not in {
            RuntimeJobStatus.SUCCEEDED,
            RuntimeJobStatus.FAILED,
            RuntimeJobStatus.NEEDS_MANUAL_RESOLUTION,
        }
    ):
        raise HTTPException(409, "Runtime job result scope mismatch")
    if body.status == RuntimeJobStatus.SUCCEEDED and body.result is None:
        raise HTTPException(422, "Successful Runtime job requires a result")
    if body.status != RuntimeJobStatus.SUCCEEDED and body.error is None:
        raise HTTPException(422, "Failed Runtime job requires an error")
    job.status = body.status
    job.result = body.result
    job.error = body.error
    job.completed_at = datetime.now(timezone.utc)
    job.updated_at = job.completed_at
    job.lease_expires_at = None
    session.add(job)
    session.commit()
    return {"status": job.status.value}


@router.post("/tasks/claim")
def claim_platform_task(body: ClaimInput, session: SessionDep) -> dict[str, Any]:
    v09_task = session.exec(
        select(AgentTask)
        .join(
            RuntimeInstance,
            col(AgentTask.runtime_instance_id) == RuntimeInstance.id,
        )
        .where(
            AgentTask.status == TaskStatus.QUEUED,
            RuntimeInstance.location_type == RuntimeLocationType.PLATFORM,
        )
        .order_by(col(AgentTask.created_at))
        .with_for_update(skip_locked=True)
    ).first()
    if v09_task is not None:
        runtime_instance = session.get(RuntimeInstance, v09_task.runtime_instance_id)
        usage = session.exec(
            select(AgentTaskModelUsage).where(
                AgentTaskModelUsage.task_id == v09_task.id
            )
        ).first()
        binding = (
            session.get(RuntimeModelBinding, usage.runtime_model_binding_id)
            if usage
            else None
        )
        configuration = (
            session.get(
                RuntimeConfigurationRevision,
                runtime_instance.applied_configuration_revision_id,
            )
            if runtime_instance and runtime_instance.applied_configuration_revision_id
            else None
        )
        capability = (
            session.get(
                RuntimeCapabilityReport,
                runtime_instance.current_capability_report_id,
            )
            if runtime_instance and runtime_instance.current_capability_report_id
            else None
        )
        snapshot = v09_task.snapshot
        if (
            runtime_instance is None
            or usage is None
            or binding is None
            or binding.status != RuntimeModelBindingStatus.AVAILABLE
            or binding.runtime_instance_id != runtime_instance.id
            or configuration is None
            or capability is None
            or configuration.configuration_digest
            != snapshot.get("runtime_configuration_digest")
            or capability.capability_fingerprint
            != snapshot.get("capability_fingerprint")
            or str(binding.id) != snapshot.get("runtime_model_binding_id")
        ):
            v09_task.status = TaskStatus.REJECTED
            v09_task.final_result = {"code": "frozen_execution_binding_changed"}
            v09_task.completed_at = datetime.now(timezone.utc)
            session.add(v09_task)
            session.commit()
            raise HTTPException(409, "frozen_execution_binding_changed")
        release = (
            session.get(AgentRelease, v09_task.agent_release_id)
            if v09_task.agent_release_id
            else None
        )
        active = (
            session.get(RuntimeAgentRelease, v09_task.runtime_agent_release_id)
            if v09_task.runtime_agent_release_id
            else None
        )
        if release is not None and (
            active is None
            or active.runtime_instance_id != runtime_instance.id
            or active.current_release_id != release.id
            or active.applied_digest != release.resolved_spec_digest
        ):
            v09_task.status = TaskStatus.REJECTED
            v09_task.final_result = {"code": "release_not_active"}
            v09_task.completed_at = datetime.now(timezone.utc)
            session.add(v09_task)
            session.commit()
            raise HTTPException(409, "release_not_active")
        if release is not None:
            blockers = release_skill_blockers(
                session,
                release,
                runtime_instance_id=runtime_instance.id,
            )
            if blockers:
                v09_task.status = TaskStatus.REJECTED
                v09_task.final_result = {
                    "code": "skill_sync_blocked",
                    "skills": blockers,
                }
                v09_task.completed_at = datetime.now(timezone.utc)
                session.add(v09_task)
                session.commit()
                raise HTTPException(409, "skill_sync_blocked")
            if release_skills_committing(
                session,
                release,
                runtime_instance_id=runtime_instance.id,
            ) or not release_skills_ready(
                session,
                release,
                runtime_instance_id=runtime_instance.id,
            ):
                raise HTTPException(204)
        v09_task.status = TaskStatus.DISPATCHED
        v09_task.claimed_by = body.worker_id
        v09_task.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        session.add(v09_task)
        append_and_apply_event(
            session,
            v09_task.id,
            1,
            AgentEventType.STATUS,
            {"state": TaskStatus.DISPATCHED.value, "executor": body.worker_id},
        )
        session.commit()
        agent_session = (
            session.get(AgentSession, v09_task.session_id)
            if v09_task.session_id
            else None
        )
        mcp_runtime_configs: list[dict[str, Any]] = []
        for server in snapshot.get("mcp_servers", []):
            target = session.exec(
                select(McpTargetBinding).where(
                    McpTargetBinding.revision_id
                    == uuid.UUID(str(server["revision_id"])),
                    McpTargetBinding.runtime_instance_id == runtime_instance.id,
                    McpTargetBinding.status == McpTargetStatus.VERIFIED,
                )
            ).first()
            mcp_secret = (
                session.exec(
                    select(McpPlatformSecret).where(
                        McpPlatformSecret.target_binding_id == target.id
                    )
                ).first()
                if target
                else None
            )
            if (
                target is None
                or mcp_secret is None
                or target.tool_digest not in server.get("tool_digests", [])
            ):
                v09_task.status = TaskStatus.REJECTED
                v09_task.final_result = {
                    "code": "mcp_target_not_ready",
                    "server": server.get("slug"),
                }
                v09_task.completed_at = datetime.now(timezone.utc)
                session.add(v09_task)
                session.commit()
                raise HTTPException(409, "mcp_target_not_ready")
            mcp_runtime_configs.append(
                {
                    **server,
                    "secret_inputs": open_secret_payload(
                        mcp_secret.secret_ciphertext
                    ),
                }
            )
        return {
            "task_id": str(v09_task.id),
            "revision": v09_task.revision,
            "requires_model_preparation": True,
            "model_preparation": {
                "runtime_instance_id": str(runtime_instance.id),
                "runtime_model_binding_id": str(binding.id),
                "engine_type": runtime_instance.engine_type.value,
                "engine_version": capability.engine_version,
                "adapter_version": capability.adapter_version,
                "engine_model_id": binding.engine_model_id,
                "route_type": binding.route_type.value,
                "route_reference": binding.route_key,
                "runtime_configuration_digest": configuration.configuration_digest,
                "capability_fingerprint": capability.capability_fingerprint,
                "effective_spec_digest": usage.effective_spec_digest,
            },
            "release_binding": {
                "runtime_instance_id": str(runtime_instance.id),
                "agent_id": str(release.agent_id) if release else None,
                "release_id": str(release.id) if release else None,
                "resolved_spec_digest": release.resolved_spec_digest
                if release
                else None,
                "resolved_spec_schema_version": release.resolved_spec_schema_version
                if release
                else None,
                "skills": release.resolved_spec.get("skills", []) if release else [],
            }
            if release
            else None,
            "mcp_runtime_configs": mcp_runtime_configs,
            "command": {
                "engine_type": runtime_instance.engine_type.value,
                "prompt": v09_task.prompt,
                "model": binding.engine_model_id,
                "system_prompt": snapshot.get("system_prompt"),
                "permission_mode": snapshot.get("permission_mode", "default"),
                "tools": snapshot.get("tools", []),
                "allowed_tools": snapshot.get("allowed_tools", []),
                "disallowed_tools": snapshot.get("disallowed_tools", []),
                "require_approval_tools": snapshot.get(
                    "require_approval_tools", []
                ),
                "required_capabilities": snapshot.get(
                    "required_capabilities", {}
                ),
                "cwd": snapshot.get("working_directory"),
                "env": {},
                "sdk_session_id": agent_session.sdk_session_id
                if agent_session
                else None,
                "start_sequence": 1,
                "timeout_seconds": snapshot.get("timeout_seconds", 3600),
                "roundtable_participants": snapshot.get(
                    "roundtable_participants", []
                ),
            },
        }
    raise HTTPException(204)


@router.post("/tasks/{task_id}/model-prepared")
def prepare_task_model(
    task_id: uuid.UUID, body: ModelPreparedInput, session: SessionDep
) -> dict[str, Any]:
    task = session.exec(
        select(AgentTask).where(AgentTask.id == task_id).with_for_update()
    ).first()
    usage = session.exec(
        select(AgentTaskModelUsage).where(AgentTaskModelUsage.task_id == task_id)
    ).first()
    if (
        task is None
        or usage is None
        or task.claimed_by != body.worker_id
        or task.revision != body.revision
        or task.status not in {TaskStatus.DISPATCHED, TaskStatus.RUNNING}
    ):
        raise HTTPException(409, "Task model preparation scope mismatch")
    expected = {
        "runtime_instance_id": usage.runtime_instance_id,
        "runtime_model_binding_id": usage.runtime_model_binding_id,
        "engine_type": usage.engine_type,
        "engine_version": usage.engine_version,
        "adapter_version": usage.adapter_version,
        "engine_model_id": task.snapshot.get("engine_model_id"),
        "route_type": usage.route_type,
        "route_reference": usage.route_reference,
        "runtime_configuration_digest": usage.runtime_configuration_digest,
        "capability_fingerprint": usage.capability_fingerprint,
        "effective_spec_digest": usage.effective_spec_digest,
    }
    actual = body.model_dump(exclude={"worker_id", "revision"})
    if actual != expected:
        task.status = TaskStatus.REJECTED
        task.final_result = {
            "code": "runtime_model_evidence_mismatch",
            "expected": {
                key: str(value) if isinstance(value, uuid.UUID) else value
                for key, value in expected.items()
            },
            "actual": {
                key: str(value) if isinstance(value, uuid.UUID) else value
                for key, value in actual.items()
            },
        }
        task.completed_at = datetime.now(timezone.utc)
        session.add(task)
        session.commit()
        raise HTTPException(409, "runtime_model_evidence_mismatch")
    binding = session.get(RuntimeModelBinding, usage.runtime_model_binding_id)
    if (
        binding is None
        or binding.status != RuntimeModelBindingStatus.AVAILABLE
        or binding.runtime_instance_id != usage.runtime_instance_id
    ):
        raise HTTPException(409, "runtime_model_binding_unavailable")
    usage.runtime_evidence = {
        key: str(value) if isinstance(value, uuid.UUID) else value
        for key, value in actual.items()
    }
    usage.evidenced_at = datetime.now(timezone.utc)
    session.add(usage)
    session.commit()
    if binding.route_type == RuntimeModelRouteType.RUNTIME_NATIVE:
        return {"route_type": binding.route_type.value, "environment": {}}
    if binding.route_type != RuntimeModelRouteType.PROVIDER_CONFIG:
        raise HTTPException(409, "runtime_model_route_unsupported")
    if binding.provider_config_id is None or binding.provider_model_id is None:
        raise HTTPException(409, "provider_route_incomplete")
    provider_model = session.get(LlmProviderModel, binding.provider_model_id)
    if (
        provider_model is None
        or provider_model.provider_config_id != binding.provider_config_id
        or provider_model.model_id != binding.engine_model_id
    ):
        raise HTTPException(409, "provider_route_model_mismatch")
    token = issue_gateway_token(
        task.namespace_id,
        usage.runtime_instance_id,
        task.id,
        binding.engine_model_id,
        provider_config_id=binding.provider_config_id,
        runtime_model_binding_id=binding.id,
    )
    return {
        "route_type": binding.route_type.value,
        "environment": {
            "ANTHROPIC_BASE_URL": (
                f"{settings.MODEL_GATEWAY_URL.rstrip('/')}/tasks/{task.id}"
            ),
            "ANTHROPIC_API_KEY": token,
        },
    }


@router.post("/tasks/{task_id}/lease")
def renew_platform_task_lease(
    task_id: uuid.UUID, body: LeaseInput, session: SessionDep
) -> dict[str, str]:
    task = session.exec(
        select(AgentTask).where(AgentTask.id == task_id).with_for_update()
    ).first()
    if (
        task is None
        or task.claimed_by != body.worker_id
        or task.revision != body.revision
    ):
        raise HTTPException(409, "Task lease scope mismatch")
    if task.status == TaskStatus.CANCELLING:
        return {"status": task.status.value}
    if task.status == TaskStatus.DISPATCHED:
        task.status = TaskStatus.RUNNING
    elif task.status != TaskStatus.RUNNING:
        raise HTTPException(409, "Task lease is not renewable")
    task.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    task.updated_at = datetime.now(timezone.utc)
    session.add(task)
    session.commit()
    return {"status": task.status.value}


@router.get("/routes/{runtime_id}/tasks/{task_id}")
def resolve_route(
    runtime_id: uuid.UUID,
    task_id: uuid.UUID,
    model_id: str,
    session: SessionDep,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Gateway route token required")
    try:
        claims = verify_gateway_token(
            authorization[7:],
            runtime_id=runtime_id,
            task_id=task_id,
            model_id=model_id,
        )
    except GatewayScopeError as exc:
        raise HTTPException(403, str(exc))
    task = session.get(AgentTask, task_id)
    if task is not None and task.runtime_instance_id == runtime_id:
        usage = session.exec(
            select(AgentTaskModelUsage).where(
                AgentTaskModelUsage.task_id == task.id
            )
        ).first()
        binding = (
            session.get(RuntimeModelBinding, usage.runtime_model_binding_id)
            if usage
            else None
        )
        if (
            usage is None
            or usage.evidenced_at is None
            or binding is None
            or binding.route_type != RuntimeModelRouteType.PROVIDER_CONFIG
            or binding.provider_config_id is None
            or binding.engine_model_id != model_id
            or task.namespace_id.hex != uuid.UUID(claims["namespace_id"]).hex
            or task.status
            not in {
                TaskStatus.DISPATCHED,
                TaskStatus.RUNNING,
                TaskStatus.CANCELLING,
            }
        ):
            raise HTTPException(403, "Task route is not active or is out of scope")
        try:
            verify_gateway_token(
                authorization[7:],
                runtime_id=runtime_id,
                task_id=task_id,
                model_id=model_id,
                provider_config_id=binding.provider_config_id,
                runtime_model_binding_id=binding.id,
            )
        except GatewayScopeError as exc:
            raise HTTPException(403, str(exc))
        provider = session.get(LlmProviderConfig, binding.provider_config_id)
        if provider is None or not provider.enabled:
            raise HTTPException(409, "Provider config is unavailable")
        try:
            provider_kind = gateway_provider_kind(provider.provider_slug)
        except UnsupportedGatewayProvider as exc:
            raise HTTPException(422, str(exc)) from exc
        return {
            "provider_kind": provider_kind,
            "base_url": provider.base_url,
            "model_id": model_id,
            "secret_inputs": open_secret_payload(provider.secret_ciphertext),
        }
    raise HTTPException(404, "RuntimeInstance route not found")
