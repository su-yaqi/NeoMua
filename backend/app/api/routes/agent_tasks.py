import uuid
from datetime import datetime, timezone
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
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
from app.runtime.catalog import (
    RuntimeCatalogError,
    available_model_bindings,
    canonical_digest,
    current_runtime_evidence,
    resolve_model_binding,
    validate_model_binding_route,
)
from app.runtime.connections import node_is_online
from app.runtime.models import (
    AgentEvent,
    AgentEventType,
    AgentTask,
    AgentTaskModelCallUsage,
    AgentTaskModelUsage,
    AgentTaskSkillUsage,
    ModelSelectionMode,
    ModelSelectionSource,
    RuntimeInstance,
    RuntimeLocationType,
    RuntimeModelBinding,
    RuntimeNode,
    RuntimeProfile,
    RuntimeRouteMode,
    RuntimeType,
)
from app.runtime.policy import TaskKind, TaskStatus, require_task_transition
from app.runtime.repository import append_and_apply_event, retry_task

router = APIRouter(prefix="/runtime-tasks", tags=["runtime-tasks"])


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_agent_release_id: uuid.UUID | None = None
    runtime_profile_id: uuid.UUID | None = None
    runtime_instance_id: uuid.UUID | None = None
    runtime_model_binding_id: uuid.UUID | None = None
    model_selection_mode: ModelSelectionMode | None = None
    node_id: uuid.UUID | None = None
    prompt: str = Field(min_length=1)
    task_kind: TaskKind = TaskKind.ORDINARY
    working_directory: str | None = None
    system_prompt: str | None = None

    @model_validator(mode="after")
    def validate_execution_target(self) -> "TaskCreate":
        legacy = self.runtime_profile_id is not None
        current = self.runtime_instance_id is not None
        if legacy == current:
            raise ValueError("submit exactly one Runtime target")
        if legacy:
            raise ValueError("Runtime Profile task creation is read-only in v0.9")
        elif self.runtime_model_binding_id is None or self.model_selection_mode is None:
            raise ValueError(
                "v0.9 task requires runtime_model_binding_id and model_selection_mode"
            )
        return self


class TaskPublic(BaseModel):
    id: uuid.UUID
    runtime_profile_id: uuid.UUID | None
    runtime_instance_id: uuid.UUID | None
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
    model_usage: dict[str, Any] | None = None
    model_call_usage: list[dict[str, Any]] = Field(default_factory=list)


class TasksPublic(BaseModel):
    data: list[TaskPublic]
    count: int


