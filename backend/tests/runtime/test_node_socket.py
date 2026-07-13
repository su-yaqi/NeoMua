import base64
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from sqlmodel import Session
from starlette.websockets import WebSocketDisconnect

from app.agent_management.capability_models import (
    McpServer,
    McpServerRevision,
    McpTargetBinding,
    McpTargetStatus,
    McpTransport,
)
from app.core.config import settings
from app.runtime.connections import node_is_online
from app.runtime.models import (
    AgentEventType,
    AgentTask,
    RuntimeJob,
    RuntimeJobKind,
    RuntimeJobStatus,
    RuntimeNode,
    RuntimeProfile,
    RuntimeRouteMode,
    RuntimeType,
)
from app.runtime.policy import TaskStatus
from app.runtime.repository import append_event_idempotent
from tests.api.routes.test_namespaces import create_namespace, namespace_headers

HARNESS_CAPABILITIES = {
    "claude_code": {
        "cli_version": "2.1.191",
        "sdk_version": "0.2.110",
        "harness_version": "0.1.0",
    }
}


def _enroll(
    client: TestClient, db: Session, headers: dict[str, str]
) -> tuple[dict, Ed25519PrivateKey]:
    namespace = create_namespace(db)
    token = client.post(
        f"{settings.API_V1_STR}/runtimes/nodes/enrollment-tokens",
        headers=namespace_headers(headers, namespace.id),
    ).json()["token"]
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    enrolled = client.post(
        f"{settings.API_V1_STR}/node/enroll",
        json={
            "token": token,
            "name": "node",
            "hostname": "node-1",
            "os_name": "linux",
            "architecture": "arm64",
            "agent_version": "0.1.0",
            "sdk_version": "0.2.110",
            "harness_capabilities": HARNESS_CAPABILITIES,
            "public_key": base64.b64encode(public).decode(),
        },
    ).json()
    return enrolled, private


def _connection_headers(enrolled: dict, private: Ed25519PrivateKey) -> dict[str, str]:
    timestamp = str(int(time.time()))
    nonce = secrets.token_urlsafe(32)
    signature = private.sign(f"neomua-ws-v1:{timestamp}:{nonce}".encode())
    return {
        "Authorization": f"Bearer {enrolled['credential']}",
        "X-Node-Protocol-Version": "2",
        "X-Node-Timestamp": timestamp,
        "X-Node-Nonce": nonce,
        "X-Node-Signature": base64.b64encode(signature).decode(),
    }


def test_presence_becomes_offline_without_revoking_pairing() -> None:
    now = datetime.now(timezone.utc)
    node = RuntimeNode(
        namespace_id=uuid.uuid4(),
        name="n",
        hostname="h",
        os_name="linux",
        architecture="arm64",
        agent_version="1",
        public_key="key",
        key_fingerprint="fingerprint",
        connection_id=uuid.uuid4(),
        last_seen_at=now,
    )
    assert node_is_online(node, now=now + timedelta(seconds=59))
    assert not node_is_online(node, now=now + timedelta(seconds=61))
    assert node.revoked_at is None


def test_authenticated_node_connects_and_heartbeats(
    client: TestClient, db: Session, superuser_token_headers: dict[str, str]
) -> None:
    enrolled, private = _enroll(client, db, superuser_token_headers)
    timestamp = str(int(time.time()))
    nonce = secrets.token_urlsafe(32)
    signature = private.sign(f"neomua-ws-v1:{timestamp}:{nonce}".encode())
    headers = {
        "Authorization": f"Bearer {enrolled['credential']}",
        "X-Node-Protocol-Version": "2",
        "X-Node-Timestamp": timestamp,
        "X-Node-Nonce": nonce,
        "X-Node-Signature": base64.b64encode(signature).decode(),
    }
    heartbeat_capabilities = {
        "claude_code": {
            "cli_version": "2.1.192",
            "sdk_version": "0.2.111",
            "harness_version": "0.1.1",
        }
    }
    with client.websocket_connect(
        f"{settings.API_V1_STR}/node/ws", headers=headers
    ) as websocket:
        hello = websocket.receive_json()
        assert hello["type"] == "hello_ack"
        message_id = str(uuid.uuid4())
        websocket.send_json(
            {
                "type": "heartbeat",
                "protocol_version": "2",
                "message_id": message_id,
                "correlation_id": None,
                "node_id": enrolled["node_id"],
                "sent_at": datetime.now(timezone.utc).isoformat(),
                "payload": {"harness_capabilities": heartbeat_capabilities},
            }
        )
        ack = websocket.receive_json()
        assert ack["type"] == "heartbeat_ack"
        assert ack["correlation_id"] == message_id

    db.expire_all()
    node = db.get(RuntimeNode, uuid.UUID(enrolled["node_id"]))
    assert node is not None
    assert node.harness_capabilities == heartbeat_capabilities


