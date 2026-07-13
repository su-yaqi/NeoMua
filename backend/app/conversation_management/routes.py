import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, or_, select

from app.agent_management.capability_models import AgentRelease, RuntimeAgentRelease
from app.agent_management.models import AgentDefinition
from app.api.deps import CurrentUser, SessionDep, require_namespace_member
from app.api.routes.runtime_artifacts import artifact_storage
from app.conversation_management.models import (
    AgentDelegation,
    AttachmentScanStatus,
    Conversation,
    ConversationAgentRole,
    ConversationAttachment,
    ConversationContextSnapshot,
    ConversationEvent,
    ConversationMessage,
    ConversationMode,
    ConversationStatus,
    ConversationVisibility,
    MessageAuthorType,
    MessageStatus,
    MessageTargetType,
)
from app.conversation_management.schemas import (
    ContextRefresh,
    ConversationCreate,
    ConversationDerive,
    ConversationMessageCreate,
    ConversationUpdate,
)
from app.conversation_management.service import (
    add_conversation_agent,
    append_conversation_event,
    append_message_event,
    canonical_digest,
    conversation_public,
    create_agent_task,
    create_context_snapshot,
    next_message_sequence,
    reconcile_agent_messages,
    require_conversation_access,
    target_participants,
    utcnow,
    validate_agent_binding,
    validate_chat_route,
)
from app.core.config import settings
from app.models import LlmProviderConfig, LlmProviderModel
from app.project_management.models import Project, ProjectMember, ProjectStatus
from app.project_management.service import require_project_member
from app.runtime.models import RuntimeProfile
from app.runtime.security import issue_gateway_token
from app.text_attachments import TEXT_ATTACHMENT_MAX_BYTES, store_text_attachment

router = APIRouter(tags=["conversations"])


def _require_idempotency(value: str | None) -> str:
    if not value:
        raise HTTPException(400, "Idempotency-Key is required")
    return value


def _project_for_conversation(
    session: SessionDep,
    project_id: uuid.UUID | None,
    namespace_id: uuid.UUID,
    current_user: CurrentUser,
) -> Project | None:
    if project_id is None:
        return None
    project = require_project_member(session, project_id, namespace_id, current_user)
    if project.status == ProjectStatus.ARCHIVED:
        raise HTTPException(409, "Project is archived and read-only")
    return project


def _attachment_public(value: ConversationAttachment) -> dict[str, Any]:
    return {
        "id": value.id,
        "conversation_id": value.conversation_id,
        "message_id": value.message_id,
        "filename": value.filename,
        "content_type": value.content_type,
        "size": value.size,
        "content_digest": value.content_digest,
        "scan_status": value.scan_status,
        "scan_details": value.scan_details,
        "created_at": value.created_at,
    }


def _attachment_text(value: ConversationAttachment) -> str:
    source = artifact_storage().open(value.storage_ref)
    try:
        content = source.read(TEXT_ATTACHMENT_MAX_BYTES + 1)
    finally:
        source.close()
    if len(content) > TEXT_ATTACHMENT_MAX_BYTES:
        raise HTTPException(409, "Stored attachment exceeds its verified size")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            409, "Stored attachment no longer passes UTF-8 scan"
        ) from exc


def _message_content_with_attachments(
    session: SessionDep,
    conversation: Conversation,
    content: str,
    attachment_ids: list[uuid.UUID],
) -> tuple[str, list[ConversationAttachment]]:
    if not attachment_ids:
        return content, []
    unique_ids = list(dict.fromkeys(attachment_ids))
    rows = session.exec(
        select(ConversationAttachment).where(
            ConversationAttachment.conversation_id == conversation.id,
            col(ConversationAttachment.id).in_(unique_ids),
        )
    ).all()
    by_id = {row.id: row for row in rows}
    if any(value not in by_id for value in unique_ids):
        raise HTTPException(422, "Attachment does not belong to this conversation")
    attachments = [by_id[value] for value in unique_ids]
    rejected = [
        str(value.id)
        for value in attachments
        if value.scan_status != AttachmentScanStatus.CLEAN
    ]
    if rejected:
        raise HTTPException(409, {"attachments_not_clean": rejected})
    already_used = [str(value.id) for value in attachments if value.message_id]
    if already_used:
        raise HTTPException(409, {"attachments_already_used": already_used})
    sections = [content]
    for value in attachments:
        sections.append(
            f"\n--- attachment: {value.filename} ({value.content_type}) ---\n"
            f"{_attachment_text(value)}"
        )
    return "\n".join(sections), attachments


