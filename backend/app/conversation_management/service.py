import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func
from sqlmodel import Session, col, select

from app.agent_management.capability_models import AgentRelease, RuntimeAgentRelease
from app.conversation_management.models import (
    AgentDelegation,
    Conversation,
    ConversationAgent,
    ConversationAgentRole,
    ConversationContextSnapshot,
    ConversationMessage,
    ConversationStatus,
    ConversationVisibility,
    DelegationStatus,
    MessageAuthorType,
    MessageStatus,
    MessageTargetType,
)
from app.models import LlmProviderConfig, LlmProviderModel, User
from app.project_management.models import (
    Project,
    ProjectMember,
    ProjectRepository,
    ProjectSpecBinding,
    ProjectSpecLocation,
    ProjectStatus,
    RepositoryStatus,
    SpecStandard,
    SpecStandardVersion,
)
from app.project_management.service import normalize_spec_path
from app.runtime.models import (
    AgentSession,
    AgentTask,
    RuntimeNode,
    RuntimeProfile,
    RuntimeRouteMode,
    RuntimeType,
)
from app.runtime.policy import TaskStatus

ACTIVE_TASK_STATUSES = {
    TaskStatus.QUEUED,
    TaskStatus.DISPATCHED,
    TaskStatus.RUNNING,
    TaskStatus.AWAITING_APPROVAL,
    TaskStatus.CANCELLING,
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def canonical_digest(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def get_conversation(
    session: Session, conversation_id: uuid.UUID, namespace_id: uuid.UUID
) -> Conversation:
    conversation = session.get(Conversation, conversation_id)
    if conversation is None or conversation.namespace_id != namespace_id:
        raise HTTPException(404, "Conversation not found")
    return conversation


def require_conversation_access(
    session: Session,
    conversation_id: uuid.UUID,
    namespace_id: uuid.UUID,
    user: User,
    *,
    participate: bool = False,
) -> Conversation:
    conversation = get_conversation(session, conversation_id, namespace_id)
    if user.is_superuser or conversation.creator_id == user.id:
        allowed = True
    elif (
        conversation.visibility == ConversationVisibility.PROJECT
        and conversation.project_id is not None
    ):
        allowed = (
            session.exec(
                select(ProjectMember).where(
                    ProjectMember.project_id == conversation.project_id,
                    ProjectMember.user_id == user.id,
                )
            ).first()
            is not None
        )
    else:
        allowed = False
    if not allowed:
        raise HTTPException(403, "Conversation access denied")
    if participate:
        if conversation.status == ConversationStatus.ARCHIVED:
            raise HTTPException(409, "Conversation is archived and read-only")
        if conversation.project_id:
            project = session.get(Project, conversation.project_id)
            if project is None or project.status == ProjectStatus.ARCHIVED:
                raise HTTPException(409, "Project is archived and read-only")
    return conversation


def validate_chat_route(
    session: Session,
    namespace_id: uuid.UUID,
    runtime_id: uuid.UUID,
    provider_config_id: uuid.UUID,
    model_id: str,
) -> tuple[RuntimeProfile, LlmProviderConfig, LlmProviderModel]:
    runtime = session.get(RuntimeProfile, runtime_id)
    if runtime is None or runtime.namespace_id != namespace_id:
        raise HTTPException(422, "Runtime does not belong to the namespace")
    if runtime.route_mode != RuntimeRouteMode.PLATFORM_GATEWAY:
        raise HTTPException(
            409,
            "Chat requires a platform-gateway Runtime so the fixed provider route can be enforced",
        )
    provider = session.get(LlmProviderConfig, provider_config_id)
    if (
        provider is None
        or provider.namespace_id != namespace_id
        or not provider.enabled
    ):
        raise HTTPException(409, "Provider config is unavailable")
    model = session.exec(
        select(LlmProviderModel).where(
            LlmProviderModel.provider_config_id == provider.id,
            LlmProviderModel.model_id == model_id,
            col(LlmProviderModel.is_enabled).is_(True),
        )
    ).first()
    if model is None:
        raise HTTPException(409, "Model is not enabled for this namespace")
    if not runtime.config.get("compatibility_verified"):
        raise HTTPException(409, "Runtime compatibility is not verified")
    return runtime, provider, model


def validate_agent_binding(
    session: Session,
    namespace_id: uuid.UUID,
    runtime_id: uuid.UUID,
    binding_id: uuid.UUID,
) -> tuple[RuntimeAgentRelease, AgentRelease]:
    binding = session.get(RuntimeAgentRelease, binding_id)
    if (
        binding is None
        or binding.namespace_id != namespace_id
        or binding.runtime_profile_id != runtime_id
    ):
        raise HTTPException(409, "Agent Release is not active on the selected Runtime")
    release = session.get(AgentRelease, binding.current_release_id)
    if (
        release is None
        or binding.applied_digest != release.resolved_spec_digest
        or binding.materialization_digest != release.resolved_spec_digest
    ):
        raise HTTPException(409, "Agent Release digest is not active on the Runtime")
    return binding, release


def add_conversation_agent(
    session: Session,
    conversation: Conversation,
    binding: RuntimeAgentRelease,
    release: AgentRelease,
    role: ConversationAgentRole,
    creator_id: uuid.UUID,
) -> ConversationAgent:
    agent_session = AgentSession(
        namespace_id=conversation.namespace_id,
        runtime_profile_id=conversation.runtime_id,
        created_by=creator_id,
        agent_release_id=release.id,
        runtime_agent_release_id=binding.id,
        resolved_spec_digest=release.resolved_spec_digest,
    )
    session.add(agent_session)
    session.flush()
    participant = ConversationAgent(
        conversation_id=conversation.id,
        role=role,
        agent_id=release.agent_id,
        agent_release_id=release.id,
        runtime_agent_release_id=binding.id,
        agent_session_id=agent_session.id,
        resolved_spec_digest=release.resolved_spec_digest,
    )
    session.add(participant)
    return participant


def create_context_snapshot(
    session: Session,
    conversation: Conversation,
    user_id: uuid.UUID,
    content_refs: list[dict[str, Any]],
) -> ConversationContextSnapshot:
    if conversation.project_id is None:
        raise HTTPException(409, "Conversation has no project context")
    repositories = session.exec(
        select(ProjectRepository).where(
            ProjectRepository.project_id == conversation.project_id
        )
    ).all()
    if not repositories:
        raise HTTPException(409, "Project has no repository context")
    unavailable = [
        str(repository.id)
        for repository in repositories
        if repository.status != RepositoryStatus.AVAILABLE
        or not repository.validated_commit
    ]
    if unavailable:
        raise HTTPException(
            409,
            {"code": "project_repository_unavailable", "repository_ids": unavailable},
        )
    repository_ids = {repository.id for repository in repositories}
    safe_content_refs: list[dict[str, Any]] = []
    for item in content_refs:
        repository_id = uuid.UUID(str(item["repository_id"]))
        if repository_id not in repository_ids:
            raise HTTPException(
                422, "Content reference repository is outside the project"
            )
        safe_content_refs.append(
            {
                **item,
                "repository_id": str(repository_id),
                "path": normalize_spec_path(str(item["path"])),
            }
        )
    repository_refs = [
        {
            "repository_id": str(repository.id),
            "remote_url": repository.remote_url,
            "purpose": repository.purpose,
            "commit": repository.validated_commit,
        }
        for repository in repositories
    ]
    locations = session.exec(
        select(ProjectSpecLocation).where(
            ProjectSpecLocation.project_id == conversation.project_id
        )
    ).all()
    spec_refs: list[dict[str, Any]] = []
    for location in locations:
        binding = session.exec(
            select(ProjectSpecBinding).where(
                ProjectSpecBinding.spec_location_id == location.id
            )
        ).first()
        if binding is None:
            raise HTTPException(
                409,
                {
                    "code": "spec_binding_missing",
                    "spec_location_id": str(location.id),
                },
            )
        version = session.get(SpecStandardVersion, binding.standard_version_id)
        standard = session.get(SpecStandard, version.standard_id) if version else None
        if version is None or standard is None:
            raise HTTPException(409, "Spec standard version is unavailable")
        repository = next(
            item for item in repositories if item.id == location.repository_id
        )
        spec_refs.append(
            {
                "spec_location_id": str(location.id),
                "repository_id": str(location.repository_id),
                "path": location.path,
                "commit": repository.validated_commit,
                "standard_slug": standard.slug,
                "standard_version_id": str(version.id),
                "standard_version": version.version,
                "standard_content_digest": version.content_digest,
            }
        )
    latest = session.exec(
        select(func.max(ConversationContextSnapshot.revision)).where(
            ConversationContextSnapshot.conversation_id == conversation.id
        )
    ).one()
    revision = int(latest or 0) + 1
    digest_input = {
        "repository_refs": repository_refs,
        "spec_refs": spec_refs,
        "content_refs": safe_content_refs,
    }
    snapshot = ConversationContextSnapshot(
        conversation_id=conversation.id,
        project_id=conversation.project_id,
        revision=revision,
        repository_refs=repository_refs,
        spec_refs=spec_refs,
        content_refs=safe_content_refs,
        content_digest=canonical_digest(digest_input),
        created_by=user_id,
    )
    session.add(snapshot)
    session.flush()
    conversation.current_context_snapshot_id = snapshot.id
    conversation.updated_at = utcnow()
    session.add(conversation)
    return snapshot


def next_message_sequence(session: Session, conversation_id: uuid.UUID) -> int:
    latest = session.exec(
        select(func.max(ConversationMessage.sequence)).where(
            ConversationMessage.conversation_id == conversation_id
        )
    ).one()
    return int(latest or 0) + 1


def target_participants(
    session: Session,
    conversation: Conversation,
    target_type: MessageTargetType,
    target_agent_id: uuid.UUID | None,
) -> list[ConversationAgent]:
    participants = list(
        session.exec(
            select(ConversationAgent).where(
                ConversationAgent.conversation_id == conversation.id
            )
        ).all()
    )
    if target_type == MessageTargetType.MAIN:
        selected = [
            item for item in participants if item.role == ConversationAgentRole.MAIN
        ]
    elif target_type == MessageTargetType.ALL:
        selected = participants
    elif target_type == MessageTargetType.AGENT:
        selected = [item for item in participants if item.id == target_agent_id]
    else:
        selected = []
    if not selected:
        raise HTTPException(422, "Message target is not part of this conversation")
    return list(selected)


def create_agent_task(
    session: Session,
    conversation: Conversation,
    participant: ConversationAgent,
    user_message: ConversationMessage,
    prompt: str,
) -> AgentTask:
    active = session.exec(
        select(AgentTask).where(
            AgentTask.session_id == participant.agent_session_id,
            col(AgentTask.status).in_(list(ACTIVE_TASK_STATUSES)),
        )
    ).first()
    if active:
        raise HTTPException(
            409, {"code": "agent_turn_in_progress", "task_id": str(active.id)}
        )
    runtime = session.get(RuntimeProfile, conversation.runtime_id)
    binding = session.get(RuntimeAgentRelease, participant.runtime_agent_release_id)
    release = session.get(AgentRelease, participant.agent_release_id)
    if (
        runtime is None
        or binding is None
        or release is None
        or binding.current_release_id != release.id
        or binding.applied_digest != participant.resolved_spec_digest
        or release.resolved_spec_digest != participant.resolved_spec_digest
    ):
        raise HTTPException(409, "Fixed Agent Release is no longer active")
    node_id = None
    if runtime.runtime_type == RuntimeType.NODE:
        node = session.exec(
            select(RuntimeNode).where(
                RuntimeNode.runtime_profile_id == runtime.id,
                col(RuntimeNode.revoked_at).is_(None),
            )
        ).first()
        if node is None:
            raise HTTPException(409, "Target node Runtime is unavailable")
        node_id = node.id
    resolved_spec = release.resolved_spec
    task = AgentTask(
        namespace_id=conversation.namespace_id,
        session_id=participant.agent_session_id,
        runtime_profile_id=runtime.id,
        target_node_id=node_id,
        prompt=prompt,
        snapshot={
            "conversation_id": str(conversation.id),
            "conversation_message_id": str(user_message.id),
            "route_mode": runtime.route_mode.value,
            "agent_id": str(release.agent_id),
            "agent_release_id": str(release.id),
            "agent_release_version": release.version,
            "resolved_spec_digest": release.resolved_spec_digest,
            "system_prompt": resolved_spec["system_prompt"],
            "provider_config_id": resolved_spec["model"]["provider_config_id"],
            "model_id": resolved_spec["model"]["model_id"],
            "base_url": runtime.base_url,
            "permission_mode": resolved_spec["policies"]["permission_mode"],
            "tools": [item["key"] for item in resolved_spec["tools"]],
            "allowed_tools": resolved_spec["policies"].get("allowed_tools", []),
            "disallowed_tools": resolved_spec["policies"].get("disallowed_tools", []),
            "require_approval_tools": resolved_spec["policies"].get(
                "require_approval_tools", []
            ),
            "working_directory": runtime.config.get("cwd"),
            "timeout_seconds": int(runtime.config.get("timeout_seconds", 3600)),
        },
        agent_release_id=release.id,
        runtime_agent_release_id=binding.id,
        resolved_spec_digest=release.resolved_spec_digest,
        idempotency_key=f"conversation:{conversation.id}:message:{user_message.id}:agent:{participant.id}",
        created_by=user_message.author_id,
    )
    session.add(task)
    session.flush()
    return task


def reconcile_agent_messages(session: Session, conversation: Conversation) -> None:
    active_messages = session.exec(
        select(ConversationMessage).where(
            ConversationMessage.conversation_id == conversation.id,
            col(ConversationMessage.status).in_(
                [MessageStatus.RUNNING, MessageStatus.QUEUED]
            ),
        )
    ).all()
    changed = False
    for message in active_messages:
        raw_task_ids = message.payload.get("task_ids")
        task_ids = (
            [uuid.UUID(str(value)) for value in raw_task_ids]
            if isinstance(raw_task_ids, list)
            else ([message.task_id] if message.task_id else [])
        )
        tasks = [session.get(AgentTask, task_id) for task_id in task_ids]
        if not tasks or any(task is None for task in tasks):
            message.status = MessageStatus.FAILED
            message.error = {"code": "agent_task_missing"}
            changed = True
            continue
        terminal = True
        failures: list[dict[str, Any]] = []
        for task in tasks:
            assert task is not None
            if task.status == TaskStatus.SUCCEEDED:
                existing_reply = session.exec(
                    select(ConversationMessage).where(
                        ConversationMessage.conversation_id == conversation.id,
                        ConversationMessage.idempotency_key == f"agent-reply:{task.id}",
                    )
                ).first()
                if existing_reply is None:
                    participant = session.exec(
                        select(ConversationAgent).where(
                            ConversationAgent.agent_session_id == task.session_id
                        )
                    ).first()
                    session.add(
                        ConversationMessage(
                            conversation_id=conversation.id,
                            sequence=next_message_sequence(session, conversation.id),
                            author_type=MessageAuthorType.AGENT,
                            author_id=participant.id if participant else None,
                            target_type=MessageTargetType.SYSTEM,
                            context_snapshot_id=message.context_snapshot_id,
                            payload=task.final_result or {},
                            status=MessageStatus.COMPLETED,
                            task_id=task.id,
                            reply_to_id=message.id,
                            idempotency_key=f"agent-reply:{task.id}",
                            completed_at=task.completed_at or utcnow(),
                        )
                    )
            elif task.status in {
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
                TaskStatus.INTERRUPTED,
                TaskStatus.REJECTED,
            }:
                failures.append(task.final_result or {"code": task.status.value})
            else:
                terminal = False
        if terminal and failures:
            message.status = MessageStatus.FAILED
            message.error = {"code": "roundtable_partial_failure", "failures": failures}
            message.completed_at = utcnow()
            changed = True
        elif terminal:
            message.status = MessageStatus.COMPLETED
            message.completed_at = utcnow()
            changed = True
        else:
            message.status = MessageStatus.RUNNING

    delegations = session.exec(
        select(AgentDelegation).where(
            AgentDelegation.conversation_id == conversation.id,
            col(AgentDelegation.status).in_(
                [DelegationStatus.QUEUED, DelegationStatus.RUNNING]
            ),
        )
    ).all()
    for delegation in delegations:
        task = (
            session.get(AgentTask, delegation.task_id) if delegation.task_id else None
        )
        if task is None:
            delegation.status = DelegationStatus.FAILED
            delegation.error = {"code": "agent_task_missing"}
            changed = True
        elif task.status == TaskStatus.SUCCEEDED:
            delegation.status = DelegationStatus.COMPLETED
            delegation.result_payload = task.final_result or {}
            delegation.completed_at = task.completed_at or utcnow()
            changed = True
        elif task.status in {
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.INTERRUPTED,
            TaskStatus.REJECTED,
        }:
            delegation.status = DelegationStatus.FAILED
            delegation.error = task.final_result or {"code": task.status.value}
            delegation.completed_at = task.completed_at or utcnow()
            changed = True
        else:
            delegation.status = DelegationStatus.RUNNING
        session.add(delegation)
    if changed:
        session.commit()


def conversation_public(session: Session, conversation: Conversation) -> dict[str, Any]:
    agents = session.exec(
        select(ConversationAgent).where(
            ConversationAgent.conversation_id == conversation.id
        )
    ).all()
    return {
        "id": conversation.id,
        "namespace_id": conversation.namespace_id,
        "creator_id": conversation.creator_id,
        "project_id": conversation.project_id,
        "source_conversation_id": conversation.source_conversation_id,
        "title": conversation.title,
        "mode": conversation.mode,
        "visibility": conversation.visibility,
        "status": conversation.status,
        "runtime_id": conversation.runtime_id,
        "provider_config_id": conversation.provider_config_id,
        "model_id": conversation.model_id,
        "idempotency_key": conversation.idempotency_key,
        "current_context_snapshot_id": conversation.current_context_snapshot_id,
        "agents": agents,
        "created_at": conversation.created_at,
        "updated_at": conversation.updated_at,
        "archived_at": conversation.archived_at,
    }
