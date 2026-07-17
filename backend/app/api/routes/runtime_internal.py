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
)
from app.api.deps import SessionDep
from app.conversation_management.models import (
    AgentDelegation,
    Conversation,
    ConversationConfigurationRevision,
    ConversationMessage,
    ConversationMode,
    MessageAuthorType,
    MessageStatus,
)
from app.conversation_management.service import (
    create_runtime_delegation,
    reconcile_agent_messages,
)
from app.core.config import settings
from app.llm_provider_service import open_secret_payload
from app.models import LlmProviderConfig
from app.runtime.capabilities import HarnessCapabilities
from app.runtime.gateway import UnsupportedGatewayProvider, gateway_provider_kind
from app.runtime.models import (
    AgentEventType,
    AgentSession,
    AgentTask,
    RuntimeJob,
    RuntimeJobStatus,
    RuntimeProfile,
    RuntimeRouteMode,
    RuntimeSecret,
    RuntimeType,
)
from app.runtime.policy import TaskStatus
from app.runtime.repository import EventSequenceConflict, append_and_apply_event
from app.runtime.security import (
    GatewayScopeError,
    issue_gateway_token,
    require_internal_runtime,
    verify_gateway_token,
)
from app.runtime.skill_sync import release_skills_committing

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


class CapabilityReport(BaseModel):
    worker_id: str
    harness_capabilities: HarnessCapabilities


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


