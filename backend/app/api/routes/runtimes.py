import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlmodel import col, select

from app.api.deps import (
    CurrentUser,
    SessionDep,
    require_namespace_admin,
    require_namespace_runtime_user,
)
from app.llm_provider_service import (
    open_secret_payload,
    seal_secret_payload,
)
from app.models import LlmProviderConfig, LlmProviderModel
from app.runtime.compatibility import (
    AnthropicCompatibilityError,
    check_anthropic_compatibility,
)
from app.runtime.endpoints import EndpointValidationError, canonical_endpoint
from app.runtime.gateway import UnsupportedGatewayProvider, gateway_provider_kind
from app.runtime.models import (
    AgentEvent,
    AgentEventType,
    AgentSession,
    AgentTask,
    RuntimeProfile,
    RuntimeRouteMode,
    RuntimeSecret,
    RuntimeType,
)
from app.runtime.policy import PermissionMode, TaskStatus, require_task_transition
from app.runtime.repository import append_and_apply_event

router = APIRouter(prefix="/runtimes", tags=["runtimes"])


class PlatformRuntimeUpsert(BaseModel):
    route_mode: RuntimeRouteMode
    model_id: str = Field(min_length=1, max_length=255)
    provider_config_id: uuid.UUID | None = None
    base_url: str | None = None
    permission_mode: PermissionMode = PermissionMode.DEFAULT
    secret_inputs: dict[str, str] | None = None


class PlatformRuntimePublic(BaseModel):
    id: uuid.UUID
    namespace_id: uuid.UUID
    route_mode: RuntimeRouteMode
    model_id: str
    provider_config_id: uuid.UUID | None
    base_url: str | None
    permission_mode: PermissionMode
    secret_masked: str | None
    compatibility_verified: bool


class SessionPublic(BaseModel):
    id: uuid.UUID
    runtime_profile_id: uuid.UUID
    sdk_session_id: str | None


class MessageInput(BaseModel):
    prompt: str = Field(min_length=1)