@router.get("/conversation-catalog/runtimes")
def list_conversation_runtimes(
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> dict[str, Any]:
    rows = session.exec(
        select(RuntimeProfile).where(RuntimeProfile.namespace_id == namespace_id)
    ).all()
    data = [
        {
            "id": row.id,
            "runtime_type": row.runtime_type,
            "route_mode": row.route_mode,
            "model_id": row.model_id,
            "compatible": bool(row.config.get("compatibility_verified")),
        }
        for row in rows
    ]
    return {"data": data, "count": len(data)}


@router.get("/conversation-catalog/models")
def list_conversation_models(
    session: SessionDep,
    _: CurrentUser,
    runtime_id: uuid.UUID,
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> dict[str, Any]:
    runtime = session.get(RuntimeProfile, runtime_id)
    if runtime is None or runtime.namespace_id != namespace_id:
        raise HTTPException(404, "Runtime not found")
    if not runtime.config.get("compatibility_verified"):
        raise HTTPException(409, "Runtime compatibility is not verified")
    rows = session.exec(
        select(LlmProviderModel, LlmProviderConfig)
        .join(
            LlmProviderConfig,
            col(LlmProviderConfig.id) == col(LlmProviderModel.provider_config_id),
        )
        .where(
            LlmProviderConfig.namespace_id == namespace_id,
            col(LlmProviderConfig.enabled).is_(True),
            col(LlmProviderModel.is_enabled).is_(True),
        )
    ).all()
    data = [
        {
            "provider_config_id": provider.id,
            "provider_name": provider.config_name,
            "provider_slug": provider.provider_slug,
            "model_id": model.model_id,
            "display_name": model.display_name,
        }
        for model, provider in rows
    ]
    return {"data": data, "count": len(data)}


@router.get("/conversation-catalog/agents")
def list_conversation_agents(
    session: SessionDep,
    _: CurrentUser,
    runtime_id: uuid.UUID,
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> dict[str, Any]:
    runtime = session.get(RuntimeProfile, runtime_id)
    if runtime is None or runtime.namespace_id != namespace_id:
        raise HTTPException(404, "Runtime not found")
    rows = session.exec(
        select(RuntimeAgentRelease, AgentRelease, AgentDefinition)
        .join(
            AgentRelease,
            col(AgentRelease.id) == col(RuntimeAgentRelease.current_release_id),
        )
        .join(
            AgentDefinition,
            col(AgentDefinition.id) == col(RuntimeAgentRelease.agent_id),
        )
        .where(
            RuntimeAgentRelease.namespace_id == namespace_id,
            RuntimeAgentRelease.runtime_profile_id == runtime_id,
        )
    ).all()
    data = [
        {
            "runtime_agent_release_id": binding.id,
            "agent_id": agent.id,
            "agent_name": agent.name,
            "agent_slug": agent.slug,
            "release_id": release.id,
            "release_version": release.version,
            "resolved_spec_digest": release.resolved_spec_digest,
            "active": binding.applied_digest == release.resolved_spec_digest
            and binding.materialization_digest == release.resolved_spec_digest,
        }
        for binding, release, agent in rows
    ]
    return {"data": data, "count": len(data)}


@router.get("/conversations")
def list_conversations(
    session: SessionDep,
    current_user: CurrentUser,
    include_archived: bool = False,
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> dict[str, Any]:
    project_ids = session.exec(
        select(ProjectMember.project_id).where(ProjectMember.user_id == current_user.id)
    ).all()
    statement = select(Conversation).where(Conversation.namespace_id == namespace_id)
    if not current_user.is_superuser:
        statement = statement.where(
            or_(
                Conversation.creator_id == current_user.id,
                col(Conversation.project_id).in_(project_ids),
            )
        )
    if not include_archived:
        statement = statement.where(Conversation.status == ConversationStatus.ACTIVE)
    rows = session.exec(statement.order_by(col(Conversation.updated_at).desc())).all()
    return {
        "data": [conversation_public(session, row) for row in rows],
        "count": len(rows),
    }


@router.post("/conversations", status_code=201)
def create_conversation(
    body: ConversationCreate,
    session: SessionDep,
    current_user: CurrentUser,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> dict[str, Any]:
    key = _require_idempotency(idempotency_key)
    fingerprint = canonical_digest(
        {"operation": "create", "body": body.model_dump(mode="json")}
    )
    existing = session.exec(
        select(Conversation).where(
            Conversation.namespace_id == namespace_id,
            Conversation.creator_id == current_user.id,
            Conversation.idempotency_key == key,
        )
    ).first()
    if existing:
        if existing.creation_fingerprint != fingerprint:
            raise HTTPException(
                409, "Idempotency-Key was reused with a different request"
            )
        return conversation_public(session, existing)
    project = _project_for_conversation(
        session, body.project_id, namespace_id, current_user
    )
    if body.visibility == ConversationVisibility.PROJECT and project is None:
        raise HTTPException(422, "Project visibility requires project membership")
    runtime = session.get(RuntimeProfile, body.runtime_id)
    if runtime is None or runtime.namespace_id != namespace_id:
        raise HTTPException(422, "Runtime does not belong to the namespace")
    if body.mode == ConversationMode.CHAT:
        assert body.provider_config_id is not None and body.model_id is not None
        validate_chat_route(
            session,
            namespace_id,
            body.runtime_id,
            body.provider_config_id,
            body.model_id,
        )
    conversation = Conversation(
        namespace_id=namespace_id,
        creator_id=current_user.id,
        project_id=body.project_id,
        title=body.title,
        mode=body.mode,
        visibility=body.visibility,
        runtime_id=body.runtime_id,
        provider_config_id=body.provider_config_id,
        model_id=body.model_id,
        idempotency_key=key,
        creation_fingerprint=fingerprint,
    )
    session.add(conversation)
    session.flush()
    try:
        if body.mode == ConversationMode.AGENT:
            assert body.main_agent is not None
            binding_ids = [
                body.main_agent.runtime_agent_release_id,
                *[item.runtime_agent_release_id for item in body.collaborators],
            ]
            if len(binding_ids) != len(set(binding_ids)):
                raise HTTPException(422, "Each Agent can participate only once")
            for index, binding_id in enumerate(binding_ids):
                binding, release = validate_agent_binding(
                    session, namespace_id, body.runtime_id, binding_id
                )
                add_conversation_agent(
                    session,
                    conversation,
                    binding,
                    release,
                    ConversationAgentRole.MAIN
                    if index == 0
                    else ConversationAgentRole.COLLABORATOR,
                    current_user.id,
                )
        if project is not None:
            create_context_snapshot(session, conversation, current_user.id)
        session.commit()
    except HTTPException:
        session.rollback()
        raise
    except IntegrityError as exc:
        session.rollback()
        concurrent = session.exec(
            select(Conversation).where(
                Conversation.namespace_id == namespace_id,
                Conversation.creator_id == current_user.id,
                Conversation.idempotency_key == key,
            )
        ).first()
        if concurrent and concurrent.creation_fingerprint == fingerprint:
            return conversation_public(session, concurrent)
        raise HTTPException(409, "Conversation creation conflict") from exc
    session.refresh(conversation)
    return conversation_public(session, conversation)


@router.get("/conversations/{conversation_id}")
def read_conversation(
    conversation_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> dict[str, Any]:
    conversation = require_conversation_access(
        session, conversation_id, namespace_id, current_user
    )
    if conversation.mode == ConversationMode.AGENT:
        if reconcile_agent_messages(session, conversation):
            session.commit()
    return conversation_public(session, conversation)


@router.patch("/conversations/{conversation_id}")
def update_conversation(
    conversation_id: uuid.UUID,
    body: ConversationUpdate,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> dict[str, Any]:
    conversation = require_conversation_access(
        session, conversation_id, namespace_id, current_user, participate=True
    )
    if conversation.creator_id != current_user.id and not current_user.is_superuser:
        raise HTTPException(
            403, "Only the creator can rename or archive a conversation"
        )
    if body.title is not None:
        conversation.title = body.title
    if body.archived:
        conversation.status = ConversationStatus.ARCHIVED
        conversation.archived_at = utcnow()
        append_conversation_event(
            session,
            conversation.id,
            "conversation_archived",
            {"status": ConversationStatus.ARCHIVED.value},
        )
    conversation.updated_at = utcnow()
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation_public(session, conversation)


@router.get("/conversations/{conversation_id}/attachments")
def list_attachments(
    conversation_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> dict[str, Any]:
    conversation = require_conversation_access(
        session, conversation_id, namespace_id, current_user
    )
    rows = session.exec(
        select(ConversationAttachment)
        .where(ConversationAttachment.conversation_id == conversation.id)
        .order_by(col(ConversationAttachment.created_at))
    ).all()
    return {"data": [_attachment_public(row) for row in rows], "count": len(rows)}


@router.post("/conversations/{conversation_id}/attachments", status_code=201)
async def upload_attachment(
    conversation_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
    file: UploadFile = File(),
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> dict[str, Any]:
    conversation = require_conversation_access(
        session, conversation_id, namespace_id, current_user, participate=True
    )
    stored = await store_text_attachment(file, storage_prefix="conversations")
    existing = session.exec(
        select(ConversationAttachment).where(
            ConversationAttachment.conversation_id == conversation.id,
            ConversationAttachment.content_digest == stored["content_digest"],
        )
    ).first()
    if existing:
        return _attachment_public(existing)
    attachment = ConversationAttachment(
        conversation_id=conversation.id,
        **stored,
        scan_status=AttachmentScanStatus.CLEAN,
    )
    session.add(attachment)
    session.commit()
    session.refresh(attachment)
    return _attachment_public(attachment)


def _chat_history(
    session: SessionDep, conversation: Conversation
) -> tuple[list[dict[str, Any]], str | None]:
    rows = session.exec(
        select(ConversationMessage)
        .where(
            ConversationMessage.conversation_id == conversation.id,
            ConversationMessage.status == MessageStatus.COMPLETED,
            col(ConversationMessage.author_type).in_(
                [MessageAuthorType.USER, MessageAuthorType.MODEL]
            ),
        )
        .order_by(col(ConversationMessage.sequence))
    ).all()
    messages: list[dict[str, Any]] = []
    for row in rows:
        content = str(row.payload.get("content", ""))
        if row.author_type == MessageAuthorType.USER:
            attachment_ids = row.payload.get("attachment_ids", [])
            for attachment_id in (
                attachment_ids if isinstance(attachment_ids, list) else []
            ):
                try:
                    attachment = session.get(
                        ConversationAttachment, uuid.UUID(str(attachment_id))
                    )
                except ValueError:
                    attachment = None
                if attachment and attachment.message_id == row.id:
                    content += (
                        f"\n\n--- attachment: {attachment.filename} "
                        f"({attachment.content_type}) ---\n{_attachment_text(attachment)}"
                    )
        messages.append(
            {
                "role": "user"
                if row.author_type == MessageAuthorType.USER
                else "assistant",
                "content": content,
            }
        )
    system = None
    if conversation.current_context_snapshot_id:
        snapshot = session.get(
            ConversationContextSnapshot, conversation.current_context_snapshot_id
        )
        if snapshot:
            materialized = [
                (f"--- {item['path']} @ {item['blob_digest']} ---\n{item['content']}")
                for item in snapshot.content_refs
                if item.get("path")
                and item.get("blob_digest")
                and isinstance(item.get("content"), str)
            ]
            system = (
                "Use only the following immutable project context snapshot. "
                f"Snapshot digest: {snapshot.content_digest}.\n"
                + "\n".join(materialized)
            )
    return messages, system


async def _execute_chat_message(
    session: SessionDep,
    conversation: Conversation,
    assistant: ConversationMessage,
) -> None:
    if conversation.provider_config_id is None or conversation.model_id is None:
        raise HTTPException(409, "Conversation model route is incomplete")
    validate_chat_route(
        session,
        conversation.namespace_id,
        conversation.runtime_id,
        conversation.provider_config_id,
        conversation.model_id,
    )
    messages, system = _chat_history(session, conversation)
    body: dict[str, Any] = {
        "model": conversation.model_id,
        "max_tokens": 4096,
        "messages": messages,
    }
    if system:
        body["system"] = system
    token = issue_gateway_token(
        conversation.namespace_id,
        conversation.runtime_id,
        assistant.id,
        conversation.model_id,
    )
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{settings.MODEL_GATEWAY_URL.rstrip('/')}/tasks/{assistant.id}/v1/messages",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Runtime-ID": str(conversation.runtime_id),
                },
                json=body,
            )
        response.raise_for_status()
        payload = response.json()
        content_blocks = payload.get("content", [])
        content = "\n".join(
            str(block.get("text", ""))
            for block in content_blocks
            if isinstance(block, dict) and block.get("type") == "text"
        )
        if not content:
            raise ValueError("Model response contains no text content")
        assistant.payload = {
            "content": content,
            "model_response": {
                "id": payload.get("id"),
                "model": payload.get("model"),
                "stop_reason": payload.get("stop_reason"),
                "usage": payload.get("usage"),
            },
        }
        assistant.status = MessageStatus.COMPLETED
        assistant.completed_at = utcnow()
    except (httpx.HTTPError, ValueError) as exc:
        assistant.status = MessageStatus.FAILED
        assistant.error = {
            "code": "chat_model_execution_failed",
            "message": str(exc),
        }
        assistant.completed_at = utcnow()
    session.add(assistant)
    append_message_event(session, assistant, "message_updated")
    conversation.updated_at = utcnow()
    session.add(conversation)
    session.commit()


@router.post("/conversations/{conversation_id}/messages", status_code=202)
async def create_message(
    conversation_id: uuid.UUID,
    body: ConversationMessageCreate,
    session: SessionDep,
    current_user: CurrentUser,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> dict[str, Any]:
    key = _require_idempotency(idempotency_key)
    conversation = require_conversation_access(
        session, conversation_id, namespace_id, current_user, participate=True
    )
    existing = session.exec(
        select(ConversationMessage).where(
            ConversationMessage.conversation_id == conversation.id,
            ConversationMessage.idempotency_key == key,
        )
    ).first()
    if existing:
        return {"message": existing}
    locked = session.exec(
        select(Conversation).where(Conversation.id == conversation.id).with_for_update()
    ).one()
    concurrent = session.exec(
        select(ConversationMessage).where(
            ConversationMessage.conversation_id == conversation.id,
            ConversationMessage.idempotency_key == key,
        )
    ).first()
    if concurrent:
        return {"message": concurrent}
    active = session.exec(
        select(ConversationMessage).where(
            ConversationMessage.conversation_id == conversation.id,
            col(ConversationMessage.status).in_(
                [MessageStatus.QUEUED, MessageStatus.RUNNING]
            ),
        )
    ).first()
    if active:
        raise HTTPException(
            409,
            {"code": "conversation_turn_in_progress", "message_id": str(active.id)},
        )
    if conversation.mode == ConversationMode.CHAT:
        if body.target_type != MessageTargetType.MODEL:
            raise HTTPException(422, "Chat messages must target the fixed model")
    elif body.target_type not in {
        MessageTargetType.MAIN,
        MessageTargetType.AGENT,
        MessageTargetType.ALL,
    }:
        raise HTTPException(422, "Invalid Agent conversation target")
    effective_content, attachments = _message_content_with_attachments(
        session, conversation, body.content, body.attachment_ids
    )
    user_message = ConversationMessage(
        conversation_id=conversation.id,
        sequence=next_message_sequence(session, conversation.id),
        author_type=MessageAuthorType.USER,
        author_id=current_user.id,
        target_type=body.target_type,
        target_agent_id=body.target_agent_id,
        context_snapshot_id=conversation.current_context_snapshot_id,
        payload={
            "content": body.content,
            "attachment_ids": [str(value.id) for value in attachments],
        },
        status=MessageStatus.RUNNING,
        idempotency_key=key,
    )
    session.add(user_message)
    session.flush()
    for attachment in attachments:
        attachment.message_id = user_message.id
        session.add(attachment)
    response_message: ConversationMessage | None = None
    if conversation.mode == ConversationMode.CHAT:
        user_message.status = MessageStatus.COMPLETED
        user_message.completed_at = utcnow()
        response_message = ConversationMessage(
            conversation_id=conversation.id,
            sequence=next_message_sequence(session, conversation.id),
            author_type=MessageAuthorType.MODEL,
            target_type=MessageTargetType.SYSTEM,
            context_snapshot_id=conversation.current_context_snapshot_id,
            payload={},
            status=MessageStatus.RUNNING,
            reply_to_id=user_message.id,
            idempotency_key=f"chat-reply:{key}",
        )
        session.add(response_message)
        session.flush()
        append_message_event(session, user_message, "message_created")
        append_message_event(session, response_message, "message_created")
        session.commit()
        await _execute_chat_message(session, locked, response_message)
    else:
        participants = target_participants(
            session, conversation, body.target_type, body.target_agent_id
        )
        tasks = [
            create_agent_task(
                session, conversation, participant, user_message, effective_content
            )
            for participant in participants
        ]
        user_message.task_id = tasks[0].id if len(tasks) == 1 else None
        user_message.payload = {
            "content": body.content,
            "task_ids": [str(task.id) for task in tasks],
            "attachment_ids": [str(value.id) for value in attachments],
        }
        session.add(user_message)
        append_message_event(session, user_message, "message_created")
        conversation.updated_at = utcnow()
        session.add(conversation)
        session.commit()
    session.refresh(user_message)
    if response_message:
        session.refresh(response_message)
    return {"message": user_message, "response": response_message}


@router.get("/conversations/{conversation_id}/messages")
def list_messages(
    conversation_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
    after_sequence: int = 0,
    limit: int = 100,
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> dict[str, Any]:
    conversation = require_conversation_access(
        session, conversation_id, namespace_id, current_user
    )
    if conversation.mode == ConversationMode.AGENT:
        if reconcile_agent_messages(session, conversation):
            session.commit()
    rows = session.exec(
        select(ConversationMessage)
        .where(
            ConversationMessage.conversation_id == conversation.id,
            ConversationMessage.sequence > after_sequence,
        )
        .order_by(col(ConversationMessage.sequence))
        .limit(min(limit, 200))
    ).all()
    return {"data": rows, "count": len(rows)}


@router.get("/conversations/{conversation_id}/events", response_model=None)
async def list_conversation_events(
    conversation_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    current_user: CurrentUser,
    after_sequence: int = 0,
    accept: str | None = Header(default=None, alias="Accept"),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> dict[str, Any] | StreamingResponse:
    conversation = require_conversation_access(
        session, conversation_id, namespace_id, current_user
    )
    if accept and "text/event-stream" in accept:
        try:
            cursor = (
                max(after_sequence, int(last_event_id))
                if last_event_id
                else after_sequence
            )
        except ValueError as exc:
            raise HTTPException(400, "Last-Event-ID must be an integer") from exc

        async def generate() -> AsyncIterator[str]:
            nonlocal cursor
            while not await request.is_disconnected():
                session.expire_all()
                current = session.get(Conversation, conversation.id)
                if current is None:
                    break
                if current.mode == ConversationMode.AGENT and reconcile_agent_messages(
                    session, current
                ):
                    session.commit()
                events = session.exec(
                    select(ConversationEvent)
                    .where(
                        ConversationEvent.conversation_id == current.id,
                        ConversationEvent.sequence > cursor,
                    )
                    .order_by(col(ConversationEvent.sequence))
                    .limit(200)
                ).all()
                for event in events:
                    cursor = event.sequence
                    data = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
                    yield (
                        f"id: {event.sequence}\n"
                        f"event: {event.event_type}\n"
                        f"data: {data}\n\n"
                    )
                if current.status == ConversationStatus.ARCHIVED and not events:
                    break
                if not events:
                    yield ": keepalive\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(generate(), media_type="text/event-stream")
    if conversation.mode == ConversationMode.AGENT and reconcile_agent_messages(
        session, conversation
    ):
        session.commit()
    rows = session.exec(
        select(ConversationEvent)
        .where(
            ConversationEvent.conversation_id == conversation.id,
            ConversationEvent.sequence > after_sequence,
        )
        .order_by(col(ConversationEvent.sequence))
        .limit(200)
    ).all()
    return {"data": rows, "count": len(rows)}


@router.get("/conversations/{conversation_id}/delegations")
def list_delegations(
    conversation_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> dict[str, Any]:
    conversation = require_conversation_access(
        session, conversation_id, namespace_id, current_user
    )
    if reconcile_agent_messages(session, conversation):
        session.commit()
    rows = session.exec(
        select(AgentDelegation)
        .where(AgentDelegation.conversation_id == conversation_id)
        .order_by(col(AgentDelegation.created_at))
    ).all()
    return {"data": rows, "count": len(rows)}


@router.post("/conversations/{conversation_id}/context-snapshots", status_code=201)
def refresh_context_snapshot(
    conversation_id: uuid.UUID,
    _body: ContextRefresh,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> ConversationContextSnapshot:
    conversation = require_conversation_access(
        session, conversation_id, namespace_id, current_user, participate=True
    )
    snapshot = create_context_snapshot(
        session,
        conversation,
        current_user.id,
    )
    session.commit()
    session.refresh(snapshot)
    return snapshot


@router.post("/conversations/{conversation_id}/derive", status_code=201)
def derive_conversation(
    conversation_id: uuid.UUID,
    body: ConversationDerive,
    session: SessionDep,
    current_user: CurrentUser,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> dict[str, Any]:
    key = _require_idempotency(idempotency_key)
    source = require_conversation_access(
        session, conversation_id, namespace_id, current_user
    )
    if source.mode != ConversationMode.CHAT or source.creator_id != current_user.id:
        raise HTTPException(403, "Only the creator can derive a Chat conversation")
    fingerprint = canonical_digest(
        {
            "operation": "derive",
            "source_conversation_id": str(source.id),
            "body": body.model_dump(mode="json"),
        }
    )
    existing = session.exec(
        select(Conversation).where(
            Conversation.namespace_id == namespace_id,
            Conversation.creator_id == current_user.id,
            Conversation.idempotency_key == key,
        )
    ).first()
    if existing:
        if existing.creation_fingerprint != fingerprint:
            raise HTTPException(
                409, "Idempotency-Key was reused with a different request"
            )
        return conversation_public(session, existing)
    validate_chat_route(
        session,
        namespace_id,
        body.runtime_id,
        body.provider_config_id,
        body.model_id,
    )
    derived = Conversation(
        namespace_id=namespace_id,
        creator_id=current_user.id,
        project_id=source.project_id,
        source_conversation_id=source.id,
        title=body.title or f"{source.title}（派生）",
        mode=ConversationMode.CHAT,
        visibility=source.visibility,
        runtime_id=body.runtime_id,
        provider_config_id=body.provider_config_id,
        model_id=body.model_id,
        idempotency_key=key,
        creation_fingerprint=fingerprint,
    )
    session.add(derived)
    try:
        session.flush()
        if source.project_id:
            create_context_snapshot(session, derived, current_user.id)
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        concurrent = session.exec(
            select(Conversation).where(
                Conversation.namespace_id == namespace_id,
                Conversation.creator_id == current_user.id,
                Conversation.idempotency_key == key,
            )
        ).first()
        if concurrent and concurrent.creation_fingerprint == fingerprint:
            return conversation_public(session, concurrent)
        raise HTTPException(409, "Conversation derivation conflict") from exc
    session.refresh(derived)
    return conversation_public(session, derived)
