import base64
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import settings
from app.runtime.connections import node_is_online
from app.runtime.models import (
    AgentEventType,
    AgentTask,
    RuntimeNode,
    RuntimeProfile,
    RuntimeRouteMode,
    RuntimeType,
)
from app.runtime.policy import TaskStatus
from app.runtime.repository import append_event_idempotent
from tests.api.routes.test_namespaces import create_namespace, namespace_headers


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
            "public_key": base64.b64encode(public).decode(),
        },
    ).json()
    return enrolled, private


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
        "X-Node-Timestamp": timestamp,
        "X-Node-Nonce": nonce,
        "X-Node-Signature": base64.b64encode(signature).decode(),
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
                "protocol_version": "1",
                "message_id": message_id,
                "correlation_id": None,
                "node_id": enrolled["node_id"],
                "sent_at": datetime.now(timezone.utc).isoformat(),
                "payload": {},
            }
        )
        ack = websocket.receive_json()
        assert ack["type"] == "heartbeat_ack"
        assert ack["correlation_id"] == message_id


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
    timestamp = str(int(time.time()))
    nonce = secrets.token_urlsafe(32)
    headers = {
        "Authorization": f"Bearer {enrolled['credential']}",
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
                "protocol_version": "1",
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
                "protocol_version": "1",
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
