import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError
from sqlalchemy import or_
from sqlmodel import col, select

from app.agent_management.capabilities import (
    canonical_digest,
    runtime_capability_fingerprint,
)
from app.agent_management.capability_models import (
    AgentActivation,
    AgentDeployment,
    AgentDeploymentStatus,
    AgentRelease,
    ApprovalStatus,
    McpServer,
    McpServerRevision,
    McpTargetBinding,
    McpTargetStatus,
    McpValidationAttempt,
    RuntimeAgentRelease,
    ToolApprovalRequest,
)
from app.agent_management.capability_routes import (
    McpValidationResult,
    _apply_validation_result,
)
from app.agent_management.release_routes import (
    ApprovalRequestCreate,
    _recompute_activation,
    _release_public,
    request_tool_approval,
)
from app.api.deps import SessionDep
from app.api.routes.runtime_instances import (
    ConfigurationResultInput,
    NativeModelValidationResult,
    RuntimeDiscoveryReport,
    report_native_model_validation_result,
    report_runtime_configuration_result,
    report_runtime_discovery,
)
from app.api.routes.runtime_skills import (
    SkillSyncResult,
    SkillUsageItem,
    apply_skill_sync_result,
    create_sync_attempt,
    record_task_skill_usage,
    sync_payload,
)
from app.conversation_management.models import (
    AgentDelegation,
    Conversation,
    DelegationStatus,
)
from app.conversation_management.service import (
    create_runtime_delegation,
    reconcile_agent_messages,
)
from app.core.config import settings
from app.llm_provider_service import open_secret_payload
from app.runtime.artifacts.manifest import DeploymentManifest
from app.runtime.artifacts.security import issue_artifact_download_token
from app.runtime.artifacts.service import ArtifactReleaseService
from app.runtime.artifacts.signing import configured_artifact_signer
from app.runtime.capabilities import validate_harness_capabilities
from app.runtime.catalog import RuntimeCatalogError, current_runtime_evidence
from app.runtime.connections import (
    NodeAuthenticationError,
    authenticate_node_connection,
)
from app.runtime.enrollment import (
    recover_node_credential_rotation,
    retire_replaced_credential,
    rotate_node_credential,
)
from app.runtime.jobs import reserve_node_runtime_jobs
from app.runtime.models import (
    AgentEventType,
    AgentTask,
    AgentTaskModelUsage,
    ArtifactDeployment,
    ArtifactRelease,
    DeploymentStatus,
    RuntimeArtifact,
    RuntimeConfigurationRevision,
    RuntimeConfigurationStatus,
    RuntimeInstance,
    RuntimeJob,
    RuntimeJobStatus,
    RuntimeModelBinding,
    RuntimeModelBindingStatus,
    RuntimeModelRouteType,
    RuntimeModelValidationAttempt,
    RuntimeNode,
    RuntimeNodeArtifact,
    RuntimeProfile,
    RuntimeRouteMode,
    RuntimeSecret,
    RuntimeSkillState,
    RuntimeSkillSyncAttempt,
)
from app.runtime.policy import TaskStatus, require_task_transition
from app.runtime.repository import (
    EventSequenceConflict,
    append_and_apply_event,
    last_contiguous_event_sequence,
    release_task_reservation,
    reserve_node_tasks,
)
from app.runtime.security import issue_gateway_token
from app.runtime.skill_sync import (
    issue_skill_download_token,
    reconcile_runtime_skill_subscriptions,
    release_skill_blockers,
    release_skills_committing,
    release_skills_ready,
)

router = APIRouter(prefix="/node", tags=["node-socket"])


class Envelope(BaseModel):
    type: str
    protocol_version: Literal["2"]
    message_id: uuid.UUID
    correlation_id: uuid.UUID | None = None
    node_id: uuid.UUID
    sent_at: datetime
    payload: dict[str, Any]


