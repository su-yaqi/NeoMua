"""Agent Release, activation, runtime binding, and Tool approval APIs."""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select

from app import crud
from app.agent_management.capabilities import (
    ClaudeCodeHarnessAdapter,
    ResolutionError,
    canonical_bytes,
    canonical_digest,
    resolve_agent_spec,
    runtime_capability_fingerprint,
    validate_semver,
)
from app.agent_management.capability_models import (
    ActivationStatus,
    AgentActivation,
    AgentDeployment,
    AgentDeploymentStatus,
    AgentRelease,
    AgentReleaseComponent,
    ApprovalStatus,
    McpPlatformSecret,
    McpTargetBinding,
    McpTargetStatus,
    RuntimeAgentRelease,
    ToolApprovalRequest,
)
from app.agent_management.catalog import Diagnostic
from app.agent_management.models import AgentDefinition, AgentDraft
from app.api.deps import (
    CurrentUser,
    SessionDep,
    require_namespace_admin,
    require_namespace_runtime_user,
)
from app.models import NamespaceRole
from app.runtime.artifacts.signing import configured_artifact_signer, verify_signature
from app.runtime.models import (
    AgentEvent,
    AgentEventType,
    AgentTask,
    RuntimeNode,
    RuntimeProfile,
    RuntimeType,
)
from app.runtime.policy import TaskStatus, require_task_transition
from app.runtime.security import redact_event_payload, require_internal_runtime
from app.runtime.skill_sync import (
    ensure_release_skill_subscriptions,
    reconcile_runtime_skill_subscriptions,
    release_skill_blockers,
    release_skills_ready,
)

router = APIRouter(tags=["agent-releases"])
internal_router = APIRouter(
    prefix="/internal/runtime",
    tags=["internal-agent-releases"],
    dependencies=[Depends(require_internal_runtime)],
)


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReleaseCreate(StrictBody):
    draft_revision: int
    version: str


class ActivationCreate(StrictBody):
    runtime_profile_ids: list[uuid.UUID] = Field(min_length=1)
    valid_for_seconds: int = Field(default=3600, ge=60, le=86400)


class ApprovalDecision(StrictBody):
    args_digest: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    reason: str | None = Field(default=None, max_length=1024)


class ApprovalRequestCreate(StrictBody):
    task_id: uuid.UUID
    task_revision: int
    tool_call_id: str = Field(min_length=1, max_length=255)
    tool_qualified_name: str = Field(min_length=1, max_length=512)
    redacted_args: dict[str, Any]
    args_digest: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    expires_in_seconds: int = Field(default=300, ge=30, le=3600)


class DeploymentResult(StrictBody):
    status: str
    resolved_spec_digest: str = Field(min_length=64, max_length=64)
    materialization_digest: str = Field(min_length=64, max_length=64)
    capability_fingerprint: str
    error: dict[str, Any] | None = None


def _get_agent(
    session: SessionDep, agent_id: uuid.UUID, namespace_id: uuid.UUID
) -> AgentDefinition:
    agent = session.get(AgentDefinition, agent_id)
    if agent is None or agent.namespace_id != namespace_id:
        raise HTTPException(404, "Agent not found")
    return agent


def _get_release(
    session: SessionDep, release_id: uuid.UUID, namespace_id: uuid.UUID
) -> AgentRelease:
    release = session.get(AgentRelease, release_id)
    if release is None or release.namespace_id != namespace_id:
        raise HTTPException(404, "Agent Release not found")
    return release


def _release_public(release: AgentRelease) -> dict[str, Any]:
    return {
        "id": release.id,
        "namespace_id": release.namespace_id,
        "agent_id": release.agent_id,
        "version": release.version,
        "draft_revision": release.draft_revision,
        "resolved_spec_schema_version": release.resolved_spec_schema_version,
        "resolved_spec_digest": release.resolved_spec_digest,
        "manifest": release.manifest,
        "dependency_lock": release.dependency_lock,
        "manifest_digest": release.manifest_digest,
        "signature": release.signature,
        "signing_public_key": release.signing_public_key,
        "created_at": release.created_at,
    }


