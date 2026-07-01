import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select
import jwt

from app.core.config import settings
from app.runtime.models import NodeCredential, NodeEnrollmentToken, RuntimeNode


class EnrollmentTokenInvalid(ValueError):
    pass


def _token_hash(raw_token: str) -> str:
    key = hmac.new(
        settings.SECRET_KEY.encode(), b"neomua-node-enrollment", hashlib.sha256
    ).digest()
    return hmac.new(key, raw_token.encode(), hashlib.sha256).hexdigest()


def create_enrollment_token(
    session: Session,
    namespace_id: uuid.UUID,
    created_by: uuid.UUID,
    *,
    ttl: timedelta = timedelta(minutes=15),
) -> tuple[str, NodeEnrollmentToken]:
    raw = f"nmenr_{secrets.token_urlsafe(32)}"
    record = NodeEnrollmentToken(
        namespace_id=namespace_id,
        token_hash=_token_hash(raw),
        expires_at=datetime.now(timezone.utc) + ttl,
        created_by=created_by,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return raw, record


def consume_enrollment_token(
    session: Session, raw_token: str
) -> NodeEnrollmentToken:
    now = datetime.now(timezone.utc)
    record = session.exec(
        select(NodeEnrollmentToken)
        .where(NodeEnrollmentToken.token_hash == _token_hash(raw_token))
        .with_for_update()
    ).first()
    if (
        record is None
        or record.consumed_at is not None
        or record.revoked_at is not None
        or record.expires_at <= now
    ):
        session.rollback()
        raise EnrollmentTokenInvalid("enrollment token is invalid or expired")
    record.consumed_at = now
    session.add(record)
    session.flush()
    return record


def _credential_token(node: RuntimeNode, credential: NodeCredential) -> str:
    return jwt.encode(
        {
            "aud": "neomua-node-runtime",
            "sub": str(node.id),
            "namespace_id": str(node.namespace_id),
            "credential_id": str(credential.id),
            "key_fingerprint": node.key_fingerprint,
            "iat": credential.issued_at,
            "exp": credential.expires_at,
        },
        settings.SECRET_KEY,
        algorithm="HS256",
    )


def issue_node_credential(
    session: Session,
    node: RuntimeNode,
    *,
    ttl: timedelta = timedelta(days=90),
) -> tuple[NodeCredential, str]:
    now = datetime.now(timezone.utc)
    credential = NodeCredential(
        node_id=node.id,
        namespace_id=node.namespace_id,
        key_fingerprint=node.key_fingerprint,
        issued_at=now,
        expires_at=now + ttl,
    )
    session.add(credential)
    session.flush()
    return credential, _credential_token(node, credential)


def rotate_node_credential(
    session: Session,
    node: RuntimeNode,
    current: NodeCredential,
) -> tuple[NodeCredential, str]:
    if (
        current.node_id != node.id
        or current.revoked_at is not None
        or current.replaced_by_id is not None
    ):
        raise ValueError("credential cannot be rotated")
    replacement, token = issue_node_credential(session, node)
    current.replaced_by_id = replacement.id
    session.add(current)
    session.flush()
    return replacement, token


def retire_replaced_credential(
    session: Session,
    node: RuntimeNode,
    old_credential_id: uuid.UUID,
    new_credential_id: uuid.UUID,
) -> None:
    old = session.get(NodeCredential, old_credential_id)
    new = session.get(NodeCredential, new_credential_id)
    if (
        old is None
        or new is None
        or old.node_id != node.id
        or new.node_id != node.id
        or old.replaced_by_id != new.id
    ):
        raise ValueError("credential rotation acknowledgement is invalid")
    old.revoked_at = datetime.now(timezone.utc)
    session.add(old)
    session.flush()
