import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError
from sqlmodel import select

from app.api.deps import SessionDep
from app.core.config import settings
from app.llm_provider_service import open_secret_payload
from app.runtime.artifacts.manifest import DeploymentManifest
from app.runtime.artifacts.security import issue_artifact_download_token
from app.runtime.artifacts.service import ArtifactReleaseService
from app.runtime.artifacts.signing import configured_artifact_signer
from app.runtime.connections import (
    NodeAuthenticationError,
    authenticate_node_connection,
)
from app.runtime.enrollment import (
    recover_node_credential_rotation,
    retire_replaced_credential,
    rotate_node_credential,
)
from app.runtime.models import (
    AgentEventType,
    AgentTask,
    ArtifactDeployment,
    ArtifactRelease,
    DeploymentStatus,
    RuntimeArtifact,
    RuntimeNode,
    RuntimeNodeArtifact,
    RuntimeProfile,
    RuntimeRouteMode,
    RuntimeSecret,
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
        if not await _connection_is_current(
            websocket, session, node.id, connection_id
        ):
            return
        runtime = session.get(RuntimeProfile, task.runtime_profile_id)
        if runtime is None:
            task.status = TaskStatus.REJECTED
            task.final_result = {"code": "runtime_unavailable"}
            task.dispatch_connection_id = None
            task.dispatch_reserved_until = None
            session.add(task)
            session.commit()
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
                task.namespace_id, runtime.id, task.id, runtime.model_id
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
        if not await _connection_is_current(
            websocket, session, node.id, connection_id
        ):
            return
        await websocket.send_json(
            _envelope(
                "task_cancel",
                node.id,
                {"task_id": str(task.id), "revision": task.revision},
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
        if not await _connection_is_current(
            websocket, session, node.id, connection_id
        ):
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
                    "deployment_manifest": deployment_manifest.model_dump(mode="json"),
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
    await _send_pending_artifacts(websocket, session, node, connection_id)
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
                await websocket.close(code=4406, reason="node protocol upgrade required")
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
            if message.type == "heartbeat":
                current.last_seen_at = datetime.now(timezone.utc)
                session.add(current)
                session.commit()
                await websocket.send_json(
                    _envelope("heartbeat_ack", node.id, {}, message.message_id)
                )
                await _send_pending_control(
                    websocket, session, current, connection_id
                )
                await _send_pending_artifacts(
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
                session.commit()
                await websocket.send_json(
                    _envelope(
                        "task_accept_ack",
                        node.id,
                        {"task_id": str(task.id)},
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
            elif message.type in {"artifact_applied", "artifact_failed"}:
                deployment = session.get(
                    ArtifactDeployment, uuid.UUID(message.payload["deployment_id"])
                )
                if (
                    deployment is None
                    or deployment.node_id != current.id
                    or deployment.status != DeploymentStatus.DISPATCHED
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
                artifact = session.get(RuntimeArtifact, deployment.artifact_id)
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
                    deployment.status = DeploymentStatus.APPLIED
                    deployment.applied_at = datetime.now(timezone.utc)
                    session.add(installed)
                else:
                    deployment.status = DeploymentStatus.FAILED
                    deployment.error = {
                        "code": message.payload.get("code", "node_apply_failed"),
                        "message": message.payload.get("message"),
                    }
                session.add(deployment)
                session.commit()
                await websocket.send_json(
                    _envelope(
                        "artifact_status_ack",
                        node.id,
                        {"deployment_id": str(deployment.id)},
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
