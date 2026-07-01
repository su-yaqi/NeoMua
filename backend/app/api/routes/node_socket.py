import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError

from app.api.deps import SessionDep
from app.runtime.connections import NodeAuthenticationError, authenticate_node_connection
from app.runtime.enrollment import retire_replaced_credential, rotate_node_credential
from app.runtime.models import RuntimeNode

router = APIRouter(prefix="/node", tags=["node-socket"])


class Envelope(BaseModel):
    type: str
    protocol_version: Literal["1"]
    message_id: uuid.UUID
    correlation_id: uuid.UUID | None = None
    node_id: uuid.UUID
    sent_at: datetime
    payload: dict[str, Any]


def _envelope(
    message_type: str, node_id: uuid.UUID, payload: dict[str, Any],
    correlation_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    return Envelope(
        type=message_type,
        protocol_version="1",
        message_id=uuid.uuid4(),
        correlation_id=correlation_id,
        node_id=node_id,
        sent_at=datetime.now(timezone.utc),
        payload=payload,
    ).model_dump(mode="json")


@router.websocket("/ws")
async def node_websocket(websocket: WebSocket, session: SessionDep) -> None:
    authorization = websocket.headers.get("authorization", "")
    timestamp = websocket.headers.get("x-node-timestamp", "")
    signature = websocket.headers.get("x-node-signature", "")
    if not authorization.startswith("Bearer "):
        await websocket.close(code=4401, reason="node credential required")
        return
    try:
        node, credential = authenticate_node_connection(
            session, authorization[7:], timestamp, signature
        )
    except NodeAuthenticationError as exc:
        await websocket.close(code=4403, reason=str(exc))
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
    if credential.expires_at <= now + timedelta(days=30):
        await websocket.send_json(
            _envelope(
                "credential_rotation_required", node.id,
                {"credential_expires_at": credential.expires_at.isoformat()},
            )
        )
    try:
        while True:
            raw = await websocket.receive_json()
            try:
                message = Envelope.model_validate(raw)
            except ValidationError as exc:
                await websocket.send_json(
                    _envelope("error", node.id, {"code": "invalid_envelope", "detail": str(exc)})
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
            elif message.type == "reconcile":
                current.last_seen_at = datetime.now(timezone.utc)
                session.add(current)
                session.commit()
                await websocket.send_json(
                    _envelope(
                        "reconcile_ack", node.id,
                        {"config_revision": current.config_revision}, message.message_id,
                    )
                )
            elif message.type == "rotate_credential":
                try:
                    replacement, token = rotate_node_credential(
                        session, current, credential
                    )
                except ValueError as exc:
                    await websocket.send_json(
                        _envelope(
                            "error", node.id,
                            {"code": "credential_rotation_rejected", "detail": str(exc)},
                            message.message_id,
                        )
                    )
                    continue
                session.commit()
                await websocket.send_json(
                    _envelope(
                        "credential_rotated", node.id,
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
                            "error", node.id,
                            {"code": "credential_rotation_ack_rejected", "detail": str(exc)},
                            message.message_id,
                        )
                    )
                    continue
                session.commit()
                await websocket.send_json(
                    _envelope(
                        "credential_rotation_acknowledged", node.id, {}, message.message_id
                    )
                )
            else:
                await websocket.send_json(
                    _envelope("error", node.id, {"code": "unknown_message_type"}, message.message_id)
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