def test_node_secret_fingerprint_change_marks_mcp_target_stale(
    client: TestClient, db: Session, superuser_token_headers: dict[str, str]
) -> None:
    enrolled, private = _enroll(client, db, superuser_token_headers)
    node = db.get(RuntimeNode, uuid.UUID(enrolled["node_id"]))
    assert node is not None
    runtime = RuntimeProfile(
        namespace_id=node.namespace_id,
        runtime_type=RuntimeType.NODE,
        route_mode=RuntimeRouteMode.DIRECT_ANTHROPIC,
        model_id="claude-node",
        base_url="https://anthropic.example",
        config={"direct_compatibility_verified": True},
    )
    db.add(runtime)
    db.flush()
    node.runtime_profile_id = runtime.id
    server = McpServer(
        namespace_id=node.namespace_id,
        slug="local-mcp",
        name="Local MCP",
    )
    db.add(server)
    db.flush()
    revision = McpServerRevision(
        server_id=server.id,
        revision=1,
        transport=McpTransport.STREAMABLE_HTTP,
        config={"endpoint": "https://mcp.example"},
        config_sha256="b" * 64,
    )
    db.add(revision)
    db.flush()
    target = McpTargetBinding(
        revision_id=revision.id,
        runtime_profile_id=runtime.id,
        secret_ref="github",
        secret_fingerprint="a" * 64,
        status=McpTargetStatus.VERIFIED,
    )
    db.add(node)
    db.add(target)
    db.commit()

    with client.websocket_connect(
        f"{settings.API_V1_STR}/node/ws",
        headers=_connection_headers(enrolled, private),
    ) as websocket:
        assert websocket.receive_json()["type"] == "hello_ack"
        websocket.send_json(
            {
                "type": "heartbeat",
                "protocol_version": "2",
                "message_id": str(uuid.uuid4()),
                "correlation_id": None,
                "node_id": enrolled["node_id"],
                "sent_at": datetime.now(timezone.utc).isoformat(),
                "payload": {"mcp_secret_fingerprints": {"github": "c" * 64}},
            }
        )
        assert websocket.receive_json()["type"] == "heartbeat_ack"

    db.expire_all()
    updated = db.get(McpTargetBinding, target.id)
    assert updated is not None
    assert updated.status == McpTargetStatus.STALE
    assert updated.secret_fingerprint == "c" * 64


def test_new_connection_supersedes_old_generation(
    client: TestClient, db: Session, superuser_token_headers: dict[str, str]
) -> None:
    enrolled, private = _enroll(client, db, superuser_token_headers)
    with client.websocket_connect(
        f"{settings.API_V1_STR}/node/ws",
        headers=_connection_headers(enrolled, private),
    ) as old_websocket:
        assert old_websocket.receive_json()["type"] == "hello_ack"
        with client.websocket_connect(
            f"{settings.API_V1_STR}/node/ws",
            headers=_connection_headers(enrolled, private),
        ) as current_websocket:
            assert current_websocket.receive_json()["type"] == "hello_ack"
            old_websocket.send_json(
                {
                    "type": "heartbeat",
                    "protocol_version": "2",
                    "message_id": str(uuid.uuid4()),
                    "correlation_id": None,
                    "node_id": enrolled["node_id"],
                    "sent_at": datetime.now(timezone.utc).isoformat(),
                    "payload": {},
                }
            )
            try:
                old_websocket.receive_json()
            except WebSocketDisconnect as exc:
                assert exc.code == 4409
            else:
                raise AssertionError("superseded connection remained active")


