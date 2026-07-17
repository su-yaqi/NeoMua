"""Control-plane APIs for Skill synchronization, independent from task use."""

import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_
from sqlmodel import col, select

from app.agent_management.capability_models import (
    AgentActivation,
    AgentDeployment,
    AgentDeploymentStatus,
    AgentRelease,
    RuntimeAgentRelease,
    SkillDefinition,
    SkillVersion,
)
from app.api.deps import (
    CurrentUser,
    SessionDep,
    require_namespace_admin,
    require_namespace_runtime_user,
)
from app.runtime.models import (
    AgentEvent,
    AgentEventType,
    AgentTask,
    AgentTaskSkillUsage,
    RuntimeNode,
    RuntimeProfile,
    RuntimeSkillState,
    RuntimeSkillSyncAttempt,
    RuntimeType,
)
from app.runtime.policy import TaskStatus, require_task_transition
from app.runtime.security import require_internal_runtime
from app.runtime.skill_sync import (
    SkillDownloadTokenError,
    ensure_skill_version_signature,
    signed_skill_manifest,
    skill_storage,
    verify_skill_download_token,
)

router = APIRouter(tags=["runtime-skills"])
internal_router = APIRouter(
    prefix="/internal/runtime/skill-sync",
    tags=["internal-runtime-skills"],
    dependencies=[Depends(require_internal_runtime)],
)
node_router = APIRouter(prefix="/node/skill-sync", tags=["node-runtime-skills"])


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SkillSyncResult(StrictBody):
    status: str
    content_sha256: str = Field(min_length=64, max_length=64)
    bytes_downloaded: int = Field(default=0, ge=0)
    error: dict[str, Any] | None = None


class SkillUsageItem(StrictBody):
    skill_id: uuid.UUID
    version_id: uuid.UUID
    version: str
    content_sha256: str = Field(min_length=64, max_length=64)
    runtime_generation: int = Field(ge=1)


class TaskSkillUsageReport(StrictBody):
    runtime_profile_id: uuid.UUID
    skills: list[SkillUsageItem]


class TaskPreparationFailure(StrictBody):
    runtime_profile_id: uuid.UUID
    revision: int
    code: str
    message: str


def _state_public(session: SessionDep, state: RuntimeSkillState) -> dict[str, Any]:
    skill = session.get(SkillDefinition, state.skill_id)
    desired = (
        session.get(SkillVersion, state.desired_version_id)
        if state.desired_version_id
        else None
    )
    applied = (
        session.get(SkillVersion, state.applied_version_id)
        if state.applied_version_id
        else None
    )
    return {
        "id": state.id,
        "runtime_profile_id": state.runtime_profile_id,
        "skill_id": state.skill_id,
        "skill_slug": skill.slug if skill else None,
        "desired_version_id": state.desired_version_id,
        "desired_version": desired.version if desired else None,
        "desired_digest": state.desired_digest,
        "applied_version_id": state.applied_version_id,
        "applied_version": applied.version if applied else None,
        "applied_digest": state.applied_digest,
        "generation": state.generation,
        "applied_generation": state.applied_generation,
        "status": state.status,
        "subscription_count": state.subscription_count,
        "retry_count": state.retry_count,
        "next_retry_at": state.next_retry_at,
        "last_error": state.last_error,
        "last_reconciled_at": state.last_reconciled_at,
        "applied_at": state.applied_at,
        "updated_at": state.updated_at,
    }