def _envelope(
    message_type: str,
    node_id: uuid.UUID,
    payload: dict[str, Any],
    correlation_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    return Envelope(
        type=message_type,
        protocol_version="2",
        message_id=uuid.uuid4(),
        correlation_id=correlation_id,
        node_id=node_id,
        sent_at=datetime.now(timezone.utc),
        payload=payload,
    ).model_dump(mode="json")


async def _send_pending_control(
    websocket: WebSocket,
    session: SessionDep,
    node: RuntimeNode,
    connection_id: uuid.UUID,
) -> None:
    if not await _connection_is_current(websocket, session, node.id, connection_id):
        return
    queued = reserve_node_tasks(
        session, node_id=node.id, connection_id=connection_id, limit=10
    )
    for task in queued:
        if not await _connection_is_current(websocket, session, node.id, connection_id):
            return
        runtime_instance = (
            session.get(RuntimeInstance, task.runtime_instance_id)
            if task.runtime_instance_id
            else None
        )
        if runtime_instance is not None:
            usage = session.exec(
                select(AgentTaskModelUsage).where(
                    AgentTaskModelUsage.task_id == task.id
                )
            ).first()
            model_binding = (
                session.get(RuntimeModelBinding, usage.runtime_model_binding_id)
                if usage
                else None
            )
            if (
                runtime_instance.runtime_node_id != node.id
                or usage is None
                or model_binding is None
                or model_binding.status != RuntimeModelBindingStatus.AVAILABLE
                or model_binding.runtime_instance_id != runtime_instance.id
            ):
                task.status = TaskStatus.REJECTED
                task.final_result = {"code": "frozen_execution_binding_changed"}
                task.dispatch_connection_id = None
                task.dispatch_reserved_until = None
                session.add(task)
                session.commit()
                continue
            release = (
                session.get(AgentRelease, task.agent_release_id)
                if task.agent_release_id
                else None
            )
            if release is not None:
                blockers = release_skill_blockers(
                    session,
                    release,
                    runtime_instance_id=runtime_instance.id,
                )
                if blockers:
                    task.status = TaskStatus.REJECTED
                    task.final_result = {
                        "code": "skill_sync_blocked",
                        "skills": blockers,
                    }
                    session.add(task)
                    session.commit()
                    continue
                if not release_skills_ready(
                    session,
                    release,
                    runtime_instance_id=runtime_instance.id,
                ):
                    release_task_reservation(session, task.id, connection_id)
                    continue
            snapshot = dict(task.snapshot)
            mcp_servers: list[dict[str, Any]] = []
            for server in snapshot.get("mcp_servers", []):
                target = session.exec(
                    select(McpTargetBinding).where(
                        McpTargetBinding.revision_id
                        == uuid.UUID(str(server["revision_id"])),
                        McpTargetBinding.runtime_instance_id == runtime_instance.id,
                        McpTargetBinding.status == McpTargetStatus.VERIFIED,
                    )
                ).first()
                if (
                    target is None
                    or not target.secret_ref
                    or target.tool_digest not in server.get("tool_digests", [])
                ):
                    task.status = TaskStatus.REJECTED
                    task.final_result = {
                        "code": "mcp_target_not_ready",
                        "server": server.get("slug"),
                    }
                    session.add(task)
                    session.commit()
                    break
                mcp_servers.append(
                    {
                        **server,
                        "secret_handles": [
                            {
                                "runtime_instance_id": str(runtime_instance.id),
                                "secret_ref": target.secret_ref,
                            }
                        ],
                    }
                )
            else:
                snapshot["mcp_servers"] = mcp_servers
                try:
                    await websocket.send_json(
                        _envelope(
                            "task_dispatch",
                            node.id,
                            {
                                "task_id": str(task.id),
                                "revision": task.revision,
                                "prompt": task.prompt,
                                "snapshot": snapshot,
                                "route": {"mode": "pending"},
                                "requires_model_preparation": True,
                                "event_sequence_start": 1,
                            },
                        )
                    )
                except Exception:
                    release_task_reservation(session, task.id, connection_id)
                    raise
                continue
            continue
        runtime = session.get(RuntimeProfile, task.runtime_profile_id)
        if runtime is None:
            task.status = TaskStatus.REJECTED
            task.final_result = {"code": "runtime_unavailable"}
            task.dispatch_connection_id = None
            task.dispatch_reserved_until = None
            session.add(task)
            session.commit()
            continue
        release = (
            session.get(AgentRelease, task.agent_release_id)
            if task.agent_release_id
            else None
        )
        if release is not None and release_skills_committing(
            session, release, runtime.id
        ):
            release_task_reservation(session, task.id, connection_id)
            continue
        route: dict[str, Any] = {"mode": runtime.route_mode.value}
        if runtime.route_mode == RuntimeRouteMode.PLATFORM_GATEWAY:
            if not settings.MODEL_GATEWAY_PUBLIC_URL:
                task.status = TaskStatus.REJECTED
                task.final_result = {"code": "gateway_public_url_not_configured"}
                task.dispatch_connection_id = None
                task.dispatch_reserved_until = None
                session.add(task)
                session.commit()
                continue
            token = issue_gateway_token(
                task.namespace_id, runtime.id, task.id, task.snapshot["model_id"]
            )
            route.update(
                {
                    "base_url": (
                        f"{settings.MODEL_GATEWAY_PUBLIC_URL.rstrip('/')}/tasks/{task.id}"
                    ),
                    "api_key": token,
                    "runtime_id": str(runtime.id),
                }
            )
        try:
            await websocket.send_json(
                _envelope(
                    "task_dispatch",
                    node.id,
                    {
                        "task_id": str(task.id),
                        "revision": task.revision,
                        "prompt": task.prompt,
                        "snapshot": task.snapshot,
                        "route": route,
                        "event_sequence_start": 1,
                    },
                )
            )
        except Exception:
            release_task_reservation(session, task.id, connection_id)
            raise
    cancelling = session.exec(
        select(AgentTask).where(
            AgentTask.target_node_id == node.id,
            AgentTask.status == TaskStatus.CANCELLING,
        )
    ).all()
    for task in cancelling:
        if not await _connection_is_current(websocket, session, node.id, connection_id):
            return
        await websocket.send_json(
            _envelope(
                "task_cancel",
                node.id,
                {"task_id": str(task.id), "revision": task.revision},
            )
        )


async def _send_pending_runtime_jobs(
    websocket: WebSocket,
    session: SessionDep,
    node: RuntimeNode,
    connection_id: uuid.UUID,
) -> None:
    if not await _connection_is_current(websocket, session, node.id, connection_id):
        return
    jobs = reserve_node_runtime_jobs(
        session, node_id=node.id, connection_id=connection_id, limit=10
    )
    for job in jobs:
        if not await _connection_is_current(websocket, session, node.id, connection_id):
            return
        try:
            await websocket.send_json(
                _envelope(
                    "runtime_job_dispatch",
                    node.id,
                    {
                        "job_id": str(job.id),
                        "revision": job.revision,
                        "kind": job.kind.value,
                        "payload": job.payload,
                        "side_effecting": job.side_effecting,
                    },
                )
            )
        except Exception:
            job.dispatch_connection_id = None
            job.dispatch_reserved_until = None
            session.add(job)
            session.commit()
            raise


async def _send_pending_runtime_instance_configurations(
    websocket: WebSocket,
    session: SessionDep,
    node: RuntimeNode,
    connection_id: uuid.UUID,
) -> None:
    if not await _connection_is_current(websocket, session, node.id, connection_id):
        return
    configuration = session.exec(
        select(RuntimeConfigurationRevision)
        .join(
            RuntimeInstance,
            col(RuntimeConfigurationRevision.runtime_instance_id)
            == RuntimeInstance.id,
        )
        .where(
            RuntimeInstance.runtime_node_id == node.id,
            RuntimeInstance.enabled.is_(True),
            RuntimeInstance.desired_configuration_revision_id
            == RuntimeConfigurationRevision.id,
            RuntimeConfigurationRevision.status
            == RuntimeConfigurationStatus.DESIRED,
        )
        .order_by(col(RuntimeConfigurationRevision.created_at))
        .with_for_update(skip_locked=True)
    ).first()
    if configuration is None:
        return
    runtime = session.get(RuntimeInstance, configuration.runtime_instance_id)
    if runtime is None:
        return
    worker_id = f"node:{node.id}"
    configuration.status = RuntimeConfigurationStatus.APPLYING
    configuration.error = {"apply_worker_id": worker_id}
    session.add(configuration)
    session.commit()
    try:
        await websocket.send_json(
            _envelope(
                "runtime_instance_configuration",
                node.id,
                {
                    "runtime_instance_id": str(runtime.id),
                    "installation_key": runtime.installation_key,
                    "engine_type": runtime.engine_type.value,
                    "configuration_revision_id": str(configuration.id),
                    "revision": configuration.revision,
                    "configuration_digest": configuration.configuration_digest,
                    "executable": configuration.executable,
                    "arguments": configuration.arguments,
                    "working_directory_policy": configuration.working_directory_policy,
                    "environment_allowlist": configuration.environment_allowlist,
                    "security_policy": configuration.security_policy,
                    "resource_limits": configuration.resource_limits,
                },
            )
        )
    except Exception:
        configuration.status = RuntimeConfigurationStatus.DESIRED
        configuration.error = None
        session.add(configuration)
        session.commit()
        raise


async def _send_pending_native_model_validations(
    websocket: WebSocket,
    session: SessionDep,
    node: RuntimeNode,
    connection_id: uuid.UUID,
) -> None:
    if not await _connection_is_current(websocket, session, node.id, connection_id):
        return
    attempt = session.exec(
        select(RuntimeModelValidationAttempt)
        .join(
            RuntimeModelBinding,
            col(RuntimeModelValidationAttempt.runtime_model_binding_id)
            == RuntimeModelBinding.id,
        )
        .join(
            RuntimeInstance,
            col(RuntimeModelBinding.runtime_instance_id) == RuntimeInstance.id,
        )
        .where(
            RuntimeInstance.runtime_node_id == node.id,
            RuntimeModelBinding.route_type == RuntimeModelRouteType.RUNTIME_NATIVE,
            RuntimeModelValidationAttempt.status == "pending",
        )
        .order_by(col(RuntimeModelValidationAttempt.started_at))
        .with_for_update(skip_locked=True)
    ).first()
    if attempt is None:
        return
    binding = session.get(RuntimeModelBinding, attempt.runtime_model_binding_id)
    runtime = session.get(RuntimeInstance, binding.runtime_instance_id) if binding else None
    if binding is None or runtime is None:
        return
    attempt.status = "running"
    attempt.error = {"worker_id": f"node:{node.id}"}
    session.add(attempt)
    session.commit()
    await websocket.send_json(
        _envelope(
            "runtime_model_validation",
            node.id,
            {
                "runtime_model_binding_id": str(binding.id),
                "attempt_no": attempt.attempt_no,
                "engine_type": runtime.engine_type.value,
                "engine_model_id": binding.engine_model_id,
                "route_key": binding.route_key,
            },
        )
    )


async def _send_runtime_config(
    websocket: WebSocket,
    session: SessionDep,
    node: RuntimeNode,
    connection_id: uuid.UUID,
) -> None:
    if not await _connection_is_current(websocket, session, node.id, connection_id):
        return
    if node.runtime_profile_id is None:
        return
    runtime = session.get(RuntimeProfile, node.runtime_profile_id)
    if runtime is None:
        return
    payload: dict[str, Any] = {
        "revision": node.config_revision,
        "runtime_id": str(runtime.id),
        "route_mode": runtime.route_mode.value,
        "base_url": runtime.base_url,
        "model_id": runtime.model_id,
    }
    if runtime.route_mode == RuntimeRouteMode.DIRECT_ANTHROPIC:
        secret = session.exec(
            select(RuntimeSecret).where(RuntimeSecret.runtime_profile_id == runtime.id)
        ).first()
        if secret is None:
            return
        values = open_secret_payload(secret.secret_ciphertext)
        api_key = (
            values.get("api_key") or values.get("api_token") or values.get("token")
        )
        if not api_key:
            return
        payload["api_key"] = api_key
    if await _connection_is_current(websocket, session, node.id, connection_id):
        await websocket.send_json(_envelope("runtime_config", node.id, payload))


async def _send_pending_artifacts(
    websocket: WebSocket,
    session: SessionDep,
    node: RuntimeNode,
    connection_id: uuid.UUID,
) -> None:
    if not await _connection_is_current(websocket, session, node.id, connection_id):
        return
    service = ArtifactReleaseService(session)
    deployments = service.reserve_pending_for_node(node.id, connection_id)
    for deployment in deployments:
        if not await _connection_is_current(websocket, session, node.id, connection_id):
            return
        release = session.get(ArtifactRelease, deployment.release_id)
        artifact = session.get(RuntimeArtifact, deployment.artifact_id)
        if release is None or artifact is None:
            continue
        token = issue_artifact_download_token(
            deployment_id=deployment.id,
            node_id=node.id,
            artifact_id=artifact.id,
            storage_key=artifact.storage_key,
            expires_at=release.valid_until,
        )
        deployment_manifest = DeploymentManifest(
            namespace_id=str(deployment.namespace_id),
            node_id=str(node.id),
            release_id=str(release.id),
            deployment_id=str(deployment.id),
            artifact_id=str(artifact.id),
            logical_target=artifact.logical_target,
            artifact_manifest_sha256=hashlib.sha256(
                json.dumps(
                    artifact.manifest,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode()
            ).hexdigest(),
            valid_until=release.valid_until,
        )
        deployment_bytes = deployment_manifest.canonical_bytes()
        signer = configured_artifact_signer()
        try:
            await websocket.send_json(
                _envelope(
                    "artifact_deploy",
                    node.id,
                    {
                        "release_id": str(release.id),
                        "deployment_id": str(deployment.id),
                        "artifact_id": str(artifact.id),
                        "logical_target": artifact.logical_target.value,
                        "manifest": artifact.manifest,
                        "signature": artifact.signature,
                        "signing_public_key": artifact.signing_public_key,
                        "content_sha256": artifact.content_sha256,
                        "download_path": f"/api/v1/node/artifacts/{deployment.id}/download",
                        "download_token": token,
                        "valid_until": release.valid_until.isoformat(),
                        "deployment_manifest": deployment_manifest.model_dump(
                            mode="json"
                        ),
                        "deployment_signature": signer.sign(deployment_bytes),
                    },
                )
            )
        except Exception:
            service.release_reservation(deployment.id, connection_id)
            raise
        if not service.mark_dispatched(deployment.id, connection_id):
            await websocket.close(code=4409, reason="connection superseded")
            return


async def _send_pending_agent_releases(
    websocket: WebSocket,
    session: SessionDep,
    node: RuntimeNode,
    connection_id: uuid.UUID,
) -> None:
    if not await _connection_is_current(
        websocket, session, node.id, connection_id
    ):
        return
    await _send_pending_skill_sync(websocket, session, node, connection_id)
    runtime_instance_ids = session.exec(
        select(RuntimeInstance.id).where(RuntimeInstance.runtime_node_id == node.id)
    ).all()
    deployments = session.exec(
        select(AgentDeployment)
        .where(
            or_(
                AgentDeployment.runtime_profile_id == node.runtime_profile_id,
                col(AgentDeployment.runtime_instance_id).in_(runtime_instance_ids),
            ),
            AgentDeployment.status == AgentDeploymentStatus.PENDING,
        )
        .order_by(col(AgentDeployment.created_at))
        .with_for_update(skip_locked=True)
    ).all()
    now = datetime.now(timezone.utc)
    runtime = (
        session.get(RuntimeProfile, node.runtime_profile_id)
        if node.runtime_profile_id
        else None
    )
    for deployment in deployments:
        if deployment.expires_at and deployment.expires_at <= now:
            deployment.status = AgentDeploymentStatus.EXPIRED
            deployment.error = {"code": "deployment_expired_before_dispatch"}
            session.add(deployment)
            continue
        activation = session.get(AgentActivation, deployment.activation_id)
        release = (
            session.get(AgentRelease, activation.release_id) if activation else None
        )
        if release is None:
            deployment.status = AgentDeploymentStatus.FAILED
            deployment.error = {"code": "release_missing"}
            session.add(deployment)
            continue
        if deployment.runtime_instance_id is not None:
            runtime_instance = session.get(
                RuntimeInstance, deployment.runtime_instance_id
            )
            if runtime_instance is None or runtime_instance.runtime_node_id != node.id:
                deployment.status = AgentDeploymentStatus.FAILED
                deployment.error = {"code": "runtime_instance_unavailable"}
                session.add(deployment)
                continue
            try:
                configuration, capability, catalog = current_runtime_evidence(
                    session, runtime_instance
                )
            except RuntimeCatalogError as exc:
                deployment.status = AgentDeploymentStatus.INCOMPATIBLE
                deployment.error = {"code": exc.code, "message": exc.message}
                session.add(deployment)
                continue
            if deployment.capability_fingerprint != capability.capability_fingerprint:
                deployment.status = AgentDeploymentStatus.INCOMPATIBLE
                deployment.error = {"code": "stale_capability_fingerprint"}
                session.add(deployment)
                continue
            blockers = release_skill_blockers(
                session,
                release,
                runtime_instance_id=runtime_instance.id,
            )
            if blockers:
                deployment.status = AgentDeploymentStatus.FAILED
                deployment.error = {
                    "code": "skill_sync_blocked",
                    "skills": blockers,
                }
                session.add(deployment)
                continue
            if not release_skills_ready(
                session,
                release,
                runtime_instance_id=runtime_instance.id,
            ):
                continue
            deployment.status = AgentDeploymentStatus.DISPATCHED
            deployment.updated_at = now
            session.add(deployment)
            session.commit()
            try:
                await websocket.send_json(
                    _envelope(
                        "agent_release_deploy",
                        node.id,
                        {
                            "deployment_id": str(deployment.id),
                            "runtime_instance_id": str(runtime_instance.id),
                            "engine_type": runtime_instance.engine_type.value,
                            "configuration": {
                                "id": str(configuration.id),
                                "revision": configuration.revision,
                                "digest": configuration.configuration_digest,
                            },
                            "capability_fingerprint": capability.capability_fingerprint,
                            "capability_inventory": {
                                "engine_type": runtime_instance.engine_type.value,
                                "engine_version": capability.engine_version,
                                "adapter_version": capability.adapter_version,
                                "configuration_digest": capability.configuration_digest,
                                "capabilities": capability.capabilities,
                                "discovered_models": capability.discovered_models,
                            },
                            "model_catalog_fingerprint": catalog,
                            "release": _release_public(release),
                            "resolved_spec": release.resolved_spec,
                            "materialization": release.manifest["materialization"],
                        },
                    )
                )
            except Exception:
                deployment.status = AgentDeploymentStatus.PENDING
                session.add(deployment)
                session.commit()
                raise
            continue
        if runtime is None:
            deployment.status = AgentDeploymentStatus.FAILED
            deployment.error = {"code": "runtime_unavailable"}
            session.add(deployment)
            continue
        blockers = release_skill_blockers(session, release, runtime.id)
        if blockers:
            failed = any(item.get("status") == "failed" for item in blockers)
            deployment.status = (
                AgentDeploymentStatus.FAILED
                if failed
                else AgentDeploymentStatus.INCOMPATIBLE
            )
            deployment.error = {
                "code": "skill_sync_failed" if failed else "skill_sync_blocked",
                "skills": blockers,
            }
            session.add(deployment)
            if activation is not None:
                _recompute_activation(session, activation)
            continue
        if not release_skills_ready(session, release, runtime.id):
            continue
        capability_inventory = {
            "runtime_type": "node",
            "harness_capabilities": node.harness_capabilities,
            "config": {
                "allowed_working_roots": runtime.config.get("allowed_working_roots", [])
            },
        }
        fingerprint = canonical_digest(capability_inventory)
        if deployment.capability_fingerprint != fingerprint:
            deployment.status = AgentDeploymentStatus.FAILED
            deployment.error = {"code": "stale_capability_fingerprint"}
            session.add(deployment)
            continue
        deployment.status = AgentDeploymentStatus.DISPATCHED
        deployment.updated_at = now
        session.add(deployment)
        session.commit()
        try:
            await websocket.send_json(
                _envelope(
                    "agent_release_deploy",
                    node.id,
                    {
                        "deployment_id": str(deployment.id),
                        "runtime_profile_id": str(node.runtime_profile_id),
                        "capability_fingerprint": fingerprint,
                        "capability_inventory": capability_inventory,
                        "release": _release_public(release),
                        "resolved_spec": release.resolved_spec,
                        "materialization": release.manifest["materialization"],
                    },
                )
            )
        except Exception:
            deployment.status = AgentDeploymentStatus.PENDING
            session.add(deployment)
            session.commit()
            raise
    session.commit()


async def _send_pending_skill_sync(
    websocket: WebSocket,
    session: SessionDep,
    node: RuntimeNode,
    connection_id: uuid.UUID,
) -> None:
    if not await _connection_is_current(
        websocket, session, node.id, connection_id
    ):
        return
    runtime_instance_ids = session.exec(
        select(RuntimeInstance.id).where(RuntimeInstance.runtime_node_id == node.id)
    ).all()
    state = session.exec(
        select(RuntimeSkillState)
        .where(
            or_(
                RuntimeSkillState.runtime_profile_id == node.runtime_profile_id,
                col(RuntimeSkillState.runtime_instance_id).in_(runtime_instance_ids),
            ),
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
        return
    await websocket.send_json(
        _envelope(
            "skill_sync_requested",
            node.id,
            {
                "runtime_skill_state_id": str(state.id),
                "skill_id": str(state.skill_id),
                "generation": state.generation,
            },
        )
    )


async def _send_tool_approval_decisions(
    websocket: WebSocket,
    session: SessionDep,
    node: RuntimeNode,
    connection_id: uuid.UUID,
) -> None:
    if not await _connection_is_current(websocket, session, node.id, connection_id):
        return
    approvals = session.exec(
        select(ToolApprovalRequest)
        .join(AgentTask, col(ToolApprovalRequest.task_id) == AgentTask.id)
        .where(
            AgentTask.target_node_id == node.id,
            col(ToolApprovalRequest.status).in_(
                [
                    ApprovalStatus.APPROVED,
                    ApprovalStatus.DENIED,
                    ApprovalStatus.EXPIRED,
                    ApprovalStatus.CANCELLED,
                ]
            ),
        )
    ).all()
    for approval in approvals:
        client_key = f"{approval.task_id}:{approval.task_revision}:{approval.tool_qualified_name}:{approval.args_digest}"
        await websocket.send_json(
            _envelope(
                "tool_approval_decision",
                node.id,
                {
                    "approval_id": str(approval.id),
                    "client_approval_key": client_key,
                    "task_id": str(approval.task_id),
                    "task_revision": approval.task_revision,
                    "tool_call_id": approval.tool_call_id,
                    "args_digest": approval.args_digest,
                    "status": approval.status.value,
                },
            )
        )


async def _send_pending_mcp_validations(
    websocket: WebSocket,
    session: SessionDep,
    node: RuntimeNode,
    connection_id: uuid.UUID,
) -> None:
    if not await _connection_is_current(
        websocket, session, node.id, connection_id
    ):
        return
    now = datetime.now(timezone.utc)
    runtime_instance_ids = session.exec(
        select(RuntimeInstance.id).where(RuntimeInstance.runtime_node_id == node.id)
    ).all()
    attempts = session.exec(
        select(McpValidationAttempt)
        .join(
            McpTargetBinding,
            col(McpValidationAttempt.target_binding_id) == McpTargetBinding.id,
        )
        .where(
            or_(
                McpTargetBinding.runtime_profile_id == node.runtime_profile_id,
                col(McpTargetBinding.runtime_instance_id).in_(runtime_instance_ids),
            ),
            McpValidationAttempt.status == McpTargetStatus.PENDING,
        )
        .order_by(col(McpValidationAttempt.created_at))
        .with_for_update(skip_locked=True)
    ).all()
    for attempt in attempts:
        if attempt.expires_at and attempt.expires_at <= now:
            attempt.status = McpTargetStatus.EXPIRED
            target = session.get(McpTargetBinding, attempt.target_binding_id)
            if target:
                target.status = McpTargetStatus.EXPIRED
                session.add(target)
            session.add(attempt)
            continue
        claimed_until = attempt.result.get("claimed_until")
        if (
            isinstance(claimed_until, str)
            and datetime.fromisoformat(claimed_until) > now
        ):
            continue
        target = session.get(McpTargetBinding, attempt.target_binding_id)
        revision = (
            session.get(McpServerRevision, target.revision_id) if target else None
        )
        server = session.get(McpServer, revision.server_id) if revision else None
        if target is None or revision is None or server is None:
            continue
        runtime_instance = (
            session.get(RuntimeInstance, target.runtime_instance_id)
            if target.runtime_instance_id
            else None
        )
        runtime = (
            session.get(RuntimeProfile, target.runtime_profile_id)
            if target.runtime_profile_id
            else None
        )
        if runtime_instance is not None:
            try:
                _configuration, capability, _catalog = current_runtime_evidence(
                    session, runtime_instance
                )
            except RuntimeCatalogError:
                continue
            fingerprint = capability.capability_fingerprint
        elif runtime is not None:
            fingerprint = runtime_capability_fingerprint(
                runtime, node.harness_capabilities
            )
        else:
            continue
        attempt.result = {"claimed_until": (now + timedelta(seconds=60)).isoformat()}
        session.add(attempt)
        session.commit()
        try:
            await websocket.send_json(
                _envelope(
                    "mcp_validation",
                    node.id,
                    {
                        "attempt_id": str(attempt.id),
                        "target_binding_id": str(target.id),
                        "server_slug": server.slug,
                        "transport": revision.transport.value,
                        "config": revision.config,
                        "protocol_version": revision.protocol_version,
                        "runtime_instance_id": str(target.runtime_instance_id)
                        if target.runtime_instance_id
                        else None,
                        "secret_ref": target.secret_ref,
                        "capability_fingerprint": fingerprint,
                    },
                )
            )
        except Exception:
            attempt.result = {}
            session.add(attempt)
            session.commit()
            raise
    session.commit()


async def _send_pending_delegation_results(
    websocket: WebSocket,
    session: SessionDep,
    node: RuntimeNode,
    connection_id: uuid.UUID,
) -> None:
    if not await _connection_is_current(websocket, session, node.id, connection_id):
        return
    delegations = session.exec(select(AgentDelegation)).all()
    for delegation in delegations:
        source_task_id = delegation.input_payload.get("source_task_id")
        client_key = delegation.input_payload.get("client_delegation_key")
        if (
            not source_task_id
            or not client_key
            or delegation.input_payload.get("result_acknowledged_at")
        ):
            continue
        try:
            source_task = session.get(AgentTask, uuid.UUID(str(source_task_id)))
        except ValueError:
            continue
        if source_task is None or source_task.target_node_id != node.id:
            continue
        if delegation.status in {DelegationStatus.QUEUED, DelegationStatus.RUNNING}:
            conversation = session.get(Conversation, delegation.conversation_id)
            if conversation is not None:
                reconcile_agent_messages(session, conversation)
                session.commit()
                session.refresh(delegation)
        if delegation.status not in {
            DelegationStatus.COMPLETED,
            DelegationStatus.FAILED,
        }:
            continue
        await websocket.send_json(
            _envelope(
                "agent_delegation_result",
                node.id,
                {
                    "delegation_id": str(delegation.id),
                    "client_delegation_key": str(client_key),
                    "result": {
                        "id": str(delegation.id),
                        "status": delegation.status.value,
                        "result": delegation.result_payload,
                        "error": delegation.error,
                        "task_id": (
                            str(delegation.task_id) if delegation.task_id else None
                        ),
                    },
                },
            )
        )


async def _connection_is_current(
    websocket: WebSocket,
    session: SessionDep,
    node_id: uuid.UUID,
    connection_id: uuid.UUID,
) -> bool:
    session.expire_all()
    current = session.get(RuntimeNode, node_id)
    if current is None or current.connection_id != connection_id:
        await websocket.close(code=4409, reason="connection superseded")
        return False
    return True


@router.websocket("/ws")
async def node_websocket(websocket: WebSocket, session: SessionDep) -> None:
    if websocket.headers.get("x-node-protocol-version") != "2":
        await websocket.close(code=4406, reason="node protocol upgrade required")
        return
    authorization = websocket.headers.get("authorization", "")
    timestamp = websocket.headers.get("x-node-timestamp", "")
    nonce = websocket.headers.get("x-node-nonce", "")
    signature = websocket.headers.get("x-node-signature", "")
    if not authorization.startswith("Bearer "):
        await websocket.close(code=4401, reason="authentication failed")
        return
    try:
        node, credential = authenticate_node_connection(
            session, authorization[7:], timestamp, nonce, signature
        )
    except NodeAuthenticationError:
        await websocket.close(code=4403, reason="authentication failed")
        return
    connection_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    node.connection_id = connection_id
    node.connected_at = now
    node.last_seen_at = now
    session.add(node)
    session.commit()
    await websocket.accept()
    await websocket.send_json(
        _envelope("hello_ack", node.id, {"heartbeat_interval_seconds": 20})
    )
    if credential.replaced_by_id is not None:
        try:
            replacement, token = recover_node_credential_rotation(
                session, node, credential
            )
        except ValueError:
            await websocket.close(code=4403, reason="authentication failed")
            return
        session.commit()
        await websocket.send_json(
            _envelope(
                "credential_rotated",
                node.id,
                {
                    "credential": token,
                    "credential_id": str(replacement.id),
                    "previous_credential_id": str(credential.id),
                    "expires_at": replacement.expires_at.isoformat(),
                },
            )
        )
        return
    if credential.expires_at <= now + timedelta(days=30):
        await websocket.send_json(
            _envelope(
                "credential_rotation_required",
                node.id,
                {"credential_expires_at": credential.expires_at.isoformat()},
            )
        )
    await _send_pending_control(websocket, session, node, connection_id)
    await _send_pending_runtime_jobs(websocket, session, node, connection_id)
    await _send_pending_runtime_instance_configurations(
        websocket, session, node, connection_id
    )
    await _send_pending_native_model_validations(
        websocket, session, node, connection_id
    )
    await _send_pending_artifacts(websocket, session, node, connection_id)
    await _send_pending_agent_releases(websocket, session, node, connection_id)
    await _send_pending_mcp_validations(websocket, session, node, connection_id)
    await _send_tool_approval_decisions(websocket, session, node, connection_id)
    await _send_pending_delegation_results(websocket, session, node, connection_id)
    try:
        while True:
            raw = await websocket.receive_json()
            if raw.get("protocol_version") != "2":
                await websocket.send_json(
                    _envelope(
                        "error",
                        node.id,
                        {"code": "protocol_upgrade_required", "required": "2"},
                    )
                )
                await websocket.close(
                    code=4406, reason="node protocol upgrade required"
                )
                return
            try:
                message = Envelope.model_validate(raw)
            except ValidationError as exc:
                await websocket.send_json(
                    _envelope(
                        "error",
                        node.id,
                        {"code": "invalid_envelope", "detail": str(exc)},
                    )
                )
                continue
            session.expire_all()
            current = session.get(RuntimeNode, node.id)
            if current is None or current.connection_id != connection_id:
                await websocket.close(code=4409, reason="connection superseded")
                return
            if message.node_id != node.id:
                await websocket.close(code=4403, reason="node scope mismatch")
                return
            if message.type == "runtime_discovery_report":
                try:
                    result = report_runtime_discovery(
                        RuntimeDiscoveryReport(
                            node_id=node.id,
                            generation=message.payload["generation"],
                            installations=message.payload["installations"],
                        ),
                        session,
                    )
                except (KeyError, ValidationError, HTTPException) as exc:
                    detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
                    await websocket.send_json(
                        _envelope(
                            "error",
                            node.id,
                            {"code": "runtime_discovery_rejected", "detail": detail},
                            message.message_id,
                        )
                    )
                    continue
                await websocket.send_json(
                    _envelope(
                        "runtime_discovery_ack",
                        node.id,
                        result,
                        message.message_id,
                    )
                )
                await _send_pending_agent_releases(
                    websocket, session, current, connection_id
                )
            elif message.type == "heartbeat":
                if "harness_capabilities" in message.payload:
                    try:
                        current.harness_capabilities = validate_harness_capabilities(
                            message.payload["harness_capabilities"]
                        )
                        if current.runtime_profile_id:
                            runtime_profile = session.get(
                                RuntimeProfile, current.runtime_profile_id
                            )
                            if runtime_profile is not None:
                                runtime_profile.harness_capabilities = (
                                    current.harness_capabilities
                                )
                                session.add(runtime_profile)
                    except ValidationError as exc:
                        await websocket.send_json(
                            _envelope(
                                "error",
                                node.id,
                                {
                                    "code": "invalid_harness_capabilities",
                                    "detail": str(exc),
                                },
                                message.message_id,
                            )
                        )
                        continue
                if "mcp_secret_fingerprints" in message.payload:
                    fingerprints = message.payload["mcp_secret_fingerprints"]
                    if not isinstance(fingerprints, dict) or any(
                        not isinstance(ref, str)
                        or not ref
                        or not isinstance(fingerprint, str)
                        or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None
                        for ref, fingerprint in fingerprints.items()
                    ):
                        await websocket.send_json(
                            _envelope(
                                "error",
                                node.id,
                                {"code": "invalid_mcp_secret_fingerprints"},
                                message.message_id,
                            )
                        )
                        continue
                    runtime_instance_ids = session.exec(
                        select(RuntimeInstance.id).where(
                            RuntimeInstance.runtime_node_id == current.id
                        )
                    ).all()
                    targets = session.exec(
                        select(McpTargetBinding).where(
                            or_(
                                McpTargetBinding.runtime_profile_id
                                == current.runtime_profile_id,
                                col(McpTargetBinding.runtime_instance_id).in_(
                                    runtime_instance_ids
                                ),
                            ),
                            col(McpTargetBinding.secret_ref).is_not(None),
                        )
                    ).all()
                    for target in targets:
                        reported = fingerprints.get(str(target.secret_ref))
                        previous = target.secret_fingerprint
                        if reported != previous:
                            target.secret_fingerprint = reported
                            if (
                                previous is not None
                                or target.status == McpTargetStatus.VERIFIED
                            ):
                                target.status = McpTargetStatus.STALE
                            target.updated_at = datetime.now(timezone.utc)
                            session.add(target)
                current.last_seen_at = datetime.now(timezone.utc)
                session.add(current)
                session.commit()
                await websocket.send_json(
                    _envelope("heartbeat_ack", node.id, {}, message.message_id)
                )
                await _send_pending_control(websocket, session, current, connection_id)
                await _send_pending_runtime_jobs(
                    websocket, session, current, connection_id
                )
                await _send_pending_runtime_instance_configurations(
                    websocket, session, current, connection_id
                )
                await _send_pending_native_model_validations(
                    websocket, session, current, connection_id
                )
                await _send_pending_artifacts(
                    websocket, session, current, connection_id
                )
                await _send_pending_agent_releases(
                    websocket, session, current, connection_id
                )
                await _send_pending_mcp_validations(
                    websocket, session, current, connection_id
                )
                await _send_tool_approval_decisions(
                    websocket, session, current, connection_id
                )
                await _send_pending_runtime_jobs(
                    websocket, session, current, connection_id
                )
                await _send_pending_runtime_instance_configurations(
                    websocket, session, current, connection_id
                )
                await _send_pending_native_model_validations(
                    websocket, session, current, connection_id
                )
                await _send_pending_delegation_results(
                    websocket, session, current, connection_id
                )
            elif message.type == "reconcile":
                current.last_seen_at = datetime.now(timezone.utc)
                for task_id_value in message.payload.get("interrupted_task_ids", []):
                    try:
                        interrupted = session.get(AgentTask, uuid.UUID(task_id_value))
                    except ValueError:
                        continue
                    if (
                        interrupted
                        and interrupted.target_node_id == current.id
                        and interrupted.status
                        in {TaskStatus.DISPATCHED, TaskStatus.RUNNING}
                    ):
                        require_task_transition(
                            interrupted.status, TaskStatus.INTERRUPTED
                        )
                        interrupted.status = TaskStatus.INTERRUPTED
                        interrupted.completed_at = datetime.now(timezone.utc)
                        interrupted.lease_expires_at = None
                        session.add(interrupted)
                session.add(current)
                session.commit()
                await websocket.send_json(
                    _envelope(
                        "reconcile_ack",
                        node.id,
                        {"config_revision": current.config_revision},
                        message.message_id,
                    )
                )
                if (
                    int(message.payload.get("config_revision", 0))
                    < current.config_revision
                ):
                    await _send_runtime_config(
                        websocket, session, current, connection_id
                    )
                await _send_pending_artifacts(
                    websocket, session, current, connection_id
                )
                await _send_pending_agent_releases(
                    websocket, session, current, connection_id
                )
                await _send_pending_mcp_validations(
                    websocket, session, current, connection_id
                )
                await _send_tool_approval_decisions(
                    websocket, session, current, connection_id
                )
            elif message.type == "rotate_credential":
                try:
                    replacement, token = rotate_node_credential(
                        session, current, credential
                    )
                except ValueError as exc:
                    await websocket.send_json(
                        _envelope(
                            "error",
                            node.id,
                            {
                                "code": "credential_rotation_rejected",
                                "detail": str(exc),
                            },
                            message.message_id,
                        )
                    )
                    continue
                session.commit()
                await websocket.send_json(
                    _envelope(
                        "credential_rotated",
                        node.id,
                        {
                            "credential": token,
                            "credential_id": str(replacement.id),
                            "previous_credential_id": str(credential.id),
                            "expires_at": replacement.expires_at.isoformat(),
                        },
                        message.message_id,
                    )
                )
            elif message.type == "credential_rotation_ack":
                try:
                    retire_replaced_credential(
                        session,
                        current,
                        uuid.UUID(message.payload["previous_credential_id"]),
                        credential.id,
                    )
                except (KeyError, ValueError) as exc:
                    await websocket.send_json(
                        _envelope(
                            "error",
                            node.id,
                            {
                                "code": "credential_rotation_ack_rejected",
                                "detail": str(exc),
                            },
                            message.message_id,
                        )
                    )
                    continue
                session.commit()
                await websocket.send_json(
                    _envelope(
                        "credential_rotation_acknowledged",
                        node.id,
                        {},
                        message.message_id,
                    )
                )
            elif message.type == "tool_approval_request":
                try:
                    approval_body = ApprovalRequestCreate.model_validate(
                        {
                            key: value
                            for key, value in message.payload.items()
                            if key != "client_approval_key"
                        }
                    )
                    approval = request_tool_approval(approval_body, session)
                except (ValidationError, HTTPException) as exc:
                    await websocket.send_json(
                        _envelope(
                            "error",
                            node.id,
                            {
                                "code": "tool_approval_request_rejected",
                                "detail": str(exc),
                            },
                            message.message_id,
                        )
                    )
                    continue
                await websocket.send_json(
                    _envelope(
                        "tool_approval_request_ack",
                        node.id,
                        {
                            "approval_id": str(approval["id"]),
                            "client_approval_key": message.payload[
                                "client_approval_key"
                            ],
                        },
                        message.message_id,
                    )
                )
            elif message.type == "agent_delegation_request":
                try:
                    source_task = session.get(
                        AgentTask, uuid.UUID(message.payload["source_task_id"])
                    )
                    if (
                        source_task is None
                        or source_task.target_node_id != current.id
                        or source_task.claimed_by != f"node:{current.id}"
                    ):
                        raise ValueError("source task is not owned by this node")
                    delegation = create_runtime_delegation(
                        session,
                        source_task,
                        int(message.payload["source_task_revision"]),
                        uuid.UUID(message.payload["target_conversation_agent_id"]),
                        str(message.payload["content"]),
                    )
                    delegation.input_payload = {
                        **delegation.input_payload,
                        "client_delegation_key": str(
                            message.payload["client_delegation_key"]
                        ),
                    }
                    session.add(delegation)
                    session.commit()
                except (KeyError, ValueError, HTTPException) as exc:
                    session.rollback()
                    await websocket.send_json(
                        _envelope(
                            "error",
                            node.id,
                            {
                                "code": "agent_delegation_request_rejected",
                                "detail": str(exc),
                            },
                            message.message_id,
                        )
                    )
                    continue
                await websocket.send_json(
                    _envelope(
                        "agent_delegation_request_ack",
                        node.id,
                        {
                            "delegation_id": str(delegation.id),
                            "client_delegation_key": message.payload[
                                "client_delegation_key"
                            ],
                        },
                        message.message_id,
                    )
                )
            elif message.type == "agent_delegation_result_ack":
                try:
                    acknowledged_delegation = session.get(
                        AgentDelegation, uuid.UUID(message.payload["delegation_id"])
                    )
                except (KeyError, ValueError):
                    acknowledged_delegation = None
                if (
                    acknowledged_delegation is None
                    or acknowledged_delegation.input_payload.get(
                        "client_delegation_key"
                    )
                    != message.payload.get("client_delegation_key")
                ):
                    await websocket.send_json(
                        _envelope(
                            "error",
                            node.id,
                            {"code": "agent_delegation_ack_scope_mismatch"},
                            message.message_id,
                        )
                    )
                    continue
                acknowledged_delegation.input_payload = {
                    **acknowledged_delegation.input_payload,
                    "result_acknowledged_at": datetime.now(timezone.utc).isoformat(),
                }
                session.add(acknowledged_delegation)
                session.commit()
            elif message.type == "runtime_job_rejected":
                try:
                    job_id = uuid.UUID(message.payload["job_id"])
                    revision = int(message.payload["revision"])
                except (KeyError, ValueError):
                    job_id = uuid.UUID(int=0)
                    revision = -1
                job = session.exec(
                    select(RuntimeJob).where(RuntimeJob.id == job_id).with_for_update()
                ).first()
                if (
                    job is None
                    or job.target_node_id != current.id
                    or job.revision != revision
                    or job.status != RuntimeJobStatus.QUEUED
                    or job.dispatch_connection_id != connection_id
                ):
                    await websocket.send_json(
                        _envelope(
                            "error",
                            node.id,
                            {"code": "runtime_job_reject_scope_mismatch"},
                            message.message_id,
                        )
                    )
                    continue
                job.status = (
                    RuntimeJobStatus.NEEDS_MANUAL_RESOLUTION
                    if job.side_effecting
                    else RuntimeJobStatus.FAILED
                )
                job.error = {
                    "code": "node_runtime_job_dispatch_conflict",
                    "reason": str(message.payload.get("reason", "unknown")),
                }
                job.completed_at = datetime.now(timezone.utc)
                job.updated_at = job.completed_at
                job.dispatch_connection_id = None
                job.dispatch_reserved_until = None
                session.add(job)
                session.commit()
                await websocket.send_json(
                    _envelope(
                        "runtime_job_rejected_ack",
                        node.id,
                        {"job_id": str(job.id), "status": job.status.value},
                        message.message_id,
                    )
                )
            elif message.type == "runtime_job_accepted":
                try:
                    job_id = uuid.UUID(message.payload["job_id"])
                    revision = int(message.payload["revision"])
                except (KeyError, ValueError):
                    job_id = uuid.UUID(int=0)
                    revision = -1
                job = session.exec(
                    select(RuntimeJob).where(RuntimeJob.id == job_id).with_for_update()
                ).first()
                if (
                    job is None
                    or job.target_node_id != current.id
                    or job.revision != revision
                    or job.status != RuntimeJobStatus.QUEUED
                    or job.dispatch_connection_id != connection_id
                    or job.dispatch_reserved_until is None
                    or job.dispatch_reserved_until < datetime.now(timezone.utc)
                ):
                    await websocket.send_json(
                        _envelope(
                            "error",
                            node.id,
                            {"code": "runtime_job_accept_scope_mismatch"},
                            message.message_id,
                        )
                    )
                    continue
                job.status = RuntimeJobStatus.DISPATCHED
                job.claimed_by = f"node:{current.id}"
                job.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
                job.dispatch_connection_id = None
                job.dispatch_reserved_until = None
                job.updated_at = datetime.now(timezone.utc)
                session.add(job)
                session.commit()
            elif message.type == "runtime_job_lease":
                job = session.exec(
                    select(RuntimeJob)
                    .where(RuntimeJob.id == uuid.UUID(message.payload["job_id"]))
                    .with_for_update()
                ).first()
                if (
                    job is None
                    or job.target_node_id != current.id
                    or job.claimed_by != f"node:{current.id}"
                    or job.revision != int(message.payload["revision"])
                    or job.status
                    not in {RuntimeJobStatus.DISPATCHED, RuntimeJobStatus.RUNNING}
                ):
                    await websocket.send_json(
                        _envelope(
                            "error",
                            node.id,
                            {"code": "runtime_job_lease_scope_mismatch"},
                            message.message_id,
                        )
                    )
                    continue
                job.status = RuntimeJobStatus.RUNNING
                job.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
                job.updated_at = datetime.now(timezone.utc)
                session.add(job)
                session.commit()
            elif message.type == "runtime_job_result":
                job = session.exec(
                    select(RuntimeJob)
                    .where(RuntimeJob.id == uuid.UUID(message.payload["job_id"]))
                    .with_for_update()
                ).first()
                try:
                    result_status = RuntimeJobStatus(message.payload["status"])
                except (KeyError, ValueError):
                    result_status = RuntimeJobStatus.FAILED
                if (
                    job is None
                    or job.target_node_id != current.id
                    or job.claimed_by != f"node:{current.id}"
                    or job.revision != int(message.payload.get("revision", -1))
                    or job.status
                    not in {RuntimeJobStatus.DISPATCHED, RuntimeJobStatus.RUNNING}
                    or result_status
                    not in {
                        RuntimeJobStatus.SUCCEEDED,
                        RuntimeJobStatus.FAILED,
                        RuntimeJobStatus.NEEDS_MANUAL_RESOLUTION,
                    }
                ):
                    await websocket.send_json(
                        _envelope(
                            "error",
                            node.id,
                            {"code": "runtime_job_result_scope_mismatch"},
                            message.message_id,
                        )
                    )
                    continue
                job.status = result_status
                job.result = message.payload.get("result")
                job.error = message.payload.get("error")
                job.completed_at = datetime.now(timezone.utc)
                job.updated_at = job.completed_at
                job.lease_expires_at = None
                session.add(job)
                session.commit()
            elif message.type == "task_accepted":
                task_id = uuid.UUID(message.payload["task_id"])
                revision = int(message.payload["revision"])
                task = session.exec(
                    select(AgentTask).where(AgentTask.id == task_id).with_for_update()
                ).first()
                if (
                    task is None
                    or task.target_node_id != node.id
                    or task.revision != revision
                ):
                    await websocket.send_json(
                        _envelope(
                            "error",
                            node.id,
                            {"code": "task_accept_scope_mismatch"},
                            message.message_id,
                        )
                    )
                    continue
                if task.status == TaskStatus.QUEUED:
                    if (
                        task.dispatch_connection_id != connection_id
                        or task.dispatch_reserved_until is None
                        or task.dispatch_reserved_until < datetime.now(timezone.utc)
                    ):
                        await websocket.send_json(
                            _envelope(
                                "error",
                                node.id,
                                {"code": "task_reservation_scope_mismatch"},
                                message.message_id,
                            )
                        )
                        continue
                    require_task_transition(task.status, TaskStatus.DISPATCHED)
                    task.status = TaskStatus.DISPATCHED
                    append_and_apply_event(
                        session,
                        task.id,
                        1,
                        AgentEventType.STATUS,
                        {
                            "state": TaskStatus.DISPATCHED.value,
                            "executor": f"node:{node.id}",
                        },
                    )
                elif task.status not in {TaskStatus.DISPATCHED, TaskStatus.RUNNING}:
                    await websocket.send_json(
                        _envelope(
                            "error",
                            node.id,
                            {"code": "task_not_dispatchable"},
                            message.message_id,
                        )
                    )
                    continue
                task.claimed_by = f"node:{node.id}"
                task.dispatch_connection_id = None
                task.dispatch_reserved_until = None
                task.lease_expires_at = datetime.now(timezone.utc) + timedelta(
                    minutes=5
                )
                task.updated_at = datetime.now(timezone.utc)
                session.add(task)
                if task.snapshot.get("resolved_spec_schema_version") in {
                    "1.1",
                    "2.0",
                }:
                    try:
                        evidence = [
                            SkillUsageItem.model_validate(item)
                            for item in message.payload.get("skill_evidence", [])
                        ]
                        record_task_skill_usage(
                            session,
                            task,
                            current.runtime_profile_id
                            if task.runtime_instance_id is None
                            else None,
                            evidence,
                            runtime_instance_id=task.runtime_instance_id,
                        )
                    except (HTTPException, ValidationError) as exc:
                        session.rollback()
                        rejected_task = session.exec(
                            select(AgentTask)
                            .where(AgentTask.id == task_id)
                            .with_for_update()
                        ).first()
                        if rejected_task is not None and rejected_task.status in {
                            TaskStatus.QUEUED,
                            TaskStatus.DISPATCHED,
                        }:
                            require_task_transition(
                                rejected_task.status, TaskStatus.REJECTED
                            )
                            rejected_task.status = TaskStatus.REJECTED
                            rejected_task.final_result = {
                                "code": "task_skill_evidence_rejected",
                                "detail": str(exc),
                            }
                            rejected_task.completed_at = datetime.now(timezone.utc)
                            rejected_task.dispatch_connection_id = None
                            rejected_task.dispatch_reserved_until = None
                            session.add(rejected_task)
                            session.commit()
                        await websocket.send_json(
                            _envelope(
                                "task_accept_rejected",
                                node.id,
                                {
                                    "code": "task_skill_evidence_rejected",
                                    "task_id": str(task_id),
                                    "detail": str(exc),
                                },
                                message.message_id,
                            )
                        )
                        continue
                ack_payload: dict[str, Any] = {"task_id": str(task.id)}
                if task.runtime_instance_id is not None:
                    usage = session.exec(
                        select(AgentTaskModelUsage).where(
                            AgentTaskModelUsage.task_id == task.id
                        )
                    ).first()
                    binding = (
                        session.get(RuntimeModelBinding, usage.runtime_model_binding_id)
                        if usage
                        else None
                    )
                    evidence = message.payload.get("model_evidence")
                    expected = (
                        {
                            "runtime_instance_id": str(usage.runtime_instance_id),
                            "runtime_model_binding_id": str(usage.runtime_model_binding_id),
                            "engine_type": usage.engine_type,
                            "engine_version": usage.engine_version,
                            "adapter_version": usage.adapter_version,
                            "engine_model_id": task.snapshot.get("engine_model_id"),
                            "route_type": usage.route_type,
                            "route_reference": usage.route_reference,
                            "runtime_configuration_digest": usage.runtime_configuration_digest,
                            "capability_fingerprint": usage.capability_fingerprint,
                            "effective_spec_digest": usage.effective_spec_digest,
                        }
                        if usage
                        else None
                    )
                    if (
                        not isinstance(evidence, dict)
                        or evidence != expected
                        or binding is None
                        or binding.status != RuntimeModelBindingStatus.AVAILABLE
                    ):
                        session.rollback()
                        rejected = session.get(AgentTask, task.id)
                        if rejected is not None:
                            rejected.status = TaskStatus.REJECTED
                            rejected.final_result = {"code": "runtime_model_evidence_mismatch"}
                            rejected.completed_at = datetime.now(timezone.utc)
                            session.add(rejected)
                            session.commit()
                        await websocket.send_json(
                            _envelope(
                                "task_accept_rejected",
                                node.id,
                                {
                                    "code": "runtime_model_evidence_mismatch",
                                    "task_id": str(task.id),
                                },
                                message.message_id,
                            )
                        )
                        continue
                    usage.runtime_evidence = evidence
                    usage.evidenced_at = datetime.now(timezone.utc)
                    session.add(usage)
                    if binding.route_type == RuntimeModelRouteType.RUNTIME_NATIVE:
                        ack_payload["route"] = {"mode": "runtime_native"}
                    elif binding.route_type == RuntimeModelRouteType.PROVIDER_CONFIG:
                        if (
                            not settings.MODEL_GATEWAY_PUBLIC_URL
                            or binding.provider_config_id is None
                        ):
                            raise HTTPException(409, "Model Gateway route is unavailable")
                        token = issue_gateway_token(
                            task.namespace_id,
                            task.runtime_instance_id,
                            task.id,
                            binding.engine_model_id,
                            provider_config_id=binding.provider_config_id,
                            runtime_model_binding_id=binding.id,
                        )
                        ack_payload["route"] = {
                            "mode": "platform_gateway",
                            "base_url": f"{settings.MODEL_GATEWAY_PUBLIC_URL.rstrip('/')}/tasks/{task.id}",
                            "api_key": token,
                            "runtime_id": str(task.runtime_instance_id),
                        }
                    else:
                        raise HTTPException(409, "Runtime model route is unsupported")
                session.commit()
                await websocket.send_json(
                    _envelope(
                        "task_accept_ack",
                        node.id,
                        ack_payload,
                        message.message_id,
                    )
                )
            elif message.type == "lease_renewed":
                task = session.get(AgentTask, uuid.UUID(message.payload["task_id"]))
                if (
                    task is None
                    or task.target_node_id != node.id
                    or task.revision != int(message.payload["revision"])
                ):
                    await websocket.send_json(
                        _envelope(
                            "error",
                            node.id,
                            {"code": "lease_scope_mismatch"},
                            message.message_id,
                        )
                    )
                    continue
                if task.status == TaskStatus.DISPATCHED:
                    require_task_transition(task.status, TaskStatus.RUNNING)
                    task.status = TaskStatus.RUNNING
                if task.status != TaskStatus.RUNNING:
                    await websocket.send_json(
                        _envelope(
                            "error",
                            node.id,
                            {"code": "lease_not_renewable"},
                            message.message_id,
                        )
                    )
                    continue
                task.lease_expires_at = datetime.now(timezone.utc) + timedelta(
                    minutes=5
                )
                task.updated_at = datetime.now(timezone.utc)
                session.add(task)
                session.commit()
            elif message.type == "task_rejected":
                task = session.get(AgentTask, uuid.UUID(message.payload["task_id"]))
                if task is None or task.target_node_id != node.id:
                    continue
                if task.status in {TaskStatus.QUEUED, TaskStatus.DISPATCHED}:
                    require_task_transition(task.status, TaskStatus.REJECTED)
                    task.status = TaskStatus.REJECTED
                    task.final_result = message.payload
                    task.completed_at = datetime.now(timezone.utc)
                    task.lease_expires_at = None
                    session.add(task)
                    session.commit()
            elif message.type == "task_events":
                task = session.get(AgentTask, uuid.UUID(message.payload["task_id"]))
                if task is None or task.target_node_id != node.id:
                    await websocket.send_json(
                        _envelope(
                            "error",
                            node.id,
                            {"code": "event_scope_mismatch"},
                            message.message_id,
                        )
                    )
                    continue
                try:
                    for event in message.payload.get("events", []):
                        event_type = AgentEventType(event["event_type"])
                        append_and_apply_event(
                            session,
                            task.id,
                            int(event["sequence"]),
                            event_type,
                            event["payload"],
                        )
                    session.commit()
                except EventSequenceConflict as exc:
                    session.commit()
                    await websocket.send_json(
                        _envelope(
                            "error",
                            node.id,
                            {"code": "event_sequence_conflict", "detail": str(exc)},
                            message.message_id,
                        )
                    )
                    continue
                except (KeyError, ValueError, LookupError) as exc:
                    session.rollback()
                    await websocket.send_json(
                        _envelope(
                            "error",
                            node.id,
                            {"code": "event_append_failed", "detail": str(exc)},
                            message.message_id,
                        )
                    )
                    continue
                contiguous = last_contiguous_event_sequence(session, task.id)
                await websocket.send_json(
                    _envelope(
                        "task_events_ack",
                        node.id,
                        {"task_id": str(task.id), "through_sequence": contiguous},
                        message.message_id,
                    )
                )
            elif message.type == "task_cancelled":
                task = session.get(AgentTask, uuid.UUID(message.payload["task_id"]))
                if (
                    task
                    and task.target_node_id == node.id
                    and task.status == TaskStatus.CANCELLING
                ):
                    require_task_transition(task.status, TaskStatus.CANCELLED)
                    task.status = TaskStatus.CANCELLED
                    task.completed_at = datetime.now(timezone.utc)
                    task.lease_expires_at = None
                    session.add(task)
                    session.commit()
            elif message.type == "runtime_instance_configuration_result":
                try:
                    result = report_runtime_configuration_result(
                        ConfigurationResultInput(
                            worker_id=f"node:{node.id}",
                            **message.payload,
                        ),
                        session,
                    )
                except (ValidationError, HTTPException) as exc:
                    detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
                    await websocket.send_json(
                        _envelope(
                            "error",
                            node.id,
                            {
                                "code": "runtime_configuration_result_rejected",
                                "detail": detail,
                            },
                            message.message_id,
                        )
                    )
                    continue
                await websocket.send_json(
                    _envelope(
                        "runtime_instance_configuration_ack",
                        node.id,
                        result,
                        message.message_id,
                    )
                )
                await _send_pending_runtime_instance_configurations(
                    websocket, session, current, connection_id
                )
            elif message.type == "runtime_model_validation_result":
                try:
                    result = report_native_model_validation_result(
                        NativeModelValidationResult(
                            worker_id=f"node:{node.id}",
                            **message.payload,
                        ),
                        session,
                    )
                except (ValidationError, HTTPException) as exc:
                    detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
                    await websocket.send_json(
                        _envelope(
                            "error",
                            node.id,
                            {
                                "code": "runtime_model_validation_result_rejected",
                                "detail": detail,
                            },
                            message.message_id,
                        )
                    )
                    continue
                await websocket.send_json(
                    _envelope(
                        "runtime_model_validation_ack",
                        node.id,
                        result,
                        message.message_id,
                    )
                )
                await _send_pending_native_model_validations(
                    websocket, session, current, connection_id
                )
            elif message.type in {"runtime_config_applied", "runtime_config_rejected"}:
                runtime_id = uuid.UUID(message.payload["runtime_id"])
                runtime = session.get(RuntimeProfile, runtime_id)
                if (
                    runtime is None
                    or current.runtime_profile_id != runtime.id
                    or int(message.payload["revision"]) != current.config_revision
                ):
                    await websocket.send_json(
                        _envelope(
                            "error",
                            node.id,
                            {"code": "runtime_config_scope_mismatch"},
                            message.message_id,
                        )
                    )
                    continue
                runtime.config = {
                    **runtime.config,
                    "direct_compatibility_verified": message.type
                    == "runtime_config_applied"
                    and message.payload.get("direct_compatibility_verified") is True,
                    "direct_compatibility_fingerprint": message.payload.get(
                        "fingerprint"
                    ),
                    "direct_compatibility_checked_at": datetime.now(
                        timezone.utc
                    ).isoformat(),
                    "node_config_applied_revision": current.config_revision
                    if message.type == "runtime_config_applied"
                    else None,
                }
                session.add(runtime)
                session.commit()
                await websocket.send_json(
                    _envelope(
                        "runtime_config_ack",
                        node.id,
                        {"revision": current.config_revision},
                        message.message_id,
                    )
                )
            elif message.type == "mcp_validation_result":
                try:
                    attempt_id = uuid.UUID(message.payload["attempt_id"])
                    body = McpValidationResult.model_validate(
                        {
                            key: value
                            for key, value in message.payload.items()
                            if key != "attempt_id"
                        }
                    )
                except (KeyError, ValueError, ValidationError) as exc:
                    await websocket.send_json(
                        _envelope(
                            "error",
                            node.id,
                            {
                                "code": "invalid_mcp_validation_result",
                                "detail": str(exc),
                            },
                            message.message_id,
                        )
                    )
                    continue
                attempt = session.exec(
                    select(McpValidationAttempt)
                    .where(McpValidationAttempt.id == attempt_id)
                    .with_for_update()
                ).first()
                validation_target = (
                    session.get(McpTargetBinding, attempt.target_binding_id)
                    if attempt
                    else None
                )
                mcp_revision = (
                    session.get(McpServerRevision, validation_target.revision_id)
                    if validation_target
                    else None
                )
                server = (
                    session.get(McpServer, mcp_revision.server_id)
                    if mcp_revision
                    else None
                )
                runtime = (
                    session.get(RuntimeProfile, validation_target.runtime_profile_id)
                    if validation_target and validation_target.runtime_profile_id
                    else None
                )
                validation_runtime_instance = (
                    session.get(
                        RuntimeInstance, validation_target.runtime_instance_id
                    )
                    if validation_target and validation_target.runtime_instance_id
                    else None
                )
                if (
                    attempt is None
                    or validation_target is None
                    or server is None
                    or (
                        validation_runtime_instance is not None
                        and validation_runtime_instance.runtime_node_id != current.id
                    )
                    or (
                        validation_runtime_instance is None
                        and (
                            runtime is None
                            or validation_target.runtime_profile_id
                            != current.runtime_profile_id
                        )
                    )
                ):
                    await websocket.send_json(
                        _envelope(
                            "error",
                            node.id,
                            {"code": "mcp_validation_scope_mismatch"},
                            message.message_id,
                        )
                    )
                    continue
                expected_fingerprint = (
                    current_runtime_evidence(
                        session, validation_runtime_instance
                    )[1].capability_fingerprint
                    if validation_runtime_instance is not None
                    else runtime_capability_fingerprint(
                        runtime, current.harness_capabilities
                    )
                )
                if body.capability_fingerprint != expected_fingerprint:
                    body = McpValidationResult(
                        status="failed",
                        capability_fingerprint=body.capability_fingerprint,
                        error={"code": "stale_capability_fingerprint"},
                    )
                try:
                    _apply_validation_result(
                        attempt, validation_target, server, body, session
                    )
                    session.commit()
                except HTTPException as exc:
                    session.rollback()
                    await websocket.send_json(
                        _envelope(
                            "error",
                            node.id,
                            {
                                "code": "mcp_validation_result_rejected",
                                "detail": exc.detail,
                            },
                            message.message_id,
                        )
                    )
                    continue
                await websocket.send_json(
                    _envelope(
                        "mcp_validation_result_ack",
                        node.id,
                        {"attempt_id": str(attempt.id), "status": attempt.status.value},
                        message.message_id,
                    )
                )
            elif message.type == "skill_sync_desired_request":
                try:
                    state_id = uuid.UUID(message.payload["runtime_skill_state_id"])
                    generation = int(message.payload["generation"])
                except (KeyError, TypeError, ValueError):
                    state_id = uuid.UUID(int=0)
                    generation = -1
                state = session.exec(
                    select(RuntimeSkillState)
                    .where(RuntimeSkillState.id == state_id)
                    .with_for_update()
                ).first()
                state_runtime_instance = (
                    session.get(RuntimeInstance, state.runtime_instance_id)
                    if state and state.runtime_instance_id
                    else None
                )
                if (
                    state is None
                    or (
                        state.runtime_instance_id is not None
                        and (
                            state_runtime_instance is None
                            or state_runtime_instance.runtime_node_id != current.id
                        )
                    )
                    or (
                        state.runtime_instance_id is None
                        and state.runtime_profile_id != current.runtime_profile_id
                    )
                    or state.generation != generation
                    or state.subscription_count <= 0
                ):
                    await websocket.send_json(
                        _envelope(
                            "skill_sync_unavailable",
                            node.id,
                            {"code": "skill_sync_desired_scope_mismatch"},
                            message.message_id,
                        )
                    )
                    continue
                try:
                    attempt, skill, version = create_sync_attempt(
                        session, state, trigger="notification"
                    )
                except ValueError as exc:
                    if state.status != "blocked":
                        state.status = "failed"
                        state.last_error = {
                            "code": "skill_bundle_invalid",
                            "message": str(exc),
                        }
                    session.add(state)
                    session.commit()
                    await websocket.send_json(
                        _envelope(
                            "skill_sync_unavailable",
                            node.id,
                            {"code": state.status, "detail": state.last_error},
                            message.message_id,
                        )
                    )
                    continue
                payload = sync_payload(attempt, skill, version)
                payload.update(
                    {
                        **(
                            {"runtime_instance_id": str(state.runtime_instance_id)}
                            if state.runtime_instance_id
                            else {"runtime_profile_id": str(state.runtime_profile_id)}
                        ),
                        "download_path": f"/api/v1/node/skill-sync/{attempt.id}/download",
                        "download_token": issue_skill_download_token(
                            attempt_id=attempt.id,
                            node_id=node.id,
                            runtime_profile_id=state.runtime_profile_id,
                            runtime_instance_id=state.runtime_instance_id,
                            skill_id=state.skill_id,
                            version_id=version.id,
                            content_sha256=version.content_sha256,
                            storage_key=version.storage_key,
                        ),
                    }
                )
                session.commit()
                await websocket.send_json(
                    _envelope(
                        "skill_sync_desired",
                        node.id,
                        payload,
                        message.message_id,
                    )
                )
            elif message.type == "skill_sync_result":
                try:
                    attempt_id = uuid.UUID(message.payload["attempt_id"])
                    body = SkillSyncResult.model_validate(
                        {
                            key: value
                            for key, value in message.payload.items()
                            if key != "attempt_id"
                        }
                    )
                    state = session.exec(
                        select(RuntimeSkillState)
                        .join(
                            RuntimeSkillSyncAttempt,
                            col(RuntimeSkillSyncAttempt.runtime_skill_state_id)
                            == RuntimeSkillState.id,
                        )
                        .where(RuntimeSkillSyncAttempt.id == attempt_id)
                    ).first()
                except (KeyError, ValueError, ValidationError):
                    state = None
                state_runtime_instance = (
                    session.get(RuntimeInstance, state.runtime_instance_id)
                    if state and state.runtime_instance_id
                    else None
                )
                if (
                    state is None
                    or (
                        state.runtime_instance_id is not None
                        and (
                            state_runtime_instance is None
                            or state_runtime_instance.runtime_node_id != current.id
                        )
                    )
                    or (
                        state.runtime_instance_id is None
                        and state.runtime_profile_id != current.runtime_profile_id
                    )
                ):
                    await websocket.send_json(
                        _envelope(
                            "error",
                            node.id,
                            {"code": "skill_sync_scope_mismatch"},
                            message.message_id,
                        )
                    )
                    continue
                try:
                    result = apply_skill_sync_result(attempt_id, body, session)
                except HTTPException as exc:
                    session.rollback()
                    await websocket.send_json(
                        _envelope(
                            "error",
                            node.id,
                            {
                                "code": "skill_sync_result_rejected",
                                "detail": exc.detail,
                            },
                            message.message_id,
                        )
                    )
                    continue
                if body.status == "verified" and result["status"] == "committing":
                    await websocket.send_json(
                        _envelope(
                            "skill_sync_commit",
                            node.id,
                            {"attempt_id": str(attempt_id)},
                            message.message_id,
                        )
                    )
                    continue
                await websocket.send_json(
                    _envelope(
                        "skill_sync_result_ack",
                        node.id,
                        {"attempt_id": str(attempt_id), "status": result["status"]},
                        message.message_id,
                    )
                )
                await _send_pending_agent_releases(
                    websocket, session, current, connection_id
                )
            elif message.type == "agent_release_result":
                try:
                    deployment_id = uuid.UUID(message.payload["deployment_id"])
                except (KeyError, ValueError):
                    await websocket.send_json(
                        _envelope(
                            "error",
                            node.id,
                            {"code": "invalid_agent_release_result"},
                            message.message_id,
                        )
                    )
                    continue
                deployment = session.exec(
                    select(AgentDeployment)
                    .where(AgentDeployment.id == deployment_id)
                    .with_for_update()
                ).first()
                activation = (
                    session.get(AgentActivation, deployment.activation_id)
                    if deployment
                    else None
                )
                release = (
                    session.get(AgentRelease, activation.release_id)
                    if activation
                    else None
                )
                deployment_runtime_instance = (
                    session.get(RuntimeInstance, deployment.runtime_instance_id)
                    if deployment and deployment.runtime_instance_id
                    else None
                )
                if (
                    deployment is None
                    or activation is None
                    or release is None
                    or (
                        deployment.runtime_instance_id is not None
                        and (
                            deployment_runtime_instance is None
                            or deployment_runtime_instance.runtime_node_id != current.id
                        )
                    )
                    or (
                        deployment.runtime_instance_id is None
                        and deployment.runtime_profile_id != current.runtime_profile_id
                    )
                    or deployment.status != AgentDeploymentStatus.DISPATCHED
                ):
                    await websocket.send_json(
                        _envelope(
                            "error",
                            node.id,
                            {"code": "agent_deployment_scope_mismatch"},
                            message.message_id,
                        )
                    )
                    continue
                if message.payload.get("status") != "applied":
                    deployment.status = AgentDeploymentStatus.FAILED
                    deployment.error = message.payload.get("error") or {
                        "code": "agent_release_apply_failed"
                    }
                elif (
                    message.payload.get("capability_fingerprint")
                    != deployment.capability_fingerprint
                ):
                    deployment.status = AgentDeploymentStatus.FAILED
                    deployment.error = {"code": "stale_capability_fingerprint"}
                elif (
                    message.payload.get("resolved_spec_digest")
                    != release.resolved_spec_digest
                    or message.payload.get("materialization_digest")
                    != release.resolved_spec_digest
                ):
                    deployment.status = AgentDeploymentStatus.FAILED
                    deployment.error = {"code": "release_digest_mismatch"}
                else:
                    deployment.status = AgentDeploymentStatus.APPLIED
                    deployment.applied_digest = release.resolved_spec_digest
                    agent_binding = session.exec(
                        select(RuntimeAgentRelease)
                        .where(
                            (
                                RuntimeAgentRelease.runtime_instance_id
                                == deployment.runtime_instance_id
                                if deployment.runtime_instance_id
                                else RuntimeAgentRelease.runtime_profile_id
                                == deployment.runtime_profile_id
                            ),
                            RuntimeAgentRelease.agent_id == release.agent_id,
                        )
                        .with_for_update()
                    ).first()
                    if agent_binding is None:
                        agent_binding = RuntimeAgentRelease(
                            namespace_id=release.namespace_id,
                            runtime_profile_id=deployment.runtime_profile_id,
                            runtime_instance_id=deployment.runtime_instance_id,
                            agent_id=release.agent_id,
                            current_release_id=release.id,
                            applied_digest=release.resolved_spec_digest,
                            materialization_digest=release.resolved_spec_digest,
                        )
                    else:
                        if agent_binding.current_release_id != release.id:
                            agent_binding.previous_release_id = (
                                agent_binding.current_release_id
                            )
                        agent_binding.current_release_id = release.id
                        agent_binding.applied_digest = release.resolved_spec_digest
                        agent_binding.materialization_digest = (
                            release.resolved_spec_digest
                        )
                        agent_binding.updated_at = datetime.now(timezone.utc)
                    if deployment_runtime_instance is not None:
                        configuration, capability, catalog = current_runtime_evidence(
                            session, deployment_runtime_instance
                        )
                        agent_binding.runtime_configuration_revision_id = configuration.id
                        agent_binding.runtime_capability_report_id = capability.id
                        agent_binding.adapter_version = capability.adapter_version
                        agent_binding.runtime_model_catalog_fingerprint = catalog
                        agent_binding.effective_spec_digest = canonical_digest(
                            {
                                "release_digest": release.resolved_spec_digest,
                                "configuration_digest": configuration.configuration_digest,
                                "capability_fingerprint": capability.capability_fingerprint,
                                "model_catalog_fingerprint": catalog,
                            }
                        )
                    session.add(agent_binding)
                    if deployment.runtime_profile_id is not None:
                        reconcile_runtime_skill_subscriptions(
                            session, deployment.runtime_profile_id
                        )
                    elif deployment.runtime_instance_id is not None:
                        reconcile_runtime_skill_subscriptions(
                            session,
                            runtime_instance_id=deployment.runtime_instance_id,
                        )
                deployment.updated_at = datetime.now(timezone.utc)
                session.add(deployment)
                _recompute_activation(session, activation)
                session.commit()
                await websocket.send_json(
                    _envelope(
                        "agent_release_status_ack",
                        node.id,
                        {"deployment_id": str(deployment.id)},
                        message.message_id,
                    )
                )
            elif message.type in {"artifact_applied", "artifact_failed"}:
                artifact_deployment = session.get(
                    ArtifactDeployment, uuid.UUID(message.payload["deployment_id"])
                )
                if (
                    artifact_deployment is None
                    or artifact_deployment.node_id != current.id
                    or artifact_deployment.status != DeploymentStatus.DISPATCHED
                ):
                    await websocket.send_json(
                        _envelope(
                            "error",
                            node.id,
                            {"code": "artifact_deployment_scope_mismatch"},
                            message.message_id,
                        )
                    )
                    continue
                artifact = session.get(RuntimeArtifact, artifact_deployment.artifact_id)
                if artifact is None:
                    continue
                if message.type == "artifact_applied":
                    installed = session.exec(
                        select(RuntimeNodeArtifact).where(
                            RuntimeNodeArtifact.node_id == current.id,
                            RuntimeNodeArtifact.logical_target
                            == artifact.logical_target,
                        )
                    ).first()
                    if installed is None:
                        installed = RuntimeNodeArtifact(
                            node_id=current.id, logical_target=artifact.logical_target
                        )
                    installed.previous_artifact_id = installed.current_artifact_id
                    installed.current_artifact_id = artifact.id
                    installed.updated_at = datetime.now(timezone.utc)
                    artifact_deployment.status = DeploymentStatus.APPLIED
                    artifact_deployment.applied_at = datetime.now(timezone.utc)
                    session.add(installed)
                else:
                    artifact_deployment.status = DeploymentStatus.FAILED
                    artifact_deployment.error = {
                        "code": message.payload.get("code", "node_apply_failed"),
                        "message": message.payload.get("message"),
                    }
                session.add(artifact_deployment)
                session.commit()
                await websocket.send_json(
                    _envelope(
                        "artifact_status_ack",
                        node.id,
                        {"deployment_id": str(artifact_deployment.id)},
                        message.message_id,
                    )
                )
            else:
                await websocket.send_json(
                    _envelope(
                        "error",
                        node.id,
                        {"code": "unknown_message_type"},
                        message.message_id,
                    )
                )
    except WebSocketDisconnect:
        pass
    finally:
        session.expire_all()
        current = session.get(RuntimeNode, node.id)
        if current and current.connection_id == connection_id:
            current.connection_id = None
            session.add(current)
            session.commit()
