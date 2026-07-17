import uuid
from datetime import datetime, timezone
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select

from app import crud
from app.agent_management.capability_models import (
    AgentRelease,
    RuntimeAgentRelease,
    SkillDefinition,
)
from app.api.deps import CurrentUser, SessionDep, require_namespace_runtime_user
from app.core.config import settings
from app.models import NamespaceRole
from app.runtime.connections import node_is_online
from app.runtime.models import (
    AgentEvent,
    AgentEventType,
    AgentTask,
    AgentTaskSkillUsage,
    RuntimeNode,
    RuntimeProfile,
    RuntimeRouteMode,
    RuntimeType,
)
from app.runtime.policy import TaskKind, TaskStatus, require_task_transition
from app.runtime.repository import append_and_apply_event, retry_task

router = APIRouter(prefix="/runtime-tasks", tags=["runtime-tasks"])


class TaskCreate(BaseModel):
    runtime_agent_release_id: uuid.UUID
    runtime_profile_id: uuid.UUID
    node_id: uuid.UUID | None = None
    prompt: str = Field(min_length=1)
    task_kind: TaskKind = TaskKind.ORDINARY
    working_directory: str | None = None


class TaskPublic(BaseModel):
    id: uuid.UUID
    runtime_profile_id: uuid.UUID
    node_id: uuid.UUID | None
    task_kind: TaskKind
    status: TaskStatus
    prompt: str
    snapshot: dict[str, Any]
    final_result: dict[str, Any] | None
    retry_of_task_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    skill_usage: list[dict[str, Any]] = Field(default_factory=list)


class TasksPublic(BaseModel):
    data: list[TaskPublic]
    count: int


