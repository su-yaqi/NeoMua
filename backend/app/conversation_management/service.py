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
    ConversationConfigurationRevision,
    ConversationContextSnapshot,
    ConversationEvent,
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
from app.project_management.service import verified_repository_runtime_proof
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


def create_configuration_revision(
    session: Session,
    conversation: Conversation,
    creator_id: uuid.UUID | None,
    *,
    provider_config_id: uuid.UUID | None = None,
    model_id: str | None = None,
    organizer_agent_id: uuid.UUID | None = None,
    participant_ids: list[uuid.UUID] | None = None,
) -> ConversationConfigurationRevision:
    latest = session.exec(
        select(func.max(ConversationConfigurationRevision.revision)).where(
            ConversationConfigurationRevision.conversation_id == conversation.id
        )
    ).one()
    revision = ConversationConfigurationRevision(
        conversation_id=conversation.id,
        revision=int(latest or 0) + 1,
        mode=conversation.mode,
        provider_config_id=provider_config_id,
        model_id=model_id,
        organizer_agent_id=organizer_agent_id,
        participant_ids=[str(value) for value in (participant_ids or [])],
        created_by=creator_id,
    )
    session.add(revision)
    session.flush()
    conversation.current_configuration_revision_id = revision.id
    conversation.provider_config_id = provider_config_id
    conversation.model_id = model_id
    conversation.updated_at = utcnow()
    session.add(conversation)
    return revision


def ensure_current_configuration(
    session: Session,
    conversation: Conversation,
    creator_id: uuid.UUID | None = None,
) -> ConversationConfigurationRevision:
    if conversation.current_configuration_revision_id is not None:
        current = session.get(
            ConversationConfigurationRevision,
            conversation.current_configuration_revision_id,
        )
        if current is not None:
            return current
    if conversation.mode.value == "chat":
        if conversation.provider_config_id is None or conversation.model_id is None:
            raise HTTPException(409, "Conversation model route is incomplete")
        return create_configuration_revision(
            session,
            conversation,
            creator_id,
            provider_config_id=conversation.provider_config_id,
            model_id=conversation.model_id,
        )
    participants = list(
        session.exec(
            select(ConversationAgent).where(
                ConversationAgent.conversation_id == conversation.id
            )
        ).all()
    )
    organizer = next(
        (item for item in participants if item.role == ConversationAgentRole.MAIN),
        None,
    )
    if organizer is None:
        raise HTTPException(409, "Agent conversation has no organizer")
    return create_configuration_revision(
        session,
        conversation,
        creator_id,
        organizer_agent_id=organizer.id,
        participant_ids=[item.id for item in participants],
    )


def configuration_public(
    revision: ConversationConfigurationRevision,
) -> dict[str, Any]:
    return {
        "id": str(revision.id),
        "conversation_id": str(revision.conversation_id),
        "revision": revision.revision,
        "mode": revision.mode.value,
        "provider_config_id": (
            str(revision.provider_config_id) if revision.provider_config_id else None
        ),
        "model_id": revision.model_id,
        "organizer_agent_id": (
            str(revision.organizer_agent_id) if revision.organizer_agent_id else None
        ),
        "participant_ids": revision.participant_ids,
        "created_by": str(revision.created_by) if revision.created_by else None,
        "created_at": revision.created_at.isoformat(),
    }