def _public(task: AgentTask, session: SessionDep) -> TaskPublic:
    skill_usage = session.exec(
        select(AgentTaskSkillUsage).where(AgentTaskSkillUsage.task_id == task.id)
    ).all()
    model_usage = session.exec(
        select(AgentTaskModelUsage).where(AgentTaskModelUsage.task_id == task.id)
    ).first()
    model_call_usage = session.exec(
        select(AgentTaskModelCallUsage)
        .where(AgentTaskModelCallUsage.task_id == task.id)
        .order_by(col(AgentTaskModelCallUsage.call_sequence))
    ).all()
    return TaskPublic(
        id=task.id,
        runtime_profile_id=task.runtime_profile_id,
        runtime_instance_id=task.runtime_instance_id,
        node_id=task.target_node_id,
        task_kind=task.task_kind,
        status=task.status,
        prompt=task.prompt,
        snapshot=task.snapshot,
        final_result=task.final_result,
        retry_of_task_id=task.retry_of_task_id,
        created_at=task.created_at,
        updated_at=task.updated_at,
        skill_usage=[item.model_dump(mode="json") for item in skill_usage],
        model_usage=model_usage.model_dump(mode="json") if model_usage else None,
        model_call_usage=[item.model_dump(mode="json") for item in model_call_usage],
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


def _validated_v09_working_directory(
    requested: str | None,
    *,
    configuration: Any,
    node: RuntimeNode | None,
) -> str | None:
    policy = configuration.working_directory_policy
    if requested is None:
        if policy == "project":
            raise HTTPException(422, "Runtime requires an explicit working directory")
        return None
    roots = configuration.security_policy.get("allowed_working_roots", [])
    if not roots:
        raise HTTPException(422, "Runtime has no allowlisted working directory roots")
    path_type = (
        PureWindowsPath if node and node.os_name.lower() == "windows" else PurePosixPath
    )
    candidate = path_type(requested)
    if not candidate.is_absolute() or ".." in candidate.parts:
        raise HTTPException(422, "Working directory must be an absolute normalized path")
    if not any(
        candidate == path_type(root) or path_type(root) in candidate.parents
        for root in roots
    ):
        raise HTTPException(422, "Working directory is outside runtime allowlist")
    return str(candidate)


def _v09_task_material(
    session: SessionDep,
    namespace_id: uuid.UUID,
    body: TaskCreate,
) -> tuple[RuntimeInstance, RuntimeNode | None, RuntimeModelBinding, AgentRelease | None, RuntimeAgentRelease | None, dict[str, Any], AgentTaskModelUsage]:
    assert body.runtime_instance_id is not None
    assert body.runtime_model_binding_id is not None
    assert body.model_selection_mode is not None
    runtime = session.get(RuntimeInstance, body.runtime_instance_id)
    if runtime is None or runtime.namespace_id != namespace_id:
        raise HTTPException(404, "Runtime not found")
    node = session.get(RuntimeNode, runtime.runtime_node_id) if runtime.runtime_node_id else None
    if runtime.location_type == RuntimeLocationType.NODE:
        if node is None or node.revoked_at is not None or not node_is_online(node):
            raise HTTPException(409, "Runtime Node is offline")
        if body.node_id is not None and body.node_id != node.id:
            raise HTTPException(422, "node_id does not match the selected Runtime")
    elif body.node_id is not None:
        raise HTTPException(422, "node_id is not valid for a platform Runtime")
    try:
        configuration, capability, catalog_fingerprint = current_runtime_evidence(
            session, runtime
        )
        model_binding = session.get(RuntimeModelBinding, body.runtime_model_binding_id)
        if (
            model_binding is None
            or model_binding.namespace_id != namespace_id
            or model_binding.runtime_instance_id != runtime.id
        ):
            raise RuntimeCatalogError(
                "model_binding_wrong_runtime",
                "Model binding does not belong to the selected Runtime",
            )
        resolved, _ = resolve_model_binding(
            session,
            runtime=runtime,
            mode=ModelSelectionMode.EXACT,
            exact_binding_id=model_binding.id,
            preferred_model_definition_id=None,
        )
        validate_model_binding_route(session, resolved)
    except RuntimeCatalogError as exc:
        raise HTTPException(409, {"code": exc.code, "message": exc.message}) from exc

    active: RuntimeAgentRelease | None = None
    release: AgentRelease | None = None
    preferred_model_id: uuid.UUID | None = None
    if body.runtime_agent_release_id is not None:
        active = session.get(RuntimeAgentRelease, body.runtime_agent_release_id)
        if (
            active is None
            or active.namespace_id != namespace_id
            or active.runtime_instance_id != runtime.id
        ):
            raise HTTPException(404, "Active Agent binding not found")
        release = session.get(AgentRelease, active.current_release_id)
        if (
            release is None
            or release.resolved_spec_schema_version != "2.0"
            or active.applied_digest != release.resolved_spec_digest
            or active.materialization_digest != release.resolved_spec_digest
        ):
            raise HTTPException(409, "release_not_active")
        preferred_model_id = release.preferred_model_definition_id
    elif body.model_selection_mode == ModelSelectionMode.AGENT_PREFERENCE:
        raise HTTPException(422, "agent_preference requires an active Agent Release")

    if body.model_selection_mode == ModelSelectionMode.AGENT_PREFERENCE:
        if preferred_model_id != model_binding.model_definition_id:
            raise HTTPException(409, "agent_preference_binding_mismatch")
        matching = [
            item
            for item in available_model_bindings(session, runtime.id)
            if item.model_definition_id == preferred_model_id
        ]
        if len(matching) != 1 or matching[0].id != model_binding.id:
            raise HTTPException(409, "agent_preference_not_uniquely_resolved")
        selection_source = ModelSelectionSource.AGENT_PREFERENCE
    else:
        selection_source = (
            ModelSelectionSource.EXPLICIT_OVERRIDE
            if preferred_model_id is not None
            and preferred_model_id != model_binding.model_definition_id
            else ModelSelectionSource.EXACT
        )

    resolved_spec = release.resolved_spec if release else {}
    policies = dict(resolved_spec.get("policies", {}))
    runtime_permission_modes = set(
        configuration.security_policy.get("permission_modes", [])
    )
    if not runtime_permission_modes and configuration.security_policy.get(
        "permission_mode"
    ):
        runtime_permission_modes.add(
            str(configuration.security_policy["permission_mode"])
        )
    permission_mode = str(policies.get("permission_mode", "default"))
    if runtime_permission_modes and permission_mode not in runtime_permission_modes:
        raise HTTPException(409, "agent_permission_exceeds_runtime_policy")
    tools = list(resolved_spec.get("tools", []))
    allowed_tool_keys = set(capability.capabilities.get("tools", []))
    required_tool_keys = {
        str(item["key"])
        for item in tools
        if item.get("policy") in {"allow", "require_approval"}
    }
    if not required_tool_keys.issubset(allowed_tool_keys):
        raise HTTPException(409, "runtime_tool_capability_changed")
    require_approval_tools = [
        item["key"] for item in tools if item.get("policy") == "require_approval"
    ]
    if require_approval_tools and not capability.capabilities.get(
        "supports_per_tool_approval", False
    ):
        raise HTTPException(409, "runtime_tool_approval_unsupported")
    if resolved_spec.get("mcp_servers") and not capability.capabilities.get(
        "supports_mcp_injection", False
    ):
        raise HTTPException(409, "runtime_mcp_injection_unsupported")
    required_capabilities = policies.get("required_capabilities", {})
    for key, required in required_capabilities.items():
        if required and capability.capabilities.get(key) is not True:
            raise HTTPException(409, f"runtime_capability_required:{key}")
    working_directory = _validated_v09_working_directory(
        body.working_directory, configuration=configuration, node=node
    )
    effective_digest = canonical_digest(
        {
            "release_digest": release.resolved_spec_digest if release else None,
            "configuration_digest": configuration.configuration_digest,
            "capability_fingerprint": capability.capability_fingerprint,
            "model_binding_id": str(model_binding.id),
            "model_catalog_fingerprint": catalog_fingerprint,
        }
    )
    snapshot = {
        "schema_version": "0.9",
        "runtime_instance_id": str(runtime.id),
        "runtime_node_id": str(node.id) if node else None,
        "engine_type": runtime.engine_type.value,
        "engine_version": capability.engine_version,
        "adapter_version": capability.adapter_version,
        "runtime_configuration_revision_id": str(configuration.id),
        "runtime_configuration_digest": configuration.configuration_digest,
        "runtime_capability_report_id": str(capability.id),
        "capability_fingerprint": capability.capability_fingerprint,
        "runtime_model_catalog_fingerprint": catalog_fingerprint,
        "runtime_model_binding_id": str(model_binding.id),
        "model_definition_id": str(model_binding.model_definition_id),
        "engine_model_id": model_binding.engine_model_id,
        "route_type": model_binding.route_type.value,
        "route_key": model_binding.route_key,
        "provider_config_id": str(model_binding.provider_config_id)
        if model_binding.provider_config_id
        else None,
        "provider_model_id": str(model_binding.provider_model_id)
        if model_binding.provider_model_id
        else None,
        "model_selection_mode": body.model_selection_mode.value,
        "selection_source": selection_source.value,
        "preferred_model_definition_id": str(preferred_model_id)
        if preferred_model_id
        else None,
        "agent_id": str(release.agent_id) if release else None,
        "agent_release_id": str(release.id) if release else None,
        "resolved_spec_digest": release.resolved_spec_digest if release else None,
        "resolved_spec_schema_version": release.resolved_spec_schema_version
        if release
        else None,
        "system_prompt": (
            resolved_spec.get("system_prompt") if release else body.system_prompt
        ),
        "permission_mode": permission_mode,
        "tools": [item["key"] for item in tools],
        "allowed_tools": [
            item["key"]
            for item in tools
            if item.get("policy") in {"allow", "require_approval"}
        ],
        "disallowed_tools": [
            item["key"]
            for item in tools
            if item.get("policy") in {"deny", "disabled", "forbidden"}
        ],
        "require_approval_tools": require_approval_tools,
        "required_capabilities": required_capabilities,
        "skills": resolved_spec.get("skills", []),
        "plugins": resolved_spec.get("plugins", []),
        "mcp_servers": resolved_spec.get("mcp_servers", []),
        "working_directory": working_directory,
        "allowed_working_roots": configuration.security_policy.get(
            "allowed_working_roots", []
        ),
        "timeout_seconds": min(
            int(policies.get("timeout_seconds", 3600)),
            int(
                configuration.resource_limits.get(
                    "max_timeout_seconds", policies.get("timeout_seconds", 3600)
                )
            ),
        ),
        "effective_spec_digest": effective_digest,
    }
    usage = AgentTaskModelUsage(
        task_id=uuid.uuid4(),
        runtime_instance_id=runtime.id,
        runtime_node_id=node.id if node else None,
        agent_release_id=release.id if release else None,
        model_definition_id=model_binding.model_definition_id,
        runtime_model_binding_id=model_binding.id,
        engine_type=runtime.engine_type.value,
        engine_version=capability.engine_version,
        adapter_version=capability.adapter_version,
        route_type=model_binding.route_type.value,
        route_reference=model_binding.route_key,
        model_selection_mode=body.model_selection_mode.value,
        selection_source=selection_source.value,
        runtime_configuration_digest=configuration.configuration_digest,
        capability_fingerprint=capability.capability_fingerprint,
        model_catalog_fingerprint=catalog_fingerprint,
        effective_spec_digest=effective_digest,
    )
    return runtime, node, model_binding, release, active, snapshot, usage


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
        "runtime_agent_release_id": str(body.runtime_agent_release_id)
        if body.runtime_agent_release_id
        else None,
        "runtime_profile_id": str(body.runtime_profile_id)
        if body.runtime_profile_id
        else None,
        "runtime_instance_id": str(body.runtime_instance_id)
        if body.runtime_instance_id
        else None,
        "runtime_model_binding_id": str(body.runtime_model_binding_id)
        if body.runtime_model_binding_id
        else None,
        "model_selection_mode": body.model_selection_mode.value
        if body.model_selection_mode
        else None,
        "node_id": str(body.node_id) if body.node_id else None,
        "task_kind": body.task_kind.value,
        "prompt": body.prompt,
        "working_directory": body.working_directory,
        "system_prompt": body.system_prompt,
    }
    if existing:
        if existing.snapshot.get("request") != request_identity:
            raise HTTPException(409, "Idempotency-Key was used for a different task")
        return _public(existing, session)
    if body.runtime_instance_id is not None:
        runtime_instance, node, _, release, active, snapshot, usage = (
            _v09_task_material(session, namespace_id, body)
        )
        snapshot["request"] = request_identity
        task = AgentTask(
            namespace_id=namespace_id,
            runtime_instance_id=runtime_instance.id,
            target_node_id=node.id if node else None,
            task_kind=body.task_kind,
            prompt=body.prompt,
            snapshot=snapshot,
            agent_release_id=release.id if release else None,
            runtime_agent_release_id=active.id if active else None,
            resolved_spec_digest=release.resolved_spec_digest if release else None,
            idempotency_key=idempotency_key,
            created_by=current_user.id,
        )
        usage.task_id = task.id
        session.add(task)
        session.add(usage)
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
                raise HTTPException(
                    409, "Idempotency-Key was used for a different task"
                )
            return _public(existing, session)
        return _public(task, session)
    assert body.runtime_profile_id is not None
    assert body.runtime_agent_release_id is not None
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
        return _public(existing, session)
    return _public(task, session)


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
    return TasksPublic(
        data=[_public(task, session) for task in tasks], count=len(tasks)
    )


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
    result = _public(task, session)
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
    model_usage = session.exec(
        select(AgentTaskModelUsage).where(AgentTaskModelUsage.task_id == task.id)
    ).first()
    if model_usage is not None:
        result.model_usage = {
            "runtime_instance_id": str(model_usage.runtime_instance_id),
            "runtime_node_id": str(model_usage.runtime_node_id)
            if model_usage.runtime_node_id
            else None,
            "agent_release_id": str(model_usage.agent_release_id)
            if model_usage.agent_release_id
            else None,
            "model_definition_id": str(model_usage.model_definition_id),
            "runtime_model_binding_id": str(model_usage.runtime_model_binding_id),
            "engine_type": model_usage.engine_type,
            "engine_version": model_usage.engine_version,
            "adapter_version": model_usage.adapter_version,
            "route_type": model_usage.route_type,
            "route_reference": model_usage.route_reference,
            "model_selection_mode": model_usage.model_selection_mode,
            "selection_source": model_usage.selection_source,
            "runtime_configuration_digest": model_usage.runtime_configuration_digest,
            "capability_fingerprint": model_usage.capability_fingerprint,
            "model_catalog_fingerprint": model_usage.model_catalog_fingerprint,
            "effective_spec_digest": model_usage.effective_spec_digest,
            "runtime_evidence": model_usage.runtime_evidence,
            "prepared_at": model_usage.prepared_at.isoformat(),
            "evidenced_at": model_usage.evidenced_at.isoformat()
            if model_usage.evidenced_at
            else None,
        }
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
        return _public(task, session)
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
    return _public(task, session)


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
        return _public(existing, session)
    if original.runtime_instance_id is not None:
        binding_id = original.snapshot.get("runtime_model_binding_id")
        runtime = session.get(RuntimeInstance, original.runtime_instance_id)
        binding = (
            session.get(RuntimeModelBinding, uuid.UUID(binding_id))
            if binding_id
            else None
        )
        try:
            if runtime is None or binding is None:
                raise RuntimeCatalogError(
                    "frozen_execution_binding_missing",
                    "The original exact execution binding is missing",
                )
            current_runtime_evidence(session, runtime)
            resolved, _ = resolve_model_binding(
                session,
                runtime=runtime,
                mode=ModelSelectionMode.EXACT,
                exact_binding_id=binding.id,
                preferred_model_definition_id=None,
            )
            validate_model_binding_route(session, resolved)
        except (RuntimeCatalogError, ValueError) as exc:
            code = getattr(exc, "code", "frozen_execution_binding_invalid")
            raise HTTPException(409, {"code": code, "message": str(exc)}) from exc
    try:
        retried = retry_task(session, original.id, idempotency_key=idempotency_key)
        original_usage = session.exec(
            select(AgentTaskModelUsage).where(
                AgentTaskModelUsage.task_id == original.id
            )
        ).first()
        if original_usage is not None:
            session.add(
                AgentTaskModelUsage(
                    task_id=retried.id,
                    runtime_instance_id=original_usage.runtime_instance_id,
                    runtime_node_id=original_usage.runtime_node_id,
                    agent_release_id=original_usage.agent_release_id,
                    model_definition_id=original_usage.model_definition_id,
                    runtime_model_binding_id=original_usage.runtime_model_binding_id,
                    engine_type=original_usage.engine_type,
                    engine_version=original_usage.engine_version,
                    adapter_version=original_usage.adapter_version,
                    route_type=original_usage.route_type,
                    route_reference=original_usage.route_reference,
                    model_selection_mode=original_usage.model_selection_mode,
                    selection_source=original_usage.selection_source,
                    runtime_configuration_digest=original_usage.runtime_configuration_digest,
                    capability_fingerprint=original_usage.capability_fingerprint,
                    model_catalog_fingerprint=original_usage.model_catalog_fingerprint,
                    effective_spec_digest=original_usage.effective_spec_digest,
                )
            )
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
        return _public(existing, session)
    except ValueError as exc:
        session.rollback()
        raise HTTPException(409, str(exc)) from exc
    return _public(retried, session)