def _public(task: AgentTask) -> TaskPublic:
    return TaskPublic(
        id=task.id,
        runtime_profile_id=task.runtime_profile_id,
        node_id=task.target_node_id,
        task_kind=task.task_kind,
        status=task.status,
        prompt=task.prompt,
        snapshot=task.snapshot,
        final_result=task.final_result,
        retry_of_task_id=task.retry_of_task_id,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def _is_namespace_admin(
    session: SessionDep, current_user: CurrentUser, namespace_id: uuid.UUID
) -> bool:
    if current_user.is_superuser:
        return True
    return (
        crud.get_namespace_role(
            session=session, user_id=current_user.id, namespace_id=namespace_id
        )
        == NamespaceRole.ADMIN
    )


def _validated_working_directory(
    requested: str | None, runtime: RuntimeProfile, node: RuntimeNode | None
) -> str | None:
    if requested is None:
        return runtime.config.get("cwd")
    roots = runtime.config.get("allowed_working_roots", [])
    if not roots:
        raise HTTPException(422, "Runtime has no allowlisted working directory roots")
    path_type = (
        PureWindowsPath if node and node.os_name.lower() == "windows" else PurePosixPath
    )
    candidate = path_type(requested)
    if not candidate.is_absolute() or ".." in candidate.parts:
        raise HTTPException(
            422, "Working directory must be an absolute normalized path"
        )
    if not any(
        candidate == path_type(root) or path_type(root) in candidate.parents
        for root in roots
    ):
        raise HTTPException(422, "Working directory is outside runtime allowlist")
    return str(candidate)


def _resolve_target(
    session: SessionDep, namespace_id: uuid.UUID, body: TaskCreate
) -> tuple[RuntimeProfile, RuntimeNode | None]:
    runtime = session.get(RuntimeProfile, body.runtime_profile_id)
    if runtime is None or runtime.namespace_id != namespace_id:
        raise HTTPException(404, "Runtime not found")
    node = None
    if runtime.runtime_type == RuntimeType.NODE:
        if body.node_id is None:
            raise HTTPException(400, "node_id is required for a node runtime")
        node = session.get(RuntimeNode, body.node_id)
        if (
            node is None
            or node.namespace_id != namespace_id
            or node.runtime_profile_id != runtime.id
            or node.revoked_at is not None
        ):
            raise HTTPException(404, "Node runtime not found")
        if not node_is_online(node):
            raise HTTPException(409, "Node is offline")
        if (
            runtime.route_mode == RuntimeRouteMode.DIRECT_ANTHROPIC
            and not runtime.config.get("direct_compatibility_verified")
        ):
            raise HTTPException(
                422, "Node direct Anthropic compatibility is not verified"
            )
        if (
            runtime.route_mode == RuntimeRouteMode.PLATFORM_GATEWAY
            and not settings.MODEL_GATEWAY_PUBLIC_URL
        ):
            raise HTTPException(409, "Public Model Gateway URL is not configured")
    elif body.node_id is not None:
        raise HTTPException(400, "node_id is not valid for platform runtime")
    elif not runtime.config.get("compatibility_verified"):
        raise HTTPException(409, "Platform runtime compatibility is not verified")
    return runtime, node


@router.post("", response_model=TaskPublic, status_code=202)
def create_task(
    body: TaskCreate,
    session: SessionDep,
    current_user: CurrentUser,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> TaskPublic:
    if body.task_kind == TaskKind.ADMIN and not _is_namespace_admin(
        session, current_user, namespace_id
    ):
        raise HTTPException(403, "Admin task requires namespace admin")
    if not idempotency_key:
        raise HTTPException(400, "Idempotency-Key is required")
    existing = session.exec(
        select(AgentTask).where(
            AgentTask.namespace_id == namespace_id,
            AgentTask.idempotency_key == idempotency_key,
        )
    ).first()
    request_identity = {
        "runtime_agent_release_id": str(body.runtime_agent_release_id),
        "runtime_profile_id": str(body.runtime_profile_id),
        "node_id": str(body.node_id) if body.node_id else None,
        "task_kind": body.task_kind.value,
        "prompt": body.prompt,
        "working_directory": body.working_directory,
    }
    if existing:
        if existing.snapshot.get("request") != request_identity:
            raise HTTPException(409, "Idempotency-Key was used for a different task")
        return _public(existing)
    runtime, node = _resolve_target(session, namespace_id, body)
    binding = session.get(RuntimeAgentRelease, body.runtime_agent_release_id)
    if (
        binding is None
        or binding.namespace_id != namespace_id
        or binding.runtime_profile_id != runtime.id
    ):
        raise HTTPException(404, "Active Agent binding not found")
    release = session.get(AgentRelease, binding.current_release_id)
    if (
        release is None
        or binding.applied_digest != release.resolved_spec_digest
        or binding.materialization_digest != release.resolved_spec_digest
    ):
        raise HTTPException(409, "release_not_active")
    working_directory = _validated_working_directory(
        body.working_directory, runtime, node
    )
    resolved_spec = release.resolved_spec
    snapshot = {
        "request": request_identity,
        "runtime_type": runtime.runtime_type.value,
        "runtime_profile_id": str(runtime.id),
        "runtime_revision": node.config_revision if node else 1,
        "route_mode": runtime.route_mode.value,
        "agent_id": str(release.agent_id),
        "agent_release_id": str(release.id),
        "agent_release_version": release.version,
        "resolved_spec_digest": release.resolved_spec_digest,
        "resolved_spec_schema_version": release.resolved_spec_schema_version,
        "system_prompt": resolved_spec["system_prompt"],
        "provider_config_id": resolved_spec["model"]["provider_config_id"],
        "model_id": resolved_spec["model"]["model_id"],
        "base_url": runtime.base_url,
        "permission_mode": resolved_spec["policies"]["permission_mode"],
        "tools": [item["key"] for item in resolved_spec["tools"]],
        "allowed_tools": [
            item["key"]
            for item in resolved_spec["tools"]
            if item["policy"] in {"allow", "require_approval"}
        ],
        "disallowed_tools": [
            item["key"]
            for item in resolved_spec["tools"]
            if item["policy"] in {"deny", "disabled", "forbidden"}
        ],
        "require_approval_tools": [
            item["key"]
            for item in resolved_spec["tools"]
            if item["policy"] == "require_approval"
        ],
        "skills": resolved_spec["skills"],
        "plugins": resolved_spec["plugins"],
        "mcp_servers": resolved_spec["mcp_servers"],
        "working_directory": working_directory,
        "allowed_working_roots": runtime.config.get("allowed_working_roots", []),
        "timeout_seconds": resolved_spec["policies"]["timeout_seconds"],
        "executor_versions": {
            "claude_agent_sdk": "0.2.110",
            "executor": node.agent_version if node else "runtime-worker-0.1.0",
        },
    }
    task = AgentTask(
        namespace_id=namespace_id,
        runtime_profile_id=runtime.id,
        target_node_id=node.id if node else None,
        task_kind=body.task_kind,
        prompt=body.prompt,
        snapshot=snapshot,
        agent_release_id=release.id,
        runtime_agent_release_id=binding.id,
        resolved_spec_digest=release.resolved_spec_digest,
        idempotency_key=idempotency_key,
        created_by=current_user.id,
    )
    session.add(task)
    try:
        session.flush()
        append_and_apply_event(
            session, task.id, 0, AgentEventType.USER_MESSAGE, {"text": body.prompt}
        )
        session.commit()
        session.refresh(task)
    except IntegrityError:
        session.rollback()
        existing = session.exec(
            select(AgentTask).where(
                AgentTask.namespace_id == namespace_id,
                AgentTask.idempotency_key == idempotency_key,
            )
        ).first()
        if existing is None:
            raise
        if existing.snapshot.get("request") != request_identity:
            raise HTTPException(409, "Idempotency-Key was used for a different task")
        return _public(existing)
    return _public(task)


@router.get("", response_model=TasksPublic)
def list_tasks(
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> TasksPublic:
    tasks = session.exec(
        select(AgentTask)
        .where(AgentTask.namespace_id == namespace_id)
        .order_by(col(AgentTask.created_at).desc())
    ).all()
    return TasksPublic(data=[_public(task) for task in tasks], count=len(tasks))


def _get_task(
    session: SessionDep, task_id: uuid.UUID, namespace_id: uuid.UUID
) -> AgentTask:
    task = session.get(AgentTask, task_id)
    if task is None or task.namespace_id != namespace_id:
        raise HTTPException(404, "Task not found")
    return task


@router.get("/{task_id}", response_model=TaskPublic)
def read_task(
    task_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> TaskPublic:
    task = _get_task(session, task_id, namespace_id)
    result = _public(task)
    usages = session.exec(
        select(AgentTaskSkillUsage).where(AgentTaskSkillUsage.task_id == task.id)
    ).all()
    result.skill_usage = []
    for usage in usages:
        skill = session.get(SkillDefinition, usage.skill_id)
        result.skill_usage.append(
            {
                "skill_id": str(usage.skill_id),
                "skill_name": skill.name if skill else None,
                "skill_slug": skill.slug if skill else None,
                "version_id": str(usage.version_id),
                "version": usage.version,
                "content_sha256": usage.content_sha256,
                "runtime_generation": usage.runtime_generation,
                "reported_at": usage.reported_at.isoformat(),
            }
        )
    return result


@router.get("/{task_id}/events")
def read_task_events(
    task_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> list[dict[str, Any]]:
    _get_task(session, task_id, namespace_id)
    rows = session.exec(
        select(AgentEvent)
        .where(AgentEvent.task_id == task_id)
        .order_by(col(AgentEvent.sequence))
    ).all()
    return [
        {
            "sequence": row.sequence,
            "event_type": row.event_type.value,
            "payload": row.payload,
            "created_at": row.created_at,
        }
        for row in rows
    ]


@router.post("/{task_id}/cancel", response_model=TaskPublic)
def cancel_task(
    task_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> TaskPublic:
    task = session.exec(
        select(AgentTask)
        .where(AgentTask.id == task_id, AgentTask.namespace_id == namespace_id)
        .with_for_update()
    ).first()
    if task is None:
        raise HTTPException(404, "Task not found")
    if task.status in {
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.INTERRUPTED,
        TaskStatus.REJECTED,
    }:
        session.rollback()
        return _public(task)
    target = (
        TaskStatus.CANCELLING
        if task.status == TaskStatus.RUNNING
        else TaskStatus.CANCELLED
    )
    try:
        require_task_transition(task.status, target)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    task.status = target
    task.updated_at = datetime.now(timezone.utc)
    session.add(task)
    session.commit()
    return _public(task)


@router.post("/{task_id}/retry", response_model=TaskPublic, status_code=202)
def retry_failed_task(
    task_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> TaskPublic:
    original = _get_task(session, task_id, namespace_id)
    if not idempotency_key:
        raise HTTPException(400, "Idempotency-Key is required")
    existing = session.exec(
        select(AgentTask).where(
            AgentTask.namespace_id == namespace_id,
            AgentTask.idempotency_key == idempotency_key,
        )
    ).first()
    if existing:
        if existing.retry_of_task_id != original.id:
            raise HTTPException(409, "Idempotency-Key was used for a different retry")
        return _public(existing)
    try:
        retried = retry_task(session, original.id, idempotency_key=idempotency_key)
        session.commit()
        session.refresh(retried)
    except IntegrityError:
        session.rollback()
        existing = session.exec(
            select(AgentTask).where(
                AgentTask.namespace_id == namespace_id,
                AgentTask.idempotency_key == idempotency_key,
            )
        ).first()
        if existing is None:
            raise
        if existing.retry_of_task_id != original.id:
            raise HTTPException(409, "Idempotency-Key was used for a different retry")
        return _public(existing)
    except ValueError as exc:
        session.rollback()
        raise HTTPException(409, str(exc)) from exc
    return _public(retried)