def test_node_dispatch_ack_and_result_are_persisted(
    client: TestClient, db: Session, superuser_token_headers: dict[str, str]
) -> None:
    enrolled, private = _enroll(client, db, superuser_token_headers)
    node = db.get(RuntimeNode, uuid.UUID(enrolled["node_id"]))
    assert node is not None
    runtime = RuntimeProfile(
        namespace_id=node.namespace_id,
        runtime_type=RuntimeType.NODE,
        route_mode=RuntimeRouteMode.DIRECT_ANTHROPIC,
        model_id="claude-node",
        base_url="https://anthropic.example",
        config={"direct_compatibility_verified": True},
    )
    db.add(runtime)
    db.flush()
    node.runtime_profile_id = runtime.id
    task = AgentTask(
        namespace_id=node.namespace_id,
        runtime_profile_id=runtime.id,
        target_node_id=node.id,
        prompt="work",
        snapshot={"runtime_profile_id": str(runtime.id), "model_id": "claude-node"},
    )
    db.add(node)
    db.add(task)
    db.commit()
    db.refresh(task)
    append_event_idempotent(db, task, 0, AgentEventType.USER_MESSAGE, {"text": "work"})
    db.commit()
    timestamp = str(int(time.time()))
    nonce = secrets.token_urlsafe(32)
    headers = {
        "Authorization": f"Bearer {enrolled['credential']}",
        "X-Node-Protocol-Version": "2",
        "X-Node-Timestamp": timestamp,
        "X-Node-Nonce": nonce,
        "X-Node-Signature": base64.b64encode(
            private.sign(f"neomua-ws-v1:{timestamp}:{nonce}".encode())
        ).decode(),
    }
    with client.websocket_connect(
        f"{settings.API_V1_STR}/node/ws", headers=headers
    ) as websocket:
        assert websocket.receive_json()["type"] == "hello_ack"
        dispatch = websocket.receive_json()
        assert dispatch["type"] == "task_dispatch"
        websocket.send_json(
            {
                "type": "task_accepted",
                "protocol_version": "2",
                "message_id": str(uuid.uuid4()),
                "correlation_id": None,
                "node_id": enrolled["node_id"],
                "sent_at": datetime.now(timezone.utc).isoformat(),
                "payload": {"task_id": str(task.id), "revision": 1},
            }
        )
        assert websocket.receive_json()["type"] == "task_accept_ack"
        websocket.send_json(
            {
                "type": "task_events",
                "protocol_version": "2",
                "message_id": str(uuid.uuid4()),
                "correlation_id": None,
                "node_id": enrolled["node_id"],
                "sent_at": datetime.now(timezone.utc).isoformat(),
                "payload": {
                    "task_id": str(task.id),
                    "events": [
                        {
                            "sequence": 2,
                            "event_type": "result",
                            "payload": {"result": "done"},
                        }
                    ],
                },
            }
        )
        event_ack = websocket.receive_json()
        assert event_ack["type"] == "task_events_ack"
        assert event_ack["payload"]["through_sequence"] == 2
    db.expire_all()
    assert db.get(AgentTask, task.id).status == TaskStatus.SUCCEEDED