@router.post("/capabilities")
def report_platform_capabilities(
    body: CapabilityReport, session: SessionDep
) -> dict[str, int]:
    runtimes = session.exec(
        select(RuntimeProfile).where(
            RuntimeProfile.runtime_type == RuntimeType.PLATFORM
        )
    ).all()
    capabilities = body.harness_capabilities.model_dump(exclude_none=True)
    now = datetime.now(timezone.utc)
    for runtime in runtimes:
        runtime.harness_capabilities = capabilities
        runtime.updated_at = now
        session.add(runtime)
    session.commit()
    return {"updated": len(runtimes)}


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
            RuntimeProfile,
            col(RuntimeJob.runtime_profile_id) == RuntimeProfile.id,
        )
        .where(
            RuntimeJob.status == RuntimeJobStatus.QUEUED,
            RuntimeProfile.runtime_type == RuntimeType.PLATFORM,
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
    task = session.exec(
        select(AgentTask)
        .join(RuntimeProfile, col(AgentTask.runtime_profile_id) == RuntimeProfile.id)
        .where(
            AgentTask.status == TaskStatus.QUEUED,
            RuntimeProfile.runtime_type == RuntimeType.PLATFORM,
        )
        .order_by(col(AgentTask.created_at))
        .with_for_update(skip_locked=True)
    ).first()
    if task is None:
        raise HTTPException(204)
    runtime = session.get(RuntimeProfile, task.runtime_profile_id)
    if runtime is None:
        raise HTTPException(409, "Runtime is unavailable")
    release = (
        session.get(AgentRelease, task.agent_release_id)
        if task.agent_release_id
        else None
    )
    if release is None or task.resolved_spec_digest != release.resolved_spec_digest:
        task.status = TaskStatus.REJECTED
        task.final_result = {"code": "release_not_active"}
        task.completed_at = datetime.now(timezone.utc)
        session.add(task)
        session.commit()
        raise HTTPException(409, "release_not_active")
    if release_skills_committing(session, release, runtime.id):
        raise HTTPException(204)
    mcp_runtime_configs: list[dict[str, Any]] = []
    for server in task.snapshot.get("mcp_servers", []):
        binding = session.exec(
            select(McpTargetBinding).where(
                McpTargetBinding.revision_id == uuid.UUID(server["revision_id"]),
                McpTargetBinding.runtime_profile_id == runtime.id,
                McpTargetBinding.status == McpTargetStatus.VERIFIED,
            )
        ).first()
        mcp_secret = (
            session.exec(
                select(McpPlatformSecret).where(
                    McpPlatformSecret.target_binding_id == binding.id
                )
            ).first()
            if binding
            else None
        )
        if (
            binding is None
            or mcp_secret is None
            or binding.tool_digest not in server.get("tool_digests", [])
        ):
            task.status = TaskStatus.REJECTED
            task.final_result = {
                "code": "mcp_target_not_ready",
                "server": server.get("slug"),
            }
            task.completed_at = datetime.now(timezone.utc)
            session.add(task)
            session.commit()
            raise HTTPException(409, "mcp_target_not_ready")
        mcp_runtime_configs.append(
            {
                **server,
                "secret_inputs": open_secret_payload(mcp_secret.secret_ciphertext),
            }
        )
    env: dict[str, str]
    if runtime.route_mode == RuntimeRouteMode.DIRECT_ANTHROPIC:
        secret = session.exec(
            select(RuntimeSecret).where(RuntimeSecret.runtime_profile_id == runtime.id)
        ).first()
        if secret is None:
            raise HTTPException(409, "Runtime credential is not configured")
        values = open_secret_payload(secret.secret_ciphertext)
        api_key = (
            values.get("api_key") or values.get("api_token") or values.get("token")
        )
        if not api_key:
            raise HTTPException(409, "Runtime API key is missing")
        env = {
            "ANTHROPIC_BASE_URL": runtime.base_url or "",
            "ANTHROPIC_API_KEY": api_key,
        }
    else:
        gateway_token = issue_gateway_token(
            task.namespace_id, runtime.id, task.id, task.snapshot["model_id"]
        )
        env = {
            "ANTHROPIC_BASE_URL": (
                f"{settings.MODEL_GATEWAY_URL.rstrip('/')}/tasks/{task.id}"
            ),
            "ANTHROPIC_API_KEY": gateway_token,
        }
    task.status = TaskStatus.DISPATCHED
    task.claimed_by = body.worker_id
    task.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    session.add(task)
    append_and_apply_event(
        session,
        task.id,
        1,
        AgentEventType.STATUS,
        {"state": TaskStatus.DISPATCHED.value, "executor": body.worker_id},
    )
    session.commit()
    agent_session = (
        session.get(AgentSession, task.session_id) if task.session_id else None
    )
    return {
        "task_id": str(task.id),
        "revision": task.revision,
        "release_binding": {
            "runtime_profile_id": str(runtime.id),
            "agent_id": str(release.agent_id),
            "release_id": str(release.id),
            "resolved_spec_digest": release.resolved_spec_digest,
            "resolved_spec_schema_version": release.resolved_spec_schema_version,
            "skills": release.resolved_spec.get("skills", []),
        },
        "mcp_runtime_configs": mcp_runtime_configs,
        "command": {
            "prompt": task.prompt,
            "model": task.snapshot.get("model_id", runtime.model_id),
            "system_prompt": task.snapshot.get("system_prompt"),
            "permission_mode": task.snapshot.get(
                "permission_mode", runtime.permission_mode
            ),
            "tools": task.snapshot.get("tools", []),
            "allowed_tools": task.snapshot.get("allowed_tools", []),
            "disallowed_tools": task.snapshot.get("disallowed_tools", []),
            "require_approval_tools": task.snapshot.get("require_approval_tools", []),
            "cwd": task.snapshot.get("working_directory"),
            "env": env,
            "sdk_session_id": agent_session.sdk_session_id if agent_session else None,
            "start_sequence": 1,
            "timeout_seconds": task.snapshot.get("timeout_seconds", 3600),
            "roundtable_participants": task.snapshot.get("roundtable_participants", []),
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
    runtime = session.get(RuntimeProfile, runtime_id)
    if runtime is None or str(runtime.namespace_id) != claims["namespace_id"]:
        raise HTTPException(404, "Runtime route not found")
    task = session.get(AgentTask, task_id)
    chat_provider_id: uuid.UUID | None = None
    if task is not None:
        if (
            task.namespace_id != runtime.namespace_id
            or task.runtime_profile_id != runtime.id
            or task.snapshot.get("model_id") != model_id
            or task.status
            not in {
                TaskStatus.DISPATCHED,
                TaskStatus.RUNNING,
                TaskStatus.CANCELLING,
            }
        ):
            raise HTTPException(403, "Task route is not active or is out of scope")
    else:
        # Chat deliberately uses the same scoped gateway without creating an
        # agent_task: the running model message is the auditable execution record.
        message = session.get(ConversationMessage, task_id)
        conversation = (
            session.get(Conversation, message.conversation_id) if message else None
        )
        configuration = (
            session.get(
                ConversationConfigurationRevision,
                message.configuration_revision_id,
            )
            if message and message.configuration_revision_id
            else None
        )
        if (
            message is None
            or conversation is None
            or configuration is None
            or conversation.namespace_id != runtime.namespace_id
            or conversation.runtime_id != runtime.id
            or conversation.mode != ConversationMode.CHAT
            or configuration.conversation_id != conversation.id
            or configuration.model_id != model_id
            or message.author_type != MessageAuthorType.MODEL
            or message.status != MessageStatus.RUNNING
            or configuration.provider_config_id is None
        ):
            raise HTTPException(403, "Chat route is not active or is out of scope")
        chat_provider_id = configuration.provider_config_id
    if runtime.route_mode == RuntimeRouteMode.DIRECT_ANTHROPIC:
        secret = session.exec(
            select(RuntimeSecret).where(RuntimeSecret.runtime_profile_id == runtime.id)
        ).first()
        if secret is None:
            raise HTTPException(409, "Runtime credential is not configured")
        return {
            "provider_kind": "anthropic",
            "base_url": runtime.base_url,
            "model_id": runtime.model_id,
            "secret_inputs": open_secret_payload(secret.secret_ciphertext),
        }
    provider = session.get(
        LlmProviderConfig, chat_provider_id or runtime.provider_config_id
    )
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
