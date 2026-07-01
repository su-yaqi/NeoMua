import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from app.api.deps import SessionDep
from app.core.config import settings
from app.llm_provider_service import open_secret_payload
from app.models import LlmProviderConfig
from app.runtime.models import (
    AgentEventType,
    AgentSession,
    AgentTask,
    RuntimeProfile,
    RuntimeRouteMode,
    RuntimeSecret,
    RuntimeType,
)
from app.runtime.policy import InvalidTaskTransition, TaskStatus, require_task_transition
from app.runtime.repository import append_event_idempotent
from app.runtime.security import (
    GatewayScopeError,
    issue_gateway_token,
    require_internal_runtime,
    verify_gateway_token,
)

router = APIRouter(
    prefix="/internal/runtime",
    tags=["runtime-internal"],
    dependencies=[Depends(require_internal_runtime)],
)


class EventInput(BaseModel):
    sequence: int
    event_type: AgentEventType
    payload: dict


class EventBatch(BaseModel):
    task_id: uuid.UUID
    events: list[EventInput]


class ClaimInput(BaseModel):
    worker_id: str


@router.post("/events")
def append_events(body: EventBatch, session: SessionDep) -> dict[str, int]:
    task = session.get(AgentTask, body.task_id)
    if task is None:
        raise HTTPException(404, "Task not found")
    for item in body.events:
        try:
            append_event_idempotent(
                session, task, item.sequence, item.event_type, item.payload
            )
            _apply_event_state(task, item.event_type, item.payload)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        session.add(task)
        session.commit()
    return {"accepted": len(body.events)}


def _transition_task(task: AgentTask, target: TaskStatus) -> None:
    if task.status == target:
        return
    require_task_transition(task.status, target)
    task.status = target


def _apply_event_state(
    task: AgentTask, event_type: AgentEventType, payload: dict
) -> None:
    try:
        if event_type in {
            AgentEventType.ASSISTANT_MESSAGE,
            AgentEventType.TOOL_CALL,
            AgentEventType.TOOL_RESULT,
        } and task.status == TaskStatus.DISPATCHED:
            _transition_task(task, TaskStatus.RUNNING)
        elif event_type == AgentEventType.STATUS:
            state = payload.get("state")
            if state in {status.value for status in TaskStatus}:
                _transition_task(task, TaskStatus(state))
        elif event_type in {AgentEventType.RESULT, AgentEventType.ERROR}:
            target = (
                TaskStatus.SUCCEEDED
                if event_type == AgentEventType.RESULT
                else TaskStatus.FAILED
            )
            if task.status == TaskStatus.DISPATCHED:
                _transition_task(task, TaskStatus.RUNNING)
            if task.status == target:
                return
            _transition_task(task, target)
            task.final_result = payload
    except InvalidTaskTransition as exc:
        raise ValueError(str(exc)) from exc


@router.post("/tasks/claim")
def claim_platform_task(body: ClaimInput, session: SessionDep) -> dict:
    task = session.exec(
        select(AgentTask)
        .join(RuntimeProfile, AgentTask.runtime_profile_id == RuntimeProfile.id)
        .where(
            AgentTask.status == TaskStatus.QUEUED,
            RuntimeProfile.runtime_type == RuntimeType.PLATFORM,
        )
        .order_by(AgentTask.created_at)
        .with_for_update(skip_locked=True)
    ).first()
    if task is None:
        raise HTTPException(204)
    runtime = session.get(RuntimeProfile, task.runtime_profile_id)
    if runtime is None:
        raise HTTPException(409, "Runtime is unavailable")
    task.status = TaskStatus.DISPATCHED
    task.claimed_by = body.worker_id
    task.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    session.add(task)
    session.commit()
    env: dict[str, str]
    if runtime.route_mode == RuntimeRouteMode.DIRECT_ANTHROPIC:
        secret = session.exec(select(RuntimeSecret).where(
            RuntimeSecret.runtime_profile_id == runtime.id
        )).first()
        if secret is None:
            raise HTTPException(409, "Runtime credential is not configured")
        values = open_secret_payload(secret.secret_ciphertext)
        api_key = values.get("api_key") or values.get("api_token") or values.get("token")
        if not api_key:
            raise HTTPException(409, "Runtime API key is missing")
        env = {"ANTHROPIC_BASE_URL": runtime.base_url or "", "ANTHROPIC_API_KEY": api_key}
    else:
        gateway_token = issue_gateway_token(
            task.namespace_id, runtime.id, task.id, runtime.model_id
        )
        env = {
            "ANTHROPIC_BASE_URL": settings.MODEL_GATEWAY_URL,
            "ANTHROPIC_API_KEY": gateway_token,
            "ANTHROPIC_CUSTOM_HEADERS": f"X-Runtime-ID: {runtime.id}",
        }
    agent_session = session.get(AgentSession, task.session_id) if task.session_id else None
    return {
        "task_id": str(task.id),
        "revision": task.revision,
        "command": {
            "prompt": task.prompt,
            "model": runtime.model_id,
            "permission_mode": runtime.permission_mode,
            "tools": runtime.config.get("tools", []),
            "allowed_tools": runtime.config.get("allowed_tools", []),
            "disallowed_tools": runtime.config.get("disallowed_tools", []),
            "cwd": runtime.config.get("cwd"),
            "env": env,
            "sdk_session_id": agent_session.sdk_session_id if agent_session else None,
        },
    }


@router.get("/routes/{runtime_id}")
def resolve_route(
    runtime_id: uuid.UUID,
    model_id: str,
    session: SessionDep,
    authorization: str | None = Header(default=None),
) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Gateway route token required")
    try:
        claims = verify_gateway_token(
            authorization[7:], runtime_id=runtime_id, model_id=model_id
        )
    except GatewayScopeError as exc:
        raise HTTPException(403, str(exc))
    runtime = session.get(RuntimeProfile, runtime_id)
    if runtime is None or str(runtime.namespace_id) != claims["namespace_id"]:
        raise HTTPException(404, "Runtime route not found")
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
    provider = session.get(LlmProviderConfig, runtime.provider_config_id)
    if provider is None or not provider.enabled:
        raise HTTPException(409, "Provider config is unavailable")
    return {
        "provider_kind": "anthropic" if provider.provider_slug == "anthropic" else "openai_compatible",
        "base_url": provider.base_url,
        "model_id": runtime.model_id,
        "secret_inputs": open_secret_payload(provider.secret_ciphertext),
    }
