import base64
import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, delete

from app.core.config import settings
from app.runtime.models import NodeCredential, NodeHandshakeNonce, RuntimeNode


class NodeAuthenticationError(ValueError):
    pass


def node_is_online(
    node: RuntimeNode, *, now: datetime | None = None, timeout_seconds: int = 60
) -> bool:
    current = now or datetime.now(timezone.utc)
    return bool(
        node.connection_id
        and node.last_seen_at
        and node.last_seen_at >= current - timedelta(seconds=timeout_seconds)
        and node.revoked_at is None
    )


def authenticate_node_connection(
    session: Session,
    credential_token: str,
    timestamp: str,
    nonce: str,
    signature: str,
) -> tuple[RuntimeNode, NodeCredential]:
    try:
        timestamp_value = int(timestamp)
        signed_at = datetime.fromtimestamp(timestamp_value, timezone.utc)
        now = datetime.now(timezone.utc)
        if abs((now - signed_at).total_seconds()) > 60:
            raise NodeAuthenticationError("node handshake timestamp is stale")
        nonce_bytes = base64.urlsafe_b64decode(nonce + "=" * (-len(nonce) % 4))
        if len(nonce_bytes) < 24:
            raise NodeAuthenticationError("node handshake nonce is invalid")
        claims = jwt.decode(
            credential_token,
            settings.SECRET_KEY,
            algorithms=["HS256"],
            audience="neomua-node-runtime",
        )
        node_id = uuid.UUID(claims["sub"])
        credential_id = uuid.UUID(claims["credential_id"])
    except (ValueError, KeyError, jwt.PyJWTError) as exc:
        if isinstance(exc, NodeAuthenticationError):
            raise
        raise NodeAuthenticationError("invalid node credential") from exc
    node = session.get(RuntimeNode, node_id)
    credential = session.get(NodeCredential, credential_id)
    if (
        node is None
        or credential is None
        or credential.node_id != node.id
        or node.revoked_at is not None
        or credential.revoked_at is not None
        or claims.get("namespace_id") != str(node.namespace_id)
        or claims.get("key_fingerprint") != node.key_fingerprint
    ):
        raise NodeAuthenticationError("node credential is revoked or out of scope")
    try:
        public_key = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(node.public_key)
        )
        public_key.verify(
            base64.b64decode(signature, validate=True),
            f"neomua-ws-v1:{timestamp}:{nonce}".encode(),
        )
    except (ValueError, InvalidSignature) as exc:
        raise NodeAuthenticationError("invalid device signature") from exc
    nonce_hash = hashlib.sha256(nonce_bytes).hexdigest()
    session.exec(delete(NodeHandshakeNonce).where(NodeHandshakeNonce.expires_at < now))
    session.add(
        NodeHandshakeNonce(
            node_id=node.id,
            nonce_hash=nonce_hash,
            expires_at=now + timedelta(minutes=2),
        )
    )
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise NodeAuthenticationError("node handshake was replayed") from exc
    return node, credential