def create_context_snapshot(
    session: Session,
    conversation: Conversation,
    user_id: uuid.UUID,
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
    runtime_key = str(conversation.runtime_id)
    unavailable = []
    runtime_proofs: dict[uuid.UUID, dict[str, Any]] = {}
    for repository in repositories:
        proof = verified_repository_runtime_proof(
            session, repository, conversation.runtime_id
        )
        if (
            repository.status != RepositoryStatus.AVAILABLE
            or not repository.validated_commit
            or proof is None
        ):
            unavailable.append(str(repository.id))
        else:
            runtime_proofs[repository.id] = proof
    if unavailable:
        raise HTTPException(
            409,
            {"code": "project_repository_unavailable", "repository_ids": unavailable},
        )
    repository_refs = [
        {
            "repository_id": str(repository.id),
            "remote_url": repository.remote_url,
            "purpose": repository.purpose,
            "commit": repository.validated_commit,
            "runtime_id": runtime_key,
            "workspace_ref": runtime_proofs[repository.id]["workspace_ref"],
            "runtime_job_id": runtime_proofs[repository.id]["runtime_job_id"],
        }
        for repository in repositories
    ]
    locations = session.exec(
        select(ProjectSpecLocation).where(
            ProjectSpecLocation.project_id == conversation.project_id
        )
    ).all()
    spec_refs: list[dict[str, Any]] = []
    safe_content_refs: list[dict[str, Any]] = []
    for location in locations:
        binding = session.exec(
            select(ProjectSpecBinding).where(
                ProjectSpecBinding.spec_location_id == location.id
            )
        ).first()
        if (
            binding is None
            or location.status.value != "valid"
            or binding.status.value != "valid"
        ):
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
        proof_locations = {
            str(item["spec_location_id"]): item
            for item in runtime_proofs[repository.id].get("spec_locations", [])
        }
        proof_location = proof_locations.get(str(location.id))
        if proof_location is None:
            raise HTTPException(
                409,
                {
                    "code": "spec_runtime_proof_missing",
                    "spec_location_id": str(location.id),
                    "runtime_id": runtime_key,
                },
            )
        for file in proof_location.get("files", []):
            safe_content_refs.append(
                {
                    "repository_id": str(repository.id),
                    "spec_location_id": str(location.id),
                    "path": file["path"],
                    "blob_digest": file["content_digest"],
                    "size": file["size"],
                    "content": file["content"],
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


def append_conversation_event(
    session: Session,
    conversation_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any],
) -> ConversationEvent:
    conversation = session.exec(
        select(Conversation).where(Conversation.id == conversation_id).with_for_update()
    ).one()
    latest = session.exec(
        select(func.max(ConversationEvent.sequence)).where(
            ConversationEvent.conversation_id == conversation.id
        )
    ).one()
    event = ConversationEvent(
        conversation_id=conversation.id,
        sequence=int(latest or 0) + 1,
        event_type=event_type,
        payload=payload,
    )
    session.add(event)
    session.flush()
    return event


def append_message_event(
    session: Session, message: ConversationMessage, event_type: str
) -> ConversationEvent:
    return append_conversation_event(
        session,
        message.conversation_id,
        event_type,
        {
            "message_id": str(message.id),
            "message_sequence": message.sequence,
            "status": message.status.value,
            "author_type": message.author_type.value,
            "target_type": message.target_type.value,
            "target_agent_id": (
                str(message.target_agent_id) if message.target_agent_id else None
            ),
            "configuration_revision_id": (
                str(message.configuration_revision_id)
                if message.configuration_revision_id
                else None
            ),
            "task_id": str(message.task_id) if message.task_id else None,
            "error": message.error,
        },
    )


def target_participants(
    session: Session,
    conversation: Conversation,
    target_type: MessageTargetType,
    target_agent_id: uuid.UUID | None,
) -> list[ConversationAgent]:
    current = (
        session.get(
            ConversationConfigurationRevision,
            conversation.current_configuration_revision_id,
        )
        if conversation.current_configuration_revision_id
        else None
    )
    active_ids = set(current.participant_ids) if current is not None else None
    participants = list(
        session.exec(
            select(ConversationAgent).where(
                ConversationAgent.conversation_id == conversation.id
            )
        ).all()
    )
    if active_ids is not None:
        participants = [item for item in participants if str(item.id) in active_ids]
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
    configuration = (
        session.get(
            ConversationConfigurationRevision,
            user_message.configuration_revision_id,
        )
        if user_message.configuration_revision_id
        else None
    )
    organizer_id = (
        configuration.organizer_agent_id
        if configuration is not None
        else next(
            (
                item.id
                for item in session.exec(
                    select(ConversationAgent).where(
                        ConversationAgent.conversation_id == conversation.id,
                        ConversationAgent.role == ConversationAgentRole.MAIN,
                    )
                ).all()
            ),
            None,
        )
    )
    roundtable_role = (
        ConversationAgentRole.MAIN
        if participant.id == organizer_id
        else ConversationAgentRole.COLLABORATOR
    )
    effective_prompt = prompt
    if user_message.context_snapshot_id is not None:
        snapshot = session.get(
            ConversationContextSnapshot, user_message.context_snapshot_id
        )
        if snapshot is None:
            raise HTTPException(409, "Conversation context snapshot is unavailable")
        materialized = [
            (f"--- {item['path']} @ {item['blob_digest']} ---\n{item['content']}")
            for item in snapshot.content_refs
            if item.get("path")
            and item.get("blob_digest")
            and isinstance(item.get("content"), str)
        ]
        effective_prompt = (
            "以下内容来自已由目标 Runtime 校验并固定的项目上下文快照。"
            f"快照摘要：{snapshot.content_digest}\n"
            + "\n".join(materialized)
            + f"\n\n当前消息：\n{prompt}"
        )
    system_prompt = resolved_spec["system_prompt"]
    roundtable_participants: list[dict[str, str]] = []
    if roundtable_role == ConversationAgentRole.MAIN:
        participant_ids = (
            set(configuration.participant_ids) if configuration is not None else None
        )
        collaborators = session.exec(
            select(ConversationAgent).where(
                ConversationAgent.conversation_id == conversation.id,
                ConversationAgent.role == ConversationAgentRole.COLLABORATOR,
            )
        ).all()
        if participant_ids is not None:
            collaborators = [
                item for item in collaborators if str(item.id) in participant_ids
            ]
        roundtable_participants = [
            {
                "conversation_agent_id": str(item.id),
                "agent_id": str(item.agent_id),
                "agent_release_id": str(item.agent_release_id),
            }
            for item in collaborators
        ]
        if roundtable_participants:
            system_prompt = (
                f"{system_prompt}\n\n"
                "你是本次圆桌会话的主 Agent。需要协作时只能使用 "
                "neomua-roundtable 的 delegate_to_collaborator 工具，并且只能选择"
                "下列 conversation_agent_id。工具会等待真实协作任务完成并返回完整结果；"
                "协作失败必须如实说明，不得伪装成自己的结论。\n"
                f"固定协作成员：{json.dumps(roundtable_participants, ensure_ascii=False)}"
            )
            previous_main_task = session.exec(
                select(AgentTask)
                .where(AgentTask.session_id == participant.agent_session_id)
                .order_by(col(AgentTask.created_at).desc())
            ).first()
            if previous_main_task is not None:
                updates = session.exec(
                    select(ConversationMessage)
                    .where(
                        ConversationMessage.conversation_id == conversation.id,
                        ConversationMessage.id != user_message.id,
                        ConversationMessage.created_at > previous_main_task.created_at,
                    )
                    .order_by(col(ConversationMessage.sequence))
                ).all()
                roundtable_updates = [
                    {
                        "sequence": item.sequence,
                        "author_type": item.author_type.value,
                        "author_id": str(item.author_id) if item.author_id else None,
                        "target_type": item.target_type.value,
                        "target_agent_id": (
                            str(item.target_agent_id) if item.target_agent_id else None
                        ),
                        "status": item.status.value,
                        "payload": item.payload,
                        "error": item.error,
                    }
                    for item in updates
                ]
                if roundtable_updates:
                    effective_prompt = (
                        "以下是主 Agent 上一轮之后新增的完整圆桌记录，请先纳入主持上下文：\n"
                        f"{json.dumps(roundtable_updates, ensure_ascii=False)}\n\n"
                        f"当前用户消息：\n{effective_prompt}"
                    )
    task = AgentTask(
        namespace_id=conversation.namespace_id,
        session_id=participant.agent_session_id,
        runtime_profile_id=runtime.id,
        target_node_id=node_id,
        prompt=effective_prompt,
        snapshot={
            "conversation_id": str(conversation.id),
            "conversation_message_id": str(user_message.id),
            "route_mode": runtime.route_mode.value,
            "agent_id": str(release.agent_id),
            "agent_release_id": str(release.id),
            "agent_release_version": release.version,
            "resolved_spec_digest": release.resolved_spec_digest,
            "system_prompt": system_prompt,
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
            "conversation_configuration_revision_id": (
                str(configuration.id) if configuration is not None else None
            ),
            "roundtable_role": roundtable_role.value,
            "roundtable_participants": roundtable_participants,
        },
        agent_release_id=release.id,
        runtime_agent_release_id=binding.id,
        resolved_spec_digest=release.resolved_spec_digest,
        idempotency_key=f"conversation:{conversation.id}:message:{user_message.id}:agent:{participant.id}",
        created_by=(
            user_message.author_id
            if user_message.author_type == MessageAuthorType.USER
            else None
        ),
    )
    session.add(task)
    session.flush()
    return task


def create_runtime_delegation(
    session: Session,
    source_task: AgentTask,
    source_task_revision: int,
    target_conversation_agent_id: uuid.UUID,
    content: str,
) -> AgentDelegation:
    """Create a delegation authenticated by the currently executing main task."""
    if source_task.revision != source_task_revision or source_task.status not in {
        TaskStatus.DISPATCHED,
        TaskStatus.RUNNING,
        TaskStatus.AWAITING_APPROVAL,
    }:
        raise HTTPException(409, "Source Agent task is not active")
    if source_task.snapshot.get("roundtable_role") != ConversationAgentRole.MAIN.value:
        raise HTTPException(403, "Only the executing main Agent may delegate")
    conversation_id_value = source_task.snapshot.get("conversation_id")
    source_message_id_value = source_task.snapshot.get("conversation_message_id")
    if not conversation_id_value or not source_message_id_value:
        raise HTTPException(409, "Source task has no roundtable scope")
    conversation = session.get(Conversation, uuid.UUID(str(conversation_id_value)))
    source_message = session.get(
        ConversationMessage, uuid.UUID(str(source_message_id_value))
    )
    source = session.exec(
        select(ConversationAgent).where(
            ConversationAgent.agent_session_id == source_task.session_id,
            ConversationAgent.role == ConversationAgentRole.MAIN,
        )
    ).first()
    target = session.get(ConversationAgent, target_conversation_agent_id)
    allowed_ids = {
        str(item.get("conversation_agent_id"))
        for item in source_task.snapshot.get("roundtable_participants", [])
        if isinstance(item, dict)
    }
    if (
        conversation is None
        or source_message is None
        or source is None
        or target is None
        or source.conversation_id != conversation.id
        or target.conversation_id != conversation.id
        or target.role != ConversationAgentRole.COLLABORATOR
        or str(target.id) not in allowed_ids
    ):
        raise HTTPException(422, "Delegation target is outside the fixed roundtable")
    identity = canonical_digest(
        {
            "source_task_id": str(source_task.id),
            "source_task_revision": source_task_revision,
            "target_conversation_agent_id": str(target.id),
            "content": content,
        }
    )
    key = f"runtime:{source_task.id}:{identity}"
    existing = session.exec(
        select(AgentDelegation).where(
            AgentDelegation.conversation_id == conversation.id,
            AgentDelegation.idempotency_key == key,
        )
    ).first()
    if existing is not None:
        return existing
    delegated_message = ConversationMessage(
        conversation_id=conversation.id,
        sequence=next_message_sequence(session, conversation.id),
        author_type=MessageAuthorType.AGENT,
        author_id=source.id,
        target_type=MessageTargetType.AGENT,
        target_agent_id=target.id,
        context_snapshot_id=source_message.context_snapshot_id,
        configuration_revision_id=source_message.configuration_revision_id,
        payload={
            "content": content,
            "delegated": True,
            "source_task_id": str(source_task.id),
        },
        status=MessageStatus.RUNNING,
        reply_to_id=source_message.id,
        idempotency_key=f"delegation-message:{key}",
    )
    session.add(delegated_message)
    session.flush()
    target_task = create_agent_task(
        session, conversation, target, delegated_message, content
    )
    delegated_message.task_id = target_task.id
    delegation = AgentDelegation(
        conversation_id=conversation.id,
        source_message_id=source_message.id,
        source_agent_id=source.id,
        target_agent_id=target.id,
        input_payload={
            "content": content,
            "source_task_id": str(source_task.id),
            "source_task_revision": source_task_revision,
            "delegated_message_id": str(delegated_message.id),
        },
        task_id=target_task.id,
        status=DelegationStatus.RUNNING,
        idempotency_key=key,
    )
    session.add_all([delegated_message, delegation])
    session.flush()
    append_message_event(session, delegated_message, "message_created")
    append_conversation_event(
        session,
        conversation.id,
        "delegation_created",
        {
            "delegation_id": str(delegation.id),
            "source_agent_id": str(source.id),
            "target_agent_id": str(target.id),
            "task_id": str(target_task.id),
            "status": delegation.status.value,
        },
    )
    return delegation


def reconcile_agent_messages(session: Session, conversation: Conversation) -> bool:
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
        previous_message_status = message.status
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
            append_message_event(session, message, "message_updated")
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
                    reply = ConversationMessage(
                        conversation_id=conversation.id,
                        sequence=next_message_sequence(session, conversation.id),
                        author_type=MessageAuthorType.AGENT,
                        author_id=participant.id if participant else None,
                        target_type=MessageTargetType.SYSTEM,
                        context_snapshot_id=message.context_snapshot_id,
                        configuration_revision_id=message.configuration_revision_id,
                        payload=task.final_result or {},
                        status=MessageStatus.COMPLETED,
                        task_id=task.id,
                        reply_to_id=message.id,
                        idempotency_key=f"agent-reply:{task.id}",
                        completed_at=task.completed_at or utcnow(),
                    )
                    session.add(reply)
                    session.flush()
                    append_message_event(session, reply, "message_created")
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
        if message.status != previous_message_status:
            append_message_event(session, message, "message_updated")

    delegations = session.exec(
        select(AgentDelegation).where(
            AgentDelegation.conversation_id == conversation.id,
            col(AgentDelegation.status).in_(
                [DelegationStatus.QUEUED, DelegationStatus.RUNNING]
            ),
        )
    ).all()
    for delegation in delegations:
        previous_delegation_status = delegation.status
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
            delegated_message_id = delegation.input_payload.get("delegated_message_id")
            delegated_message = (
                session.get(ConversationMessage, uuid.UUID(str(delegated_message_id)))
                if delegated_message_id
                else None
            )
            if delegated_message is not None:
                delegated_message.status = MessageStatus.COMPLETED
                delegated_message.completed_at = delegation.completed_at
                existing_reply = session.exec(
                    select(ConversationMessage).where(
                        ConversationMessage.conversation_id == conversation.id,
                        ConversationMessage.idempotency_key
                        == f"delegation-reply:{delegation.id}",
                    )
                ).first()
                if existing_reply is None:
                    reply = ConversationMessage(
                        conversation_id=conversation.id,
                        sequence=next_message_sequence(session, conversation.id),
                        author_type=MessageAuthorType.AGENT,
                        author_id=delegation.target_agent_id,
                        target_type=MessageTargetType.MAIN,
                        context_snapshot_id=delegated_message.context_snapshot_id,
                        configuration_revision_id=delegated_message.configuration_revision_id,
                        payload=task.final_result or {},
                        status=MessageStatus.COMPLETED,
                        task_id=task.id,
                        reply_to_id=delegated_message.id,
                        idempotency_key=f"delegation-reply:{delegation.id}",
                        completed_at=delegation.completed_at,
                    )
                    session.add(reply)
                    session.flush()
                    append_message_event(session, reply, "message_created")
                session.add(delegated_message)
                append_message_event(session, delegated_message, "message_updated")
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
            delegated_message_id = delegation.input_payload.get("delegated_message_id")
            delegated_message = (
                session.get(ConversationMessage, uuid.UUID(str(delegated_message_id)))
                if delegated_message_id
                else None
            )
            if delegated_message is not None:
                delegated_message.status = MessageStatus.FAILED
                delegated_message.error = delegation.error
                delegated_message.completed_at = delegation.completed_at
                session.add(delegated_message)
                append_message_event(session, delegated_message, "message_updated")
            changed = True
        else:
            delegation.status = DelegationStatus.RUNNING
        if delegation.status != previous_delegation_status:
            append_conversation_event(
                session,
                conversation.id,
                "delegation_updated",
                {
                    "delegation_id": str(delegation.id),
                    "status": delegation.status.value,
                    "task_id": str(delegation.task_id) if delegation.task_id else None,
                    "error": delegation.error,
                },
            )
        session.add(delegation)
    if changed:
        session.flush()
    return changed


def conversation_public(session: Session, conversation: Conversation) -> dict[str, Any]:
    agents = session.exec(
        select(ConversationAgent).where(
            ConversationAgent.conversation_id == conversation.id
        )
    ).all()
    current = (
        session.get(
            ConversationConfigurationRevision,
            conversation.current_configuration_revision_id,
        )
        if conversation.current_configuration_revision_id
        else None
    )
    active_ids = set(current.participant_ids) if current is not None else {
        str(item.id) for item in agents
    }
    organizer_id = (
        str(current.organizer_agent_id)
        if current is not None and current.organizer_agent_id
        else next(
            (
                str(item.id)
                for item in agents
                if item.role == ConversationAgentRole.MAIN
            ),
            None,
        )
    )
    public_agents = [
        {
            **item.model_dump(mode="json"),
            "active": str(item.id) in active_ids,
            "role": (
                ConversationAgentRole.MAIN
                if str(item.id) == organizer_id
                else ConversationAgentRole.COLLABORATOR
            ),
        }
        for item in agents
    ]
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
        "current_configuration_revision_id": conversation.current_configuration_revision_id,
        "configuration": configuration_public(current) if current else None,
        "agents": public_agents,
        "created_at": conversation.created_at,
        "updated_at": conversation.updated_at,
        "archived_at": conversation.archived_at,
    }