def test_runtime_job_dispatch_lease_and_result_are_persisted(
    client: TestClient, db: Session, superuser_token_headers: dict[str, str]
) -> None:
    enrolled, private = _enroll(client, db, superuser_token_headers)
    node = db.get(RuntimeNode, uuid.UUID(enrolled["node_id"]))
    assert node is not None
    runtime = RuntimeProfile(
        namespace_id=node.namespace_id,
        runtime_type=RuntimeType.NODE,
        route_mode=RuntimeRouteMode.DIRECT_ANTHROPIC,
        model_id="claude-node",
        base_url="https://anthropic.example",
        config={"direct_compatibility_verified": True},
    )
    db.add(runtime)
    db.flush()
    node.runtime_profile_id = runtime.id
    job = RuntimeJob(
        namespace_id=node.namespace_id,
        runtime_profile_id=runtime.id,
        target_node_id=node.id,
        kind=RuntimeJobKind.WORKFLOW_HANDLER,
        payload={"component_key": "project_delivery.prepare"},
        idempotency_key=f"node-job:{uuid.uuid4()}",
    )
    db.add(node)
    db.add(job)
    db.commit()
    db.refresh(job)

    with client.websocket_connect(
        f"{settings.API_V1_STR}/node/ws",
        headers=_connection_headers(enrolled, private),
    ) as websocket:
        assert websocket.receive_json()["type"] == "hello_ack"
        dispatch = websocket.receive_json()
        assert dispatch["type"] == "runtime_job_dispatch"
        assert dispatch["payload"]["job_id"] == str(job.id)
        websocket.send_json(
            {
                "type": "runtime_job_accepted",
                "protocol_version": "2",
                "message_id": str(uuid.uuid4()),
                "correlation_id": None,
                "node_id": enrolled["node_id"],
                "sent_at": datetime.now(timezone.utc).isoformat(),
                "payload": {"job_id": str(job.id), "revision": job.revision},
            }
        )
        websocket.send_json(
            {
                "type": "runtime_job_lease",
                "protocol_version": "2",
                "message_id": str(uuid.uuid4()),
                "correlation_id": None,
                "node_id": enrolled["node_id"],
                "sent_at": datetime.now(timezone.utc).isoformat(),
                "payload": {"job_id": str(job.id), "revision": job.revision},
            }
        )
        websocket.send_json(
            {
                "type": "runtime_job_result",
                "protocol_version": "2",
                "message_id": str(uuid.uuid4()),
                "correlation_id": None,
                "node_id": enrolled["node_id"],
                "sent_at": datetime.now(timezone.utc).isoformat(),
                "payload": {
                    "job_id": str(job.id),
                    "revision": job.revision,
                    "status": "succeeded",
                    "result": {"summary": "done"},
                    "error": None,
                },
            }
        )
        websocket.send_json(
            {
                "type": "heartbeat",
                "protocol_version": "2",
                "message_id": str(uuid.uuid4()),
                "correlation_id": None,
                "node_id": enrolled["node_id"],
                "sent_at": datetime.now(timezone.utc).isoformat(),
                "payload": {},
            }
        )
        assert websocket.receive_json()["type"] == "heartbeat_ack"

    db.expire_all()
    persisted = db.get(RuntimeJob, job.id)
    assert persisted is not None
    assert persisted.status == RuntimeJobStatus.SUCCEEDED
    assert persisted.result == {"summary": "done"}


def test_side_effecting_runtime_job_rejection_requires_manual_resolution(
    client: TestClient, db: Session, superuser_token_headers: dict[str, str]
) -> None:
    enrolled, private = _enroll(client, db, superuser_token_headers)
    node = db.get(RuntimeNode, uuid.UUID(enrolled["node_id"]))
    assert node is not None
    runtime = RuntimeProfile(
        namespace_id=node.namespace_id,
        runtime_type=RuntimeType.NODE,
        route_mode=RuntimeRouteMode.DIRECT_ANTHROPIC,
        model_id="claude-node",
        base_url="https://anthropic.example",
        config={"direct_compatibility_verified": True},
    )
    db.add(runtime)
    db.flush()
    node.runtime_profile_id = runtime.id
    job = RuntimeJob(
        namespace_id=node.namespace_id,
        runtime_profile_id=runtime.id,
        target_node_id=node.id,
        kind=RuntimeJobKind.WORKFLOW_HANDLER,
        payload={"component_key": "project_delivery.prepare"},
        side_effecting=True,
        idempotency_key=f"node-job:{uuid.uuid4()}",
    )
    db.add(node)
    db.add(job)
    db.commit()
    db.refresh(job)

    with client.websocket_connect(
        f"{settings.API_V1_STR}/node/ws",
        headers=_connection_headers(enrolled, private),
    ) as websocket:
        assert websocket.receive_json()["type"] == "hello_ack"
        assert websocket.receive_json()["type"] == "runtime_job_dispatch"
        websocket.send_json(
            {
                "type": "runtime_job_rejected",
                "protocol_version": "2",
                "message_id": str(uuid.uuid4()),
                "correlation_id": None,
                "node_id": enrolled["node_id"],
                "sent_at": datetime.now(timezone.utc).isoformat(),
                "payload": {
                    "job_id": str(job.id),
                    "revision": job.revision,
                    "reason": "durable revision conflict",
                },
            }
        )
        rejected = websocket.receive_json()
        assert rejected["type"] == "runtime_job_rejected_ack"
        assert rejected["payload"]["status"] == "needs_manual_resolution"

    db.expire_all()
    persisted = db.get(RuntimeJob, job.id)
    assert persisted is not None
    assert persisted.status == RuntimeJobStatus.NEEDS_MANUAL_RESOLUTION
    assert persisted.error == {
        "code": "node_runtime_job_dispatch_conflict",
        "reason": "durable revision conflict",
    }