@router.get("/agents/{agent_id}/releases")
def list_agent_releases(
    agent_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> dict[str, Any]:
    agent = _get_agent(session, agent_id, namespace_id)
    rows = session.exec(
        select(AgentRelease)
        .where(AgentRelease.agent_id == agent.id)
        .order_by(col(AgentRelease.created_at).desc())
    ).all()
    return {"data": [_release_public(row) for row in rows], "count": len(rows)}


@router.post("/agents/{agent_id}/releases", status_code=201)
def create_agent_release(
    agent_id: uuid.UUID,
    body: ReleaseCreate,
    session: SessionDep,
    current_user: CurrentUser,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    if not idempotency_key:
        raise HTTPException(400, "Idempotency-Key is required")
    if not validate_semver(body.version):
        raise HTTPException(422, "version must be canonical SemVer")
    agent = _get_agent(session, agent_id, namespace_id)
    draft = session.exec(
        select(AgentDraft).where(AgentDraft.agent_id == agent.id).with_for_update()
    ).first()
    if draft is None:
        raise HTTPException(404, "Agent draft not found")
    existing_key = session.exec(
        select(AgentRelease).where(
            AgentRelease.namespace_id == namespace_id,
            AgentRelease.idempotency_key == idempotency_key,
        )
    ).first()
    if existing_key:
        if (
            existing_key.agent_id != agent.id
            or existing_key.version != body.version
            or existing_key.draft_revision != body.draft_revision
        ):
            raise HTTPException(409, "Idempotency-Key was used for a different release")
        return _release_public(existing_key)
    if (
        draft.revision != body.draft_revision
        or draft.validated_revision != draft.revision
    ):
        raise HTTPException(409, "Agent draft revision is not currently validated")
    release_id = uuid.uuid4()
    try:
        spec, dependency_lock, components = resolve_agent_spec(
            session, agent, release_id=release_id
        )
    except ResolutionError as exc:
        raise HTTPException(
            422, {"errors": [diag.model_dump() for diag in exc.diagnostics]}
        ) from exc
    adapter = ClaudeCodeHarnessAdapter()
    diagnostics = adapter.validate_spec(spec)
    if diagnostics:
        raise HTTPException(
            422, {"errors": [diag.model_dump() for diag in diagnostics]}
        )
    resolved_spec = spec.canonical()
    resolved_digest = spec.digest()
    materialization = adapter.materialization_manifest(spec)
    manifest = {
        "schema_version": "1.0",
        "release_id": str(release_id),
        "namespace_id": str(namespace_id),
        "agent_id": str(agent.id),
        "agent_slug": agent.slug,
        "version": body.version,
        "draft_revision": body.draft_revision,
        "resolved_spec_schema_version": spec.schema_version,
        "resolved_spec_digest": resolved_digest,
        "dependency_lock_digest": canonical_digest(dependency_lock),
        "materialization": materialization,
    }
    manifest_digest = canonical_digest(manifest)
    signer = configured_artifact_signer()
    existing_version = session.exec(
        select(AgentRelease).where(
            AgentRelease.agent_id == agent.id, AgentRelease.version == body.version
        )
    ).first()
    if existing_version:
        if existing_version.manifest_digest != manifest_digest:
            raise HTTPException(
                409, "Agent Release version already exists with different content"
            )
        return _release_public(existing_version)
    release = AgentRelease(
        id=release_id,
        namespace_id=namespace_id,
        agent_id=agent.id,
        version=body.version,
        draft_revision=draft.revision,
        resolved_spec_schema_version=spec.schema_version,
        resolved_spec=resolved_spec,
        resolved_spec_digest=resolved_digest,
        manifest=manifest,
        dependency_lock=dependency_lock,
        manifest_digest=manifest_digest,
        signature=signer.sign(canonical_bytes(manifest)),
        signing_public_key=signer.public_key(),
        idempotency_key=idempotency_key,
        created_by=current_user.id,
    )
    session.add(release)
    for component in components:
        key = str(component.get("slug") or component.get("key"))
        version = component.get("version") or component.get("revision")
        session.add(
            AgentReleaseComponent(
                release_id=release.id,
                component_type=component["type"],
                component_key=key,
                component_version=str(version) if version is not None else None,
                source_chain=["agent", f"{component['type']}:{key}"],
            )
        )
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            409, "Agent Release version or idempotency key already exists"
        ) from exc
    return _release_public(release)


@router.get("/agent-releases/{release_id}")
def get_agent_release(
    release_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> dict[str, Any]:
    release = _get_release(session, release_id, namespace_id)
    components = session.exec(
        select(AgentReleaseComponent).where(
            AgentReleaseComponent.release_id == release.id
        )
    ).all()
    return {
        **_release_public(release),
        "signature_valid": verify_signature(
            release.signing_public_key,
            canonical_bytes(release.manifest),
            release.signature,
        ),
        "components": [
            {
                "type": row.component_type,
                "key": row.component_key,
                "version": row.component_version,
                "source_chain": row.source_chain,
            }
            for row in components
        ],
    }


@router.get("/agent-releases/{release_id}/resolved-spec")
def get_resolved_spec(
    release_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> dict[str, Any]:
    release = _get_release(session, release_id, namespace_id)
    return {
        "schema_version": release.resolved_spec_schema_version,
        "digest": release.resolved_spec_digest,
        "spec": release.resolved_spec,
        "dependency_lock": release.dependency_lock,
        "materialization": release.manifest.get("materialization"),
    }


def _deployment_public(row: AgentDeployment) -> dict[str, Any]:
    return {
        "id": row.id,
        "activation_id": row.activation_id,
        "runtime_profile_id": row.runtime_profile_id,
        "attempt": row.attempt,
        "status": row.status.value,
        "capability_fingerprint": row.capability_fingerprint,
        "applied_digest": row.applied_digest,
        "error": row.error,
        "expires_at": row.expires_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _activation_public(
    session: SessionDep, activation: AgentActivation
) -> dict[str, Any]:
    deployments = session.exec(
        select(AgentDeployment)
        .where(AgentDeployment.activation_id == activation.id)
        .order_by(col(AgentDeployment.runtime_profile_id), col(AgentDeployment.attempt))
    ).all()
    return {
        "id": activation.id,
        "namespace_id": activation.namespace_id,
        "release_id": activation.release_id,
        "status": activation.status.value,
        "rollback_of_activation_id": activation.rollback_of_activation_id,
        "created_at": activation.created_at,
        "deployments": [_deployment_public(row) for row in deployments],
    }


def _recompute_activation(session: SessionDep, activation: AgentActivation) -> None:
    rows = session.exec(
        select(AgentDeployment).where(AgentDeployment.activation_id == activation.id)
    ).all()
    latest: dict[uuid.UUID, AgentDeployment] = {}
    for row in rows:
        if (
            row.runtime_profile_id not in latest
            or row.attempt > latest[row.runtime_profile_id].attempt
        ):
            latest[row.runtime_profile_id] = row
    statuses = {row.status for row in latest.values()}
    if statuses and statuses <= {AgentDeploymentStatus.APPLIED}:
        activation.status = ActivationStatus.ACTIVE
    elif AgentDeploymentStatus.APPLIED in statuses and statuses & {
        AgentDeploymentStatus.FAILED,
        AgentDeploymentStatus.INCOMPATIBLE,
        AgentDeploymentStatus.EXPIRED,
    }:
        activation.status = ActivationStatus.PARTIAL
    elif statuses and statuses <= {
        AgentDeploymentStatus.FAILED,
        AgentDeploymentStatus.INCOMPATIBLE,
        AgentDeploymentStatus.EXPIRED,
    }:
        activation.status = ActivationStatus.FAILED
    else:
        activation.status = ActivationStatus.DEPLOYING
    session.add(activation)


def _create_deployment(
    session: SessionDep,
    release: AgentRelease,
    activation: AgentActivation,
    runtime: RuntimeProfile,
    *,
    attempt: int,
    expires_at: datetime,
    subscribe_skills: bool = True,
) -> AgentDeployment:
    spec = release.resolved_spec
    try:
        from app.agent_management.capabilities import ResolvedAgentSpec

        parsed = ResolvedAgentSpec.model_validate(spec)
    except ValueError as exc:
        return AgentDeployment(
            activation_id=activation.id,
            runtime_profile_id=runtime.id,
            attempt=attempt,
            status=AgentDeploymentStatus.INCOMPATIBLE,
            error={"code": "invalid_resolved_spec", "message": str(exc)},
            expires_at=expires_at,
        )
    capability_inventory = runtime.harness_capabilities
    if runtime.runtime_type == RuntimeType.NODE:
        node = session.exec(
            select(RuntimeNode).where(
                RuntimeNode.runtime_profile_id == runtime.id,
                col(RuntimeNode.revoked_at).is_(None),
            )
        ).first()
        capability_inventory = node.harness_capabilities if node else {}
    diagnostics = ClaudeCodeHarnessAdapter().validate_target(
        parsed, runtime, capability_inventory
    )
    if subscribe_skills:
        try:
            ensure_release_skill_subscriptions(session, release, runtime.id)
        except ValueError as exc:
            diagnostics.append(
                Diagnostic(
                    code="skill_current_version_invalid",
                    field="skills",
                    message=str(exc),
                )
            )
    for mcp in parsed.mcp_servers:
        target = session.exec(
            select(McpTargetBinding).where(
                McpTargetBinding.revision_id == uuid.UUID(str(mcp["revision_id"])),
                McpTargetBinding.runtime_profile_id == runtime.id,
            )
        ).first()
        if target is None or target.status != McpTargetStatus.VERIFIED:
            diagnostics.append(
                Diagnostic(
                    code="mcp_target_not_ready",
                    field="mcp",
                    message=f"MCP {mcp['slug']} is not verified for this exact runtime target",
                )
            )
            continue
        if target.tool_digest not in mcp.get("tool_digests", []):
            diagnostics.append(
                Diagnostic(
                    code="mcp_tool_digest_stale",
                    field="mcp",
                    message=f"MCP {mcp['slug']} Tool schema changed after Release creation",
                )
            )
        if target.capability_fingerprint != runtime_capability_fingerprint(
            runtime, capability_inventory
        ):
            diagnostics.append(
                Diagnostic(
                    code="mcp_capability_stale",
                    field="mcp",
                    message=f"MCP {mcp['slug']} was not validated against the current target capabilities",
                )
            )
        if runtime.runtime_type == RuntimeType.NODE and not target.secret_ref:
            diagnostics.append(
                Diagnostic(
                    code="mcp_node_secret_missing",
                    field="mcp",
                    message=f"MCP {mcp['slug']} has no node-local secret reference",
                )
            )
        if (
            runtime.runtime_type == RuntimeType.PLATFORM
            and session.exec(
                select(McpPlatformSecret).where(
                    McpPlatformSecret.target_binding_id == target.id
                )
            ).first()
            is None
        ):
            diagnostics.append(
                Diagnostic(
                    code="mcp_platform_secret_missing",
                    field="mcp",
                    message=f"MCP {mcp['slug']} has no platform secret",
                )
            )
        if mcp["transport"] == "stdio":
            inventory = capability_inventory.get("mcp_executables") or []
            if mcp["config"].get("executable_key") not in inventory:
                diagnostics.append(
                    Diagnostic(
                        code="mcp_executable_unavailable",
                        field="mcp",
                        message=f"MCP {mcp['slug']} executable is absent from target inventory",
                    )
                )
    status = (
        AgentDeploymentStatus.INCOMPATIBLE
        if diagnostics
        else AgentDeploymentStatus.PENDING
    )
    return AgentDeployment(
        activation_id=activation.id,
        runtime_profile_id=runtime.id,
        attempt=attempt,
        status=status,
        capability_fingerprint=runtime_capability_fingerprint(
            runtime, capability_inventory
        ),
        error={"errors": [diag.model_dump() for diag in diagnostics]}
        if diagnostics
        else None,
        expires_at=expires_at,
    )


def _activation_targets(
    session: SessionDep,
    body: ActivationCreate,
    namespace_id: uuid.UUID,
) -> list[RuntimeProfile]:
    if len(set(body.runtime_profile_ids)) != len(body.runtime_profile_ids):
        raise HTTPException(422, "Duplicate runtime target")
    runtimes: list[RuntimeProfile] = []
    for runtime_id in body.runtime_profile_ids:
        runtime = session.get(RuntimeProfile, runtime_id)
        if runtime is None or runtime.namespace_id != namespace_id:
            raise HTTPException(404, "Runtime target not found")
        runtimes.append(runtime)
    return runtimes


@router.post("/agent-releases/{release_id}/activations/precheck")
def precheck_activation(
    release_id: uuid.UUID,
    body: ActivationCreate,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    release = _get_release(session, release_id, namespace_id)
    runtimes = _activation_targets(session, body, namespace_id)
    preview = AgentActivation(
        namespace_id=namespace_id,
        release_id=release.id,
        idempotency_key=f"precheck:{uuid.uuid4()}",
    )
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=body.valid_for_seconds)
    deployments = [
        _create_deployment(
            session,
            release,
            preview,
            runtime,
            attempt=1,
            expires_at=expires_at,
            subscribe_skills=False,
        )
        for runtime in runtimes
    ]
    return {
        "release_id": release.id,
        "compatible": all(
            item.status == AgentDeploymentStatus.PENDING for item in deployments
        ),
        "targets": [_deployment_public(item) for item in deployments],
    }


@router.post("/agent-releases/{release_id}/activations", status_code=202)
def activate_release(
    release_id: uuid.UUID,
    body: ActivationCreate,
    session: SessionDep,
    current_user: CurrentUser,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    if not idempotency_key:
        raise HTTPException(400, "Idempotency-Key is required")
    release = _get_release(session, release_id, namespace_id)
    existing = session.exec(
        select(AgentActivation).where(
            AgentActivation.namespace_id == namespace_id,
            AgentActivation.idempotency_key == idempotency_key,
        )
    ).first()
    if existing:
        if existing.release_id != release.id:
            raise HTTPException(
                409, "Idempotency-Key was used for a different activation"
            )
        return _activation_public(session, existing)
    runtimes = _activation_targets(session, body, namespace_id)
    activation = AgentActivation(
        namespace_id=namespace_id,
        release_id=release.id,
        idempotency_key=idempotency_key,
        created_by=current_user.id,
    )
    session.add(activation)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=body.valid_for_seconds)
    for runtime in runtimes:
        session.add(
            _create_deployment(
                session, release, activation, runtime, attempt=1, expires_at=expires_at
            )
        )
    session.flush()
    _recompute_activation(session, activation)
    session.commit()
    return _activation_public(session, activation)


@router.get("/agent-activations/{activation_id}")
def get_activation(
    activation_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> dict[str, Any]:
    row = session.get(AgentActivation, activation_id)
    if row is None or row.namespace_id != namespace_id:
        raise HTTPException(404, "Agent activation not found")
    return _activation_public(session, row)


def _get_deployment(
    session: SessionDep, deployment_id: uuid.UUID, namespace_id: uuid.UUID
) -> tuple[AgentDeployment, AgentActivation, AgentRelease, RuntimeProfile]:
    deployment = session.get(AgentDeployment, deployment_id)
    activation = (
        session.get(AgentActivation, deployment.activation_id) if deployment else None
    )
    release = session.get(AgentRelease, activation.release_id) if activation else None
    runtime = (
        session.get(RuntimeProfile, deployment.runtime_profile_id)
        if deployment
        else None
    )
    if (
        deployment is None
        or activation is None
        or release is None
        or runtime is None
        or activation.namespace_id != namespace_id
    ):
        raise HTTPException(404, "Agent deployment not found")
    return deployment, activation, release, runtime


@router.post("/agent-deployments/{deployment_id}/retry", status_code=202)
def retry_deployment(
    deployment_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    deployment, activation, release, runtime = _get_deployment(
        session, deployment_id, namespace_id
    )
    if deployment.status not in {
        AgentDeploymentStatus.FAILED,
        AgentDeploymentStatus.EXPIRED,
        AgentDeploymentStatus.INCOMPATIBLE,
    }:
        raise HTTPException(
            409, "Only failed, expired, or incompatible deployments can be retried"
        )
    attempts = session.exec(
        select(AgentDeployment.attempt).where(
            AgentDeployment.activation_id == activation.id,
            AgentDeployment.runtime_profile_id == runtime.id,
        )
    ).all()
    retried = _create_deployment(
        session,
        release,
        activation,
        runtime,
        attempt=max(attempts) + 1,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    session.add(retried)
    _recompute_activation(session, activation)
    session.commit()
    return _deployment_public(retried)


@router.post("/agent-deployments/{deployment_id}/rollback", status_code=202)
def rollback_deployment(
    deployment_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> dict[str, Any]:
    if not idempotency_key:
        raise HTTPException(400, "Idempotency-Key is required")
    _, activation, release, runtime = _get_deployment(
        session, deployment_id, namespace_id
    )
    binding = session.exec(
        select(RuntimeAgentRelease)
        .where(
            RuntimeAgentRelease.runtime_profile_id == runtime.id,
            RuntimeAgentRelease.agent_id == release.agent_id,
        )
        .with_for_update()
    ).first()
    if binding is None or binding.previous_release_id is None:
        raise HTTPException(
            409, "No retained previous Release is available for rollback"
        )
    previous = session.get(AgentRelease, binding.previous_release_id)
    if previous is None or not verify_signature(
        previous.signing_public_key,
        canonical_bytes(previous.manifest),
        previous.signature,
    ):
        raise HTTPException(409, "Previous Release cannot be verified")
    rollback = AgentActivation(
        namespace_id=namespace_id,
        release_id=previous.id,
        idempotency_key=idempotency_key,
        rollback_of_activation_id=activation.id,
        created_by=current_user.id,
    )
    session.add(rollback)
    session.add(
        _create_deployment(
            session,
            previous,
            rollback,
            runtime,
            attempt=1,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    session.flush()
    _recompute_activation(session, rollback)
    session.commit()
    return _activation_public(session, rollback)


@router.get("/runtime-agents")
def runtime_agents(
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> dict[str, Any]:
    rows = session.exec(
        select(RuntimeAgentRelease)
        .where(RuntimeAgentRelease.namespace_id == namespace_id)
        .order_by(
            col(RuntimeAgentRelease.runtime_profile_id),
            col(RuntimeAgentRelease.agent_id),
        )
    ).all()
    data: list[dict[str, Any]] = []
    for row in rows:
        agent = session.get(AgentDefinition, row.agent_id)
        release = session.get(AgentRelease, row.current_release_id)
        runtime = session.get(RuntimeProfile, row.runtime_profile_id)
        node = (
            session.exec(
                select(RuntimeNode).where(
                    RuntimeNode.runtime_profile_id == row.runtime_profile_id
                )
            ).first()
            if runtime and runtime.runtime_type == RuntimeType.NODE
            else None
        )
        data.append(
            {
                "id": row.id,
                "runtime_profile_id": row.runtime_profile_id,
                "runtime_type": runtime.runtime_type.value if runtime else None,
                "runtime_name": node.name if node else "platform",
                "agent_id": row.agent_id,
                "agent_slug": agent.slug if agent else None,
                "agent_name": agent.name if agent else None,
                "current_release_id": row.current_release_id,
                "current_release_version": release.version if release else None,
                "previous_release_id": row.previous_release_id,
                "applied_digest": row.applied_digest,
                "materialization_digest": row.materialization_digest,
                "updated_at": row.updated_at,
            }
        )
    return {"data": data, "count": len(data)}


@router.get("/runtime-agents/{binding_id}")
def runtime_agent(
    binding_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> dict[str, Any]:
    row = session.get(RuntimeAgentRelease, binding_id)
    if row is None or row.namespace_id != namespace_id:
        raise HTTPException(404, "Runtime Agent binding not found")
    release = session.get(AgentRelease, row.current_release_id)
    return {
        "id": row.id,
        "runtime_profile_id": row.runtime_profile_id,
        "agent_id": row.agent_id,
        "current_release_id": row.current_release_id,
        "previous_release_id": row.previous_release_id,
        "applied_digest": row.applied_digest,
        "materialization_digest": row.materialization_digest,
        "release_version": release.version if release else None,
        "updated_at": row.updated_at,
    }


@internal_router.post("/agent-deployments/claim")
def claim_agent_deployment(
    session: SessionDep, response: Response
) -> dict[str, Any] | None:
    now = datetime.now(timezone.utc)
    rows = session.exec(
        select(AgentDeployment)
        .where(AgentDeployment.status == AgentDeploymentStatus.PENDING)
        .order_by(col(AgentDeployment.created_at))
        .with_for_update(skip_locked=True)
    ).all()
    for row in rows:
        if row.expires_at and row.expires_at <= now:
            row.status = AgentDeploymentStatus.EXPIRED
            row.error = {"code": "deployment_expired_before_dispatch"}
            session.add(row)
            continue
        runtime = session.get(RuntimeProfile, row.runtime_profile_id)
        if runtime and runtime.runtime_type == RuntimeType.PLATFORM:
            activation = session.get(AgentActivation, row.activation_id)
            release = (
                session.get(AgentRelease, activation.release_id) if activation else None
            )
            if release is None:
                row.status = AgentDeploymentStatus.FAILED
                row.error = {"code": "release_missing"}
                session.add(row)
                continue
            blockers = release_skill_blockers(session, release, runtime.id)
            if blockers:
                failed = any(item.get("status") == "failed" for item in blockers)
                row.status = (
                    AgentDeploymentStatus.FAILED
                    if failed
                    else AgentDeploymentStatus.INCOMPATIBLE
                )
                row.error = {
                    "code": "skill_sync_failed" if failed else "skill_sync_blocked",
                    "skills": blockers,
                }
                session.add(row)
                if activation is not None:
                    _recompute_activation(session, activation)
                continue
            if not release_skills_ready(session, release, runtime.id):
                continue
            row.status = AgentDeploymentStatus.DISPATCHED
            row.updated_at = now
            session.add(row)
            session.commit()
            capability_inventory = {
                "runtime_type": runtime.runtime_type.value,
                "harness_capabilities": runtime.harness_capabilities,
                "config": {
                    "allowed_working_roots": runtime.config.get(
                        "allowed_working_roots", []
                    )
                },
            }
            return {
                "deployment_id": row.id,
                "runtime_profile_id": runtime.id,
                "capability_fingerprint": row.capability_fingerprint,
                "capability_inventory": capability_inventory,
                "release": _release_public(release),
                "resolved_spec": release.resolved_spec,
                "materialization": release.manifest["materialization"],
            }
    session.commit()
    response.status_code = 204
    return None


@internal_router.post("/agent-deployments/{deployment_id}/result")
def report_agent_deployment(
    deployment_id: uuid.UUID, body: DeploymentResult, session: SessionDep
) -> dict[str, Any]:
    deployment = session.exec(
        select(AgentDeployment)
        .where(AgentDeployment.id == deployment_id)
        .with_for_update()
    ).first()
    activation = (
        session.get(AgentActivation, deployment.activation_id) if deployment else None
    )
    release = session.get(AgentRelease, activation.release_id) if activation else None
    runtime = (
        session.get(RuntimeProfile, deployment.runtime_profile_id)
        if deployment
        else None
    )
    if deployment is None or activation is None or release is None or runtime is None:
        raise HTTPException(404, "Agent deployment not found")
    if deployment.status not in {
        AgentDeploymentStatus.DISPATCHED,
        AgentDeploymentStatus.APPLYING,
    }:
        raise HTTPException(409, "Agent deployment is not in progress")
    if body.status != "applied":
        deployment.status = AgentDeploymentStatus.FAILED
        deployment.error = body.error or {"code": "agent_release_apply_failed"}
    elif body.capability_fingerprint != deployment.capability_fingerprint:
        deployment.status = AgentDeploymentStatus.FAILED
        deployment.error = {"code": "stale_capability_fingerprint"}
    elif (
        body.resolved_spec_digest != release.resolved_spec_digest
        or body.materialization_digest
        != release.manifest["materialization"]["resolved_spec_digest"]
    ):
        deployment.status = AgentDeploymentStatus.FAILED
        deployment.error = {"code": "release_digest_mismatch"}
    else:
        deployment.status = AgentDeploymentStatus.APPLIED
        deployment.applied_digest = body.resolved_spec_digest
        binding = session.exec(
            select(RuntimeAgentRelease)
            .where(
                RuntimeAgentRelease.runtime_profile_id == runtime.id,
                RuntimeAgentRelease.agent_id == release.agent_id,
            )
            .with_for_update()
        ).first()
        if binding is None:
            binding = RuntimeAgentRelease(
                namespace_id=release.namespace_id,
                runtime_profile_id=runtime.id,
                agent_id=release.agent_id,
                current_release_id=release.id,
                applied_digest=release.resolved_spec_digest,
                materialization_digest=body.materialization_digest,
            )
        else:
            if binding.current_release_id != release.id:
                binding.previous_release_id = binding.current_release_id
            binding.current_release_id = release.id
            binding.applied_digest = release.resolved_spec_digest
            binding.materialization_digest = body.materialization_digest
            binding.updated_at = datetime.now(timezone.utc)
        session.add(binding)
        reconcile_runtime_skill_subscriptions(session, runtime.id)
    deployment.updated_at = datetime.now(timezone.utc)
    session.add(deployment)
    _recompute_activation(session, activation)
    session.commit()
    return _deployment_public(deployment)


@internal_router.post("/tool-approvals", status_code=201)
def request_tool_approval(
    body: ApprovalRequestCreate, session: SessionDep
) -> dict[str, Any]:
    task = session.exec(
        select(AgentTask).where(AgentTask.id == body.task_id).with_for_update()
    ).first()
    if task is None or task.revision != body.task_revision:
        raise HTTPException(409, "Task revision no longer matches")
    if task.status != TaskStatus.RUNNING:
        raise HTTPException(409, "Task is not running")
    release = (
        session.get(AgentRelease, task.agent_release_id)
        if task.agent_release_id
        else None
    )
    policies = (
        {item["key"]: item["policy"] for item in release.resolved_spec.get("tools", [])}
        if release
        else {}
    )
    policy = policies.get(body.tool_qualified_name)
    if policy in {"deny", "disabled", "forbidden"}:
        raise HTTPException(403, "Tool is forbidden")
    if policy != "require_approval":
        raise HTTPException(422, "Tool does not require approval")
    existing = session.exec(
        select(ToolApprovalRequest).where(
            ToolApprovalRequest.task_id == task.id,
            ToolApprovalRequest.task_revision == task.revision,
            ToolApprovalRequest.tool_call_id == body.tool_call_id,
            ToolApprovalRequest.args_digest == body.args_digest,
        )
    ).first()
    if existing:
        return _approval_public(existing)
    task.status = TaskStatus.AWAITING_APPROVAL
    approval = ToolApprovalRequest(
        namespace_id=task.namespace_id,
        task_id=task.id,
        task_revision=task.revision,
        tool_call_id=body.tool_call_id,
        tool_qualified_name=body.tool_qualified_name,
        redacted_args=redact_event_payload(body.redacted_args),
        args_digest=body.args_digest,
        expires_at=datetime.now(timezone.utc)
        + timedelta(seconds=body.expires_in_seconds),
    )
    session.add(task)
    session.add(approval)
    session.flush()
    sequence = session.exec(
        select(AgentEvent.sequence)
        .where(AgentEvent.task_id == task.id)
        .order_by(col(AgentEvent.sequence).desc())
    ).first()
    session.add(
        AgentEvent(
            namespace_id=task.namespace_id,
            task_id=task.id,
            sequence=(sequence if sequence is not None else -1) + 1,
            event_type=AgentEventType.APPROVAL_REQUESTED,
            payload={
                "approval_id": str(approval.id),
                "tool": approval.tool_qualified_name,
                "args_digest": approval.args_digest,
                "expires_at": approval.expires_at.isoformat(),
            },
        )
    )
    session.commit()
    return _approval_public(approval)


@internal_router.get("/tool-approvals/{approval_id}")
def internal_tool_approval_status(
    approval_id: uuid.UUID, session: SessionDep
) -> dict[str, Any]:
    approval = session.exec(
        select(ToolApprovalRequest)
        .where(ToolApprovalRequest.id == approval_id)
        .with_for_update()
    ).first()
    if approval is None:
        raise HTTPException(404, "Tool approval not found")
    if (
        approval.status == ApprovalStatus.PENDING
        and approval.expires_at <= datetime.now(timezone.utc)
    ):
        approval.status = ApprovalStatus.EXPIRED
        approval.resolved_at = datetime.now(timezone.utc)
        task = session.exec(
            select(AgentTask).where(AgentTask.id == approval.task_id).with_for_update()
        ).first()
        if task and task.status == TaskStatus.AWAITING_APPROVAL:
            task.status = TaskStatus.RUNNING
            task.updated_at = datetime.now(timezone.utc)
            session.add(task)
            sequence = session.exec(
                select(AgentEvent.sequence)
                .where(AgentEvent.task_id == task.id)
                .order_by(col(AgentEvent.sequence).desc())
            ).first()
            session.add(
                AgentEvent(
                    namespace_id=task.namespace_id,
                    task_id=task.id,
                    sequence=(sequence if sequence is not None else -1) + 1,
                    event_type=AgentEventType.APPROVAL_EXPIRED,
                    payload={
                        "approval_id": str(approval.id),
                        "args_digest": approval.args_digest,
                    },
                )
            )
        session.add(approval)
        session.commit()
    return _approval_public(approval)


def _approval_public(row: ToolApprovalRequest) -> dict[str, Any]:
    status = row.status
    if status == ApprovalStatus.PENDING and row.expires_at <= datetime.now(
        timezone.utc
    ):
        status = ApprovalStatus.EXPIRED
    return {
        "id": row.id,
        "task_id": row.task_id,
        "task_revision": row.task_revision,
        "tool_call_id": row.tool_call_id,
        "tool_qualified_name": row.tool_qualified_name,
        "redacted_args": row.redacted_args,
        "args_digest": row.args_digest,
        "status": status.value,
        "requested_at": row.requested_at,
        "expires_at": row.expires_at,
        "resolved_at": row.resolved_at,
        "resolved_by": row.resolved_by,
        "decision_reason": row.decision_reason,
    }


@router.get("/runtime-tasks/{task_id}/approvals")
def list_task_approvals(
    task_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> dict[str, Any]:
    task = session.get(AgentTask, task_id)
    if task is None or task.namespace_id != namespace_id:
        raise HTTPException(404, "Task not found")
    rows = session.exec(
        select(ToolApprovalRequest)
        .where(ToolApprovalRequest.task_id == task.id)
        .order_by(col(ToolApprovalRequest.requested_at).desc())
    ).all()
    changed = False
    for row in rows:
        if row.status == ApprovalStatus.PENDING and row.expires_at <= datetime.now(
            timezone.utc
        ):
            row.status = ApprovalStatus.EXPIRED
            row.resolved_at = datetime.now(timezone.utc)
            session.add(row)
            changed = True
    if changed:
        session.commit()
    return {"data": [_approval_public(row) for row in rows], "count": len(rows)}


def _may_decide(
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID,
    task: AgentTask,
) -> bool:
    if current_user.is_superuser:
        return True
    role = crud.get_namespace_role(
        session=session, user_id=current_user.id, namespace_id=namespace_id
    )
    if role == NamespaceRole.ADMIN:
        return True
    return bool(
        role == NamespaceRole.DEVELOPER
        and task.created_by == current_user.id
        and task.task_kind.value == "ordinary"
        and task.snapshot.get("initiator_may_approve") is True
    )


def _decide_approval(
    approval_id: uuid.UUID,
    body: ApprovalDecision,
    decision: ApprovalStatus,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID,
) -> dict[str, Any]:
    approval = session.exec(
        select(ToolApprovalRequest)
        .where(
            ToolApprovalRequest.id == approval_id,
            ToolApprovalRequest.namespace_id == namespace_id,
        )
        .with_for_update()
    ).first()
    if approval is None:
        raise HTTPException(404, "Tool approval not found")
    task = session.exec(
        select(AgentTask).where(AgentTask.id == approval.task_id).with_for_update()
    ).first()
    if task is None or not _may_decide(session, current_user, namespace_id, task):
        raise HTTPException(403, "Tool approval requires namespace admin")
    if (
        body.args_digest != approval.args_digest
        or task.revision != approval.task_revision
    ):
        raise HTTPException(409, "Approval no longer matches the exact Tool Call")
    if approval.status == decision:
        return _approval_public(approval)
    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(
            409, "Tool approval already has a different terminal decision"
        )
    if approval.expires_at <= datetime.now(timezone.utc):
        approval.status = ApprovalStatus.EXPIRED
        approval.resolved_at = datetime.now(timezone.utc)
        session.add(approval)
        session.commit()
        raise HTTPException(409, "Tool approval has expired")
    approval.status = decision
    approval.resolved_at = datetime.now(timezone.utc)
    approval.resolved_by = current_user.id
    approval.decision_reason = body.reason
    if task.status == TaskStatus.AWAITING_APPROVAL:
        require_task_transition(task.status, TaskStatus.RUNNING)
        task.status = TaskStatus.RUNNING
        task.updated_at = datetime.now(timezone.utc)
        session.add(task)
    sequence = session.exec(
        select(AgentEvent.sequence)
        .where(AgentEvent.task_id == task.id)
        .order_by(col(AgentEvent.sequence).desc())
    ).first()
    session.add(
        AgentEvent(
            namespace_id=task.namespace_id,
            task_id=task.id,
            sequence=(sequence if sequence is not None else -1) + 1,
            event_type=AgentEventType.APPROVAL_RESOLVED,
            payload={
                "approval_id": str(approval.id),
                "status": decision.value,
                "args_digest": approval.args_digest,
            },
        )
    )
    session.add(approval)
    session.commit()
    return _approval_public(approval)


@router.post("/tool-approvals/{approval_id}/approve")
def approve_tool(
    approval_id: uuid.UUID,
    body: ApprovalDecision,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> dict[str, Any]:
    return _decide_approval(
        approval_id, body, ApprovalStatus.APPROVED, session, current_user, namespace_id
    )


@router.post("/tool-approvals/{approval_id}/deny")
def deny_tool(
    approval_id: uuid.UUID,
    body: ApprovalDecision,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_runtime_user),
) -> dict[str, Any]:
    return _decide_approval(
        approval_id, body, ApprovalStatus.DENIED, session, current_user, namespace_id
    )