def create_sync_attempt(
    session: SessionDep, state: RuntimeSkillState, *, trigger: str
) -> tuple[RuntimeSkillSyncAttempt, SkillDefinition, SkillVersion]:
    if state.desired_version_id is None or state.desired_digest is None:
        raise ValueError("Runtime Skill state has no desired bundle")
    skill = session.get(SkillDefinition, state.skill_id)
    version = session.get(SkillVersion, state.desired_version_id)
    if (
        skill is None
        or version is None
        or version.skill_id != skill.id
        or version.content_sha256 != state.desired_digest
        or version.deprecated
    ):
        raise ValueError("Desired Skill bundle is invalid")
    releases: dict[uuid.UUID, AgentRelease] = {}
    for binding in session.exec(
        select(RuntimeAgentRelease).where(
            RuntimeAgentRelease.runtime_profile_id == state.runtime_profile_id
        )
    ).all():
        release = session.get(AgentRelease, binding.current_release_id)
        if release is not None:
            releases[release.id] = release
    deployments = session.exec(
        select(AgentDeployment).where(
            AgentDeployment.runtime_profile_id == state.runtime_profile_id,
            AgentDeployment.status.in_(
                [AgentDeploymentStatus.PENDING, AgentDeploymentStatus.DISPATCHED]
            ),
        )
    ).all()
    for deployment in deployments:
        activation = session.get(AgentActivation, deployment.activation_id)
        release = (
            session.get(AgentRelease, activation.release_id) if activation else None
        )
        if release is not None:
            releases[release.id] = release
    compatibility_errors: list[dict[str, Any]] = []
    required_tools = set(version.required_capabilities.get("tools", []))
    required_mcp_tools = set(version.required_capabilities.get("mcp_tools", []))
    for release in releases.values():
        if not any(
            str(item.get("id")) == str(skill.id)
            for item in release.resolved_spec.get("skills", [])
        ):
            continue
        available_tools = {
            str(item["key"])
            for item in release.resolved_spec.get("tools", [])
            if item.get("policy") not in {"deny", "disabled", "forbidden"}
        }
        available_mcp_tools = {
            str(tool)
            for server in release.resolved_spec.get("mcp_servers", [])
            for tool in server.get("allowed_tools", [])
        }
        missing_tools = sorted(required_tools - available_tools)
        missing_mcp_tools = sorted(required_mcp_tools - available_mcp_tools)
        if missing_tools or missing_mcp_tools:
            compatibility_errors.append(
                {
                    "release_id": str(release.id),
                    "missing_tools": missing_tools,
                    "missing_mcp_tools": missing_mcp_tools,
                }
            )
    if compatibility_errors:
        state.status = "blocked"
        state.last_error = {
            "code": "skill_version_capability_incompatible",
            "releases": compatibility_errors,
        }
        state.updated_at = datetime.now(timezone.utc)
        session.add(state)
        raise ValueError("Skill current version expands required capabilities")
    ensure_skill_version_signature(skill, version)
    latest_attempt = session.exec(
        select(RuntimeSkillSyncAttempt.attempt_no)
        .where(RuntimeSkillSyncAttempt.runtime_skill_state_id == state.id)
        .order_by(col(RuntimeSkillSyncAttempt.attempt_no).desc())
    ).first()
    stale_attempts = session.exec(
        select(RuntimeSkillSyncAttempt).where(
            RuntimeSkillSyncAttempt.runtime_skill_state_id == state.id,
            RuntimeSkillSyncAttempt.status.in_(["syncing", "verified"]),
        )
    ).all()
    for stale in stale_attempts:
        stale.status = "superseded"
        stale.completed_at = datetime.now(timezone.utc)
        session.add(stale)
    attempt = RuntimeSkillSyncAttempt(
        runtime_skill_state_id=state.id,
        attempt_no=(latest_attempt or 0) + 1,
        generation=state.generation,
        version_id=version.id,
        content_sha256=version.content_sha256,
        trigger=trigger,
        status="syncing",
    )
    state.status = "syncing"
    state.last_reconciled_at = datetime.now(timezone.utc)
    state.updated_at = datetime.now(timezone.utc)
    session.add(version)
    session.add(state)
    session.add(attempt)
    session.flush()
    return attempt, skill, version


def sync_payload(
    attempt: RuntimeSkillSyncAttempt,
    skill: SkillDefinition,
    version: SkillVersion,
) -> dict[str, Any]:
    return {
        "attempt_id": str(attempt.id),
        "runtime_skill_state_id": str(attempt.runtime_skill_state_id),
        "generation": attempt.generation,
        "manifest": signed_skill_manifest(skill, version),
        "manifest_digest": version.manifest_digest,
        "signature": version.signature,
        "signing_public_key": version.signing_public_key,
    }


