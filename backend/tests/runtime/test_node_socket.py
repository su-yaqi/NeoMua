import base64
import time
import uuid
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import settings
from app.runtime.connections import node_is_online
from app.runtime.models import RuntimeNode
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
            "token": token, "name": "node", "hostname": "node-1",
            "os_name": "linux", "architecture": "arm64",
            "agent_version": "0.1.0", "sdk_version": "0.2.110",
            "public_key": base64.b64encode(public).decode(),
        },
    ).json()
    return enrolled, private


def test_presence_becomes_offline_without_revoking_pairing() -> None:
    now = datetime.now(timezone.utc)
    node = RuntimeNode(
        namespace_id=uuid.uuid4(), name="n", hostname="h", os_name="linux",
        architecture="arm64", agent_version="1", public_key="key",
        key_fingerprint="fingerprint", connection_id=uuid.uuid4(),
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
    signature = private.sign(f"neomua-ws-v1:{timestamp}".encode())
    headers = {
        "Authorization": f"Bearer {enrolled['credential']}",
        "X-Node-Timestamp": timestamp,
        "X-Node-Signature": base64.b64encode(signature).decode(),
    }
    with client.websocket_connect(
        f"{settings.API_V1_STR}/node/ws", headers=headers
    ) as websocket:
        hello = websocket.receive_json()
        assert hello["type"] == "hello_ack"
        message_id = str(uuid.uuid4())
        websocket.send_json({
            "type": "heartbeat", "protocol_version": "1",
            "message_id": message_id, "correlation_id": None,
            "node_id": enrolled["node_id"],
            "sent_at": datetime.now(timezone.utc).isoformat(), "payload": {},
        })
        ack = websocket.receive_json()
        assert ack["type"] == "heartbeat_ack"
        assert ack["correlation_id"] == message_id