class TaskPublic(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID | None
    status: str


class EventPublic(BaseModel):
    sequence: int
    event_type: AgentEventType
    payload: dict[str, Any]


def _public(
    runtime: RuntimeProfile, secret: RuntimeSecret | None
) -> PlatformRuntimePublic:
    return PlatformRuntimePublic(
        id=runtime.id,
        namespace_id=runtime.namespace_id,
        route_mode=runtime.route_mode,
        model_id=runtime.model_id,
        provider_config_id=runtime.provider_config_id,
        base_url=runtime.base_url,
        permission_mode=PermissionMode(runtime.permission_mode),
        secret_masked=secret.secret_masked if secret else None,
        compatibility_verified=bool(runtime.config.get("compatibility_verified")),
    )


@router.put("/platform", response_model=PlatformRuntimePublic)
def upsert_platform_runtime(
    body: PlatformRuntimeUpsert,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> PlatformRuntimePublic:
    runtime = session.exec(
        select(RuntimeProfile).where(
            RuntimeProfile.namespace_id == namespace_id,
            RuntimeProfile.runtime_type == RuntimeType.PLATFORM,
        )
    ).first()
    existing_secret = (
        session.exec(
            select(RuntimeSecret).where(RuntimeSecret.runtime_profile_id == runtime.id)
        ).first()
        if runtime
        else None
    )
    canonical_base_url = None
    if body.base_url:
        try:
            canonical_base_url = canonical_endpoint(body.base_url)
        except EndpointValidationError as exc:
            raise HTTPException(422, str(exc)) from exc
    endpoint_changed = bool(runtime and canonical_base_url != runtime.base_url)
    if body.route_mode == RuntimeRouteMode.DIRECT_ANTHROPIC and (
        not canonical_base_url
        or (
            not body.secret_inputs
            and (existing_secret is None or endpoint_changed)
        )
    ):
        raise HTTPException(
            400,
            "direct_anthropic requires credentials when the endpoint is new or changed",
        )
    if body.route_mode == RuntimeRouteMode.PLATFORM_GATEWAY:
        provider = session.get(LlmProviderConfig, body.provider_config_id)
        if (
            provider is None
            or provider.namespace_id != namespace_id
            or not provider.enabled
        ):
            raise HTTPException(404, "Enabled provider config not found")
        try:
            gateway_provider_kind(provider.provider_slug)
        except UnsupportedGatewayProvider as exc:
            raise HTTPException(422, str(exc)) from exc
        model = session.exec(
            select(LlmProviderModel).where(
                LlmProviderModel.provider_config_id == provider.id,
                LlmProviderModel.model_id == body.model_id,
                col(LlmProviderModel.is_enabled).is_(True),
            )
        ).first()
        if model is None:
            raise HTTPException(400, "Enabled model not found")
    if runtime is None:
        runtime = RuntimeProfile(
            namespace_id=namespace_id,
            runtime_type=RuntimeType.PLATFORM,
            route_mode=body.route_mode,
            model_id=body.model_id,
        )
    previous_model = runtime.model_id
    runtime.route_mode = body.route_mode
    runtime.model_id = body.model_id
    runtime.provider_config_id = body.provider_config_id
    runtime.base_url = canonical_base_url
    runtime.permission_mode = body.permission_mode.value
    runtime.config = {
        **runtime.config,
        "compatibility_verified": body.route_mode == RuntimeRouteMode.PLATFORM_GATEWAY
        or (
            bool(runtime.config.get("compatibility_verified"))
            and not endpoint_changed
            and previous_model == body.model_id
            and not body.secret_inputs
        ),
    }
    session.add(runtime)
    session.flush()
    secret = existing_secret
    if body.secret_inputs:
        if secret is None:
            secret = RuntimeSecret(
                namespace_id=namespace_id,
                runtime_profile_id=runtime.id,
                secret_ciphertext="",
            )
        secret.secret_ciphertext = seal_secret_payload(body.secret_inputs) or ""
        secret.secret_masked = "****"
        session.add(secret)
    session.commit()
    session.refresh(runtime)
    return _public(runtime, secret)


@router.post("/platform/validate", response_model=PlatformRuntimePublic)
async def validate_platform_runtime(
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> PlatformRuntimePublic:
    runtime = session.exec(
        select(RuntimeProfile).where(
            RuntimeProfile.namespace_id == namespace_id,
            RuntimeProfile.runtime_type == RuntimeType.PLATFORM,
        )
    ).first()
    if runtime is None:
        raise HTTPException(404, "Platform runtime not configured")
    secret = session.exec(
        select(RuntimeSecret).where(RuntimeSecret.runtime_profile_id == runtime.id)
    ).first()
    if runtime.route_mode == RuntimeRouteMode.DIRECT_ANTHROPIC:
        if secret is None or not runtime.base_url:
            raise HTTPException(409, "Runtime direct credential is missing")
        values = open_secret_payload(secret.secret_ciphertext)
        api_key = (
            values.get("api_key") or values.get("api_token") or values.get("token")
        )
        if not api_key:
            raise HTTPException(409, "Runtime API key is missing")
        try:
            await check_anthropic_compatibility(
                runtime.base_url, api_key, runtime.model_id
            )
        except AnthropicCompatibilityError as exc:
            runtime.config = {**runtime.config, "compatibility_verified": False}
            session.add(runtime)
            session.commit()
            raise HTTPException(422, str(exc)) from exc
    runtime.config = {**runtime.config, "compatibility_verified": True}
    session.add(runtime)
    session.commit()
    return _public(runtime, secret)


@router.get("/platform", response_model=PlatformRuntimePublic)
def read_platform_runtime(
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> PlatformRuntimePublic:
    runtime = session.exec(
        select(RuntimeProfile).where(
            RuntimeProfile.namespace_id == namespace_id,
            RuntimeProfile.runtime_type == RuntimeType.PLATFORM,
        )
    ).first()
    if runtime is None:
        raise HTTPException(404, "Platform runtime not configured")
    secret = session.exec(
        select(RuntimeSecret).where(RuntimeSecret.runtime_profile_id == runtime.id)
    ).first()
    return _public(runtime, secret)


@router.post("/platform/sessions", response_model=SessionPublic, status_code=201)
def create_platform_session(
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> SessionPublic:
    runtime = session.exec(
        select(RuntimeProfile).where(
            RuntimeProfile.namespace_id == namespace_id,
            RuntimeProfile.runtime_type == RuntimeType.PLATFORM,
        )
    ).first()
    if runtime is None:
        raise HTTPException(409, "Platform runtime not configured")
    if not runtime.config.get("compatibility_verified"):
        raise HTTPException(409, "Platform runtime compatibility is not verified")
    agent_session = AgentSession(
        namespace_id=namespace_id,
        runtime_profile_id=runtime.id,
        created_by=current_user.id,
    )
    session.add(agent_session)
    session.commit()
    session.refresh(agent_session)
    return SessionPublic(
        id=agent_session.id,
        runtime_profile_id=agent_session.runtime_profile_id,
        sdk_session_id=agent_session.sdk_session_id,
    )


@router.post(
    "/sessions/{session_id}/messages", response_model=TaskPublic, status_code=202
)
def create_session_message(
    session_id: uuid.UUID,
    body: MessageInput,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> TaskPublic:
    agent_session = session.exec(
        select(AgentSession)
        .where(AgentSession.id == session_id)
        .with_for_update()
    ).first()
    if agent_session is None or agent_session.namespace_id != namespace_id:
        raise HTTPException(404, "Session not found")
    runtime = session.get(RuntimeProfile, agent_session.runtime_profile_id)
    if runtime is None:
        raise HTTPException(409, "Runtime is unavailable")
    active_task = session.exec(
        select(AgentTask).where(
            AgentTask.session_id == agent_session.id,
            col(AgentTask.status).in_(
                [
                    TaskStatus.QUEUED,
                    TaskStatus.DISPATCHED,
                    TaskStatus.RUNNING,
                    TaskStatus.CANCELLING,
                ]
            ),
        )
    ).first()
    if active_task is not None:
        raise HTTPException(
            409,
            detail={
                "code": "session_task_in_progress",
                "task_id": str(active_task.id),
            },
        )
    task = AgentTask(
        namespace_id=namespace_id,
        session_id=agent_session.id,
        runtime_profile_id=runtime.id,
        prompt=body.prompt,
        snapshot={
            "route_mode": runtime.route_mode.value,
            "model_id": runtime.model_id,
            "permission_mode": runtime.permission_mode,
            "tools": runtime.config.get("tools", []),
            "allowed_tools": runtime.config.get("allowed_tools", []),
            "disallowed_tools": runtime.config.get("disallowed_tools", []),
            "working_directory": runtime.config.get("cwd"),
            "timeout_seconds": runtime.config.get("timeout_seconds", 3600),
            "provider_config_id": str(runtime.provider_config_id)
            if runtime.provider_config_id
            else None,
            "base_url": runtime.base_url,
        },
        created_by=current_user.id,
    )
    session.add(task)
    session.flush()
    append_and_apply_event(
        session, task.id, 0, AgentEventType.USER_MESSAGE, {"text": body.prompt}
    )
    session.commit()
    session.refresh(task)
    return TaskPublic(id=task.id, session_id=task.session_id, status=task.status.value)


def _namespace_task(
    session: SessionDep, task_id: uuid.UUID, namespace_id: uuid.UUID
) -> AgentTask:
    task = session.get(AgentTask, task_id)
    if task is None or task.namespace_id != namespace_id:
        raise HTTPException(404, "Task not found")
    return task


@router.get("/tasks/{task_id}", response_model=TaskPublic)
def read_task(
    task_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> TaskPublic:
    task = _namespace_task(session, task_id, namespace_id)
    return TaskPublic(id=task.id, session_id=task.session_id, status=task.status.value)


@router.get("/tasks/{task_id}/events", response_model=list[EventPublic])
def read_task_events(
    task_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    after_sequence: int = -1,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> list[EventPublic]:
    _namespace_task(session, task_id, namespace_id)
    events = session.exec(
        select(AgentEvent)
        .where(
            AgentEvent.task_id == task_id,
            AgentEvent.sequence > after_sequence,
        )
        .order_by(col(AgentEvent.sequence))
    ).all()
    return [
        EventPublic(sequence=e.sequence, event_type=e.event_type, payload=e.payload)
        for e in events
    ]


@router.get("/tasks/{task_id}/stream", response_model=None)
async def stream_task_events(
    task_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    _: CurrentUser,
    after_sequence: int = -1,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> StreamingResponse:
    _namespace_task(session, task_id, namespace_id)
    try:
        cursor = (
            max(after_sequence, int(last_event_id))
            if last_event_id
            else after_sequence
        )
    except ValueError as exc:
        raise HTTPException(400, "Last-Event-ID must be an integer") from exc
    terminal = {
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.INTERRUPTED,
        TaskStatus.REJECTED,
    }

    async def generate() -> AsyncIterator[str]:
        nonlocal cursor
        while not await request.is_disconnected():
            session.expire_all()
            events = session.exec(
                select(AgentEvent)
                .where(
                    AgentEvent.task_id == task_id,
                    AgentEvent.sequence > cursor,
                )
                .order_by(col(AgentEvent.sequence))
            ).all()
            for event in events:
                cursor = event.sequence
                data = json.dumps(
                    {
                        "sequence": event.sequence,
                        "event_type": event.event_type.value,
                        "payload": event.payload,
                    },
                    ensure_ascii=False,
                )
                yield f"id: {event.sequence}\nevent: {event.event_type.value}\ndata: {data}\n\n"
            current = session.get(AgentTask, task_id)
            if current is None:
                break
            if current.status in terminal:
                if events:
                    continue
                break
            if not events:
                yield ": keepalive\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/tasks/{task_id}/cancel", response_model=TaskPublic)
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
        return TaskPublic(id=task.id, session_id=task.session_id, status=task.status.value)
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
    session.add(task)
    session.commit()
    return TaskPublic(id=task.id, session_id=task.session_id, status=task.status.value)