@router.get("/skills/{skill_id}/runtime-sync")
def skill_runtime_sync_status(
    skill_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> dict[str, Any]:
    skill = session.get(SkillDefinition, skill_id)
    if skill is None or skill.namespace_id != namespace_id:
        raise HTTPException(404, "Skill not found")
    states = session.exec(
        select(RuntimeSkillState)
        .where(RuntimeSkillState.skill_id == skill.id)
        .order_by(col(RuntimeSkillState.updated_at).desc())
    ).all()
    return {
        "data": [_state_public(session, row) for row in states],
        "count": len(states),
    }


@router.get("/runtimes/{runtime_id}/skills")
def runtime_skill_status(
    runtime_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> dict[str, Any]:
    runtime = session.get(RuntimeProfile, runtime_id)
    if runtime is None or runtime.namespace_id != namespace_id:
        raise HTTPException(404, "Runtime not found")
    states = session.exec(
        select(RuntimeSkillState)
        .where(RuntimeSkillState.runtime_profile_id == runtime.id)
        .order_by(col(RuntimeSkillState.updated_at).desc())
    ).all()
    return {
        "data": [_state_public(session, row) for row in states],
        "count": len(states),
    }


@router.post("/runtime-skill-states/{state_id}/retry")
def retry_runtime_skill_sync(
    state_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    state = session.exec(
        select(RuntimeSkillState)
        .where(
            RuntimeSkillState.id == state_id,
            RuntimeSkillState.namespace_id == namespace_id,
        )
        .with_for_update()
    ).first()
    if state is None:
        raise HTTPException(404, "Runtime Skill state not found")
    if state.status != "failed":
        raise HTTPException(409, "Only failed Skill synchronization can be retried")
    state.status = "pending"
    state.retry_count = 0
    state.next_retry_at = None
    state.last_error = None
    state.updated_at = datetime.now(timezone.utc)
    session.add(state)
    session.commit()
    return _state_public(session, state)


@router.post("/skills/{skill_id}/runtime-sync/{runtime_id}/retry")
def retry_skill_runtime_target(
    skill_id: uuid.UUID,
    runtime_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    state = session.exec(
        select(RuntimeSkillState)
        .where(
            RuntimeSkillState.namespace_id == namespace_id,
            RuntimeSkillState.skill_id == skill_id,
            RuntimeSkillState.runtime_profile_id == runtime_id,
        )
        .with_for_update()
    ).first()
    if state is None:
        raise HTTPException(404, "Runtime Skill state not found")
    if state.status != "failed":
        raise HTTPException(409, "Only failed Skill synchronization can be retried")
    state.status = "pending"
    state.retry_count = 0
    state.next_retry_at = None
    state.last_error = None
    state.updated_at = datetime.now(timezone.utc)
    session.add(state)
    session.commit()
    return _state_public(session, state)


@internal_router.post("/claim")
def claim_platform_skill_sync(
    session: SessionDep, response: Response
) -> dict[str, Any] | None:
    state = session.exec(
        select(RuntimeSkillState)
        .join(
            RuntimeProfile,
            col(RuntimeSkillState.runtime_profile_id) == RuntimeProfile.id,
        )
        .where(
            RuntimeProfile.runtime_type == RuntimeType.PLATFORM,
            RuntimeSkillState.subscription_count > 0,
            or_(
                RuntimeSkillState.status == "pending",
                (
                    (RuntimeSkillState.status == "failed")
                    & (RuntimeSkillState.retry_count < 5)
                    & (RuntimeSkillState.next_retry_at <= datetime.now(timezone.utc))
                ),
                (
                    (RuntimeSkillState.status == "syncing")
                    & (
                        RuntimeSkillState.updated_at
                        <= datetime.now(timezone.utc) - timedelta(minutes=10)
                    )
                ),
                (
                    (RuntimeSkillState.status == "committing")
                    & (
                        RuntimeSkillState.updated_at
                        <= datetime.now(timezone.utc) - timedelta(minutes=1)
                    )
                ),
            ),
            col(RuntimeSkillState.desired_version_id).is_not(None),
        )
        .order_by(col(RuntimeSkillState.updated_at))
        .with_for_update(skip_locked=True)
    ).first()
    if state is None:
        response.status_code = 204
        return None
    try:
        attempt, skill, version = create_sync_attempt(session, state, trigger="poll")
    except ValueError as exc:
        if state.status != "blocked":
            state.status = "failed"
            state.last_error = {"code": "skill_bundle_invalid", "message": str(exc)}
        session.add(state)
        session.commit()
        response.status_code = 204
        return None
    payload = sync_payload(attempt, skill, version)
    payload["runtime_profile_id"] = str(state.runtime_profile_id)
    payload["download_path"] = (
        f"/api/v1/internal/runtime/skill-sync/{attempt.id}/download"
    )
    session.commit()
    return payload


def _attempt_bundle(
    session: SessionDep, attempt_id: uuid.UUID
) -> tuple[RuntimeSkillSyncAttempt, RuntimeSkillState, SkillVersion]:
    attempt = session.get(RuntimeSkillSyncAttempt, attempt_id)
    state = (
        session.get(RuntimeSkillState, attempt.runtime_skill_state_id)
        if attempt
        else None
    )
    version = session.get(SkillVersion, attempt.version_id) if attempt else None
    if attempt is None or state is None or version is None:
        raise HTTPException(404, "Skill sync attempt not found")
    return attempt, state, version


def _stream_version(version: SkillVersion) -> StreamingResponse:
    source = skill_storage().open(version.storage_key)

    def stream() -> Iterator[bytes]:
        try:
            while chunk := source.read(1024 * 1024):
                yield chunk
        finally:
            source.close()

    return StreamingResponse(stream(), media_type="application/zip")


@internal_router.get("/{attempt_id}/download", response_model=None)
def download_platform_skill_bundle(
    attempt_id: uuid.UUID, session: SessionDep
) -> StreamingResponse:
    attempt, state, version = _attempt_bundle(session, attempt_id)
    if attempt.status != "syncing" or attempt.generation != state.generation:
        raise HTTPException(409, "Skill sync attempt is stale")
    return _stream_version(version)


@node_router.get("/{attempt_id}/download", response_model=None)
def download_node_skill_bundle(
    attempt_id: uuid.UUID, token: str, session: SessionDep
) -> StreamingResponse:
    try:
        claims = verify_skill_download_token(token, attempt_id)
    except SkillDownloadTokenError as exc:
        raise HTTPException(403, str(exc)) from exc
    attempt, state, version = _attempt_bundle(session, attempt_id)
    node = session.exec(
        select(RuntimeNode).where(
            RuntimeNode.runtime_profile_id == state.runtime_profile_id,
            col(RuntimeNode.revoked_at).is_(None),
        )
    ).first()
    if (
        node is None
        or claims.get("node_id") != str(node.id)
        or claims.get("runtime_profile_id") != str(state.runtime_profile_id)
        or claims.get("skill_id") != str(state.skill_id)
        or claims.get("version_id") != str(version.id)
        or claims.get("content_sha256") != version.content_sha256
        or claims.get("storage_key") != version.storage_key
        or attempt.status != "syncing"
        or attempt.generation != state.generation
    ):
        raise HTTPException(409, "Skill sync attempt is unavailable")
    return _stream_version(version)


@internal_router.post("/{attempt_id}/result")
def report_platform_skill_sync(
    attempt_id: uuid.UUID, body: SkillSyncResult, session: SessionDep
) -> dict[str, Any]:
    return apply_skill_sync_result(attempt_id, body, session)


def apply_skill_sync_result(
    attempt_id: uuid.UUID, body: SkillSyncResult, session: SessionDep
) -> dict[str, Any]:
    attempt, state, version = _attempt_bundle(session, attempt_id)
    if attempt.status not in {"syncing", "verified"}:
        raise HTTPException(409, "Skill sync attempt is not active")
    now = datetime.now(timezone.utc)
    attempt.bytes_downloaded = body.bytes_downloaded
    if attempt.generation != state.generation:
        attempt.status = "superseded"
        attempt.completed_at = now
        session.add(attempt)
        session.commit()
        return _state_public(session, state)
    valid_result = (
        body.content_sha256 == attempt.content_sha256
        and state.desired_version_id == version.id
    )
    if body.status == "verified" and attempt.status == "syncing" and valid_result:
        attempt.status = "verified"
        state.status = "committing"
        state.last_error = None
        state.next_retry_at = None
    elif body.status == "committed" and attempt.status == "verified" and valid_result:
        attempt.status = "applied"
        attempt.completed_at = now
        state.applied_version_id = version.id
        state.applied_digest = version.content_sha256
        state.applied_generation = attempt.generation
        state.status = "applied"
        state.retry_count = 0
        state.next_retry_at = None
        state.last_error = None
        state.applied_at = now
    else:
        attempt.status = "failed"
        attempt.completed_at = now
        attempt.error = body.error or {"code": "skill_sync_failed"}
        state.status = "failed"
        state.retry_count += 1
        state.next_retry_at = now + timedelta(
            seconds=min(30 * (2 ** max(0, state.retry_count - 1)), 900)
        )
        state.last_error = attempt.error
    state.last_reconciled_at = now
    state.updated_at = now
    session.add(attempt)
    session.add(state)
    session.commit()
    return _state_public(session, state)


def record_task_skill_usage(
    session: SessionDep,
    task: AgentTask,
    runtime_profile_id: uuid.UUID,
    evidence: list[SkillUsageItem],
) -> list[AgentTaskSkillUsage]:
    if task.runtime_profile_id != runtime_profile_id:
        raise HTTPException(409, "Task runtime does not match Skill evidence")
    if task.status not in {TaskStatus.DISPATCHED, TaskStatus.RUNNING}:
        raise HTTPException(409, "Task is not starting")
    release = (
        session.get(AgentRelease, task.agent_release_id)
        if task.agent_release_id
        else None
    )
    if release is None:
        raise HTTPException(409, "Task has no Agent Release")
    expected_ids = {
        uuid.UUID(str(item["id"])) for item in release.resolved_spec.get("skills", [])
    }
    actual_ids = {item.skill_id for item in evidence}
    if actual_ids != expected_ids or len(evidence) != len(actual_ids):
        raise HTTPException(409, "Task Skill evidence does not match Agent Release")
    rows: list[AgentTaskSkillUsage] = []
    for item in evidence:
        version = session.get(SkillVersion, item.version_id)
        state = session.exec(
            select(RuntimeSkillState).where(
                RuntimeSkillState.runtime_profile_id == runtime_profile_id,
                RuntimeSkillState.skill_id == item.skill_id,
            )
        ).first()
        if (
            version is None
            or version.skill_id != item.skill_id
            or version.version != item.version
            or version.content_sha256 != item.content_sha256
            or state is None
            or state.applied_version_id != version.id
            or state.applied_digest != item.content_sha256
            or state.applied_generation is None
            or state.applied_generation != item.runtime_generation
        ):
            raise HTTPException(409, "Runtime Skill evidence is stale or invalid")
        existing = session.exec(
            select(AgentTaskSkillUsage).where(
                AgentTaskSkillUsage.task_id == task.id,
                AgentTaskSkillUsage.skill_id == item.skill_id,
            )
        ).first()
        if existing is not None:
            if (
                existing.version_id != item.version_id
                or existing.content_sha256 != item.content_sha256
            ):
                raise HTTPException(409, "Task Skill evidence is immutable")
            rows.append(existing)
            continue
        row = AgentTaskSkillUsage(
            task_id=task.id,
            skill_id=item.skill_id,
            version_id=item.version_id,
            version=item.version,
            content_sha256=item.content_sha256,
            runtime_generation=item.runtime_generation,
        )
        session.add(row)
        rows.append(row)
    return rows


@internal_router.post("/tasks/{task_id}/usage")
def report_task_skill_usage(
    task_id: uuid.UUID, body: TaskSkillUsageReport, session: SessionDep
) -> dict[str, Any]:
    task = session.exec(
        select(AgentTask).where(AgentTask.id == task_id).with_for_update()
    ).first()
    if task is None:
        raise HTTPException(404, "Task not found")
    rows = record_task_skill_usage(session, task, body.runtime_profile_id, body.skills)
    session.commit()
    return {"task_id": task.id, "count": len(rows)}


@internal_router.post("/tasks/{task_id}/preparation-failed")
def report_task_preparation_failure(
    task_id: uuid.UUID, body: TaskPreparationFailure, session: SessionDep
) -> dict[str, Any]:
    task = session.exec(
        select(AgentTask).where(AgentTask.id == task_id).with_for_update()
    ).first()
    if (
        task is None
        or task.runtime_profile_id != body.runtime_profile_id
        or task.revision != body.revision
    ):
        raise HTTPException(409, "Task preparation failure scope mismatch")
    if task.status != TaskStatus.DISPATCHED:
        raise HTTPException(409, "Task is not awaiting preparation")
    require_task_transition(task.status, TaskStatus.FAILED)
    task.status = TaskStatus.FAILED
    task.final_result = {"code": body.code, "message": body.message}
    task.completed_at = datetime.now(timezone.utc)
    task.lease_expires_at = None
    sequence = session.exec(
        select(AgentEvent.sequence)
        .where(AgentEvent.task_id == task.id)
        .order_by(col(AgentEvent.sequence).desc())
    ).first()
    session.add(task)
    session.add(
        AgentEvent(
            namespace_id=task.namespace_id,
            task_id=task.id,
            sequence=(sequence if sequence is not None else -1) + 1,
            event_type=AgentEventType.ERROR,
            payload=task.final_result,
        )
    )
    session.commit()
    return {"task_id": task.id, "status": task.status.value}
