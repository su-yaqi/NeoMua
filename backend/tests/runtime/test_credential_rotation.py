import base64
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlmodel import Session

from app.runtime.enrollment import (
    issue_node_credential,
    recover_node_credential_rotation,
    retire_replaced_credential,
    rotate_node_credential,
)
from app.runtime.models import NodeCredential, RuntimeNode
from tests.api.routes.test_namespaces import create_namespace


def test_rotation_issues_90_day_credential_and_retires_old(db: Session) -> None:
    namespace = create_namespace(db)
    public = (
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    )
    node = RuntimeNode(
        namespace_id=namespace.id,
        name="node",
        hostname="host",
        os_name="linux",
        architecture="arm64",
        agent_version="1",
        public_key=base64.b64encode(public).decode(),
        key_fingerprint="f" * 64,
    )
    db.add(node)
    db.flush()
    old, _ = issue_node_credential(db, node, ttl=timedelta(days=60))
    new, token = rotate_node_credential(db, node, old)
    assert token
    assert old.replaced_by_id == new.id
    assert new.expires_at > datetime.now(timezone.utc) + timedelta(days=89)
    retire_replaced_credential(db, node, old.id, new.id)
    assert old.revoked_at is not None


def test_one_day_offline_does_not_remove_node_pairing(db: Session) -> None:
    namespace = create_namespace(db)
    node = RuntimeNode(
        namespace_id=namespace.id,
        name="node",
        hostname="host",
        os_name="linux",
        architecture="arm64",
        agent_version="1",
        public_key="key",
        key_fingerprint="e" * 64,
        last_seen_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    assert node.revoked_at is None
    assert node.public_key == "key"


def test_unacknowledged_rotation_recovers_after_restart(db: Session) -> None:
    namespace = create_namespace(db)
    node = RuntimeNode(
        namespace_id=namespace.id,
        name="node",
        hostname="host",
        os_name="linux",
        architecture="arm64",
        agent_version="1",
        public_key="key",
        key_fingerprint="d" * 64,
    )
    db.add(node)
    db.flush()
    old, _ = issue_node_credential(db, node, ttl=timedelta(days=60))
    abandoned, _ = rotate_node_credential(db, node, old)
    old_id = old.id
    abandoned_id = abandoned.id
    db.commit()
    db.expire_all()

    persisted_node = db.get(RuntimeNode, node.id)
    persisted_old = db.get(NodeCredential, old_id)
    assert persisted_node is not None
    assert persisted_old is not None
    replacement, token = recover_node_credential_rotation(
        db, persisted_node, persisted_old
    )
    db.commit()

    assert token
    assert replacement.id != abandoned_id
    assert persisted_old.replaced_by_id == replacement.id
    abandoned_after_recovery = db.get(NodeCredential, abandoned_id)
    assert abandoned_after_recovery is not None
    assert abandoned_after_recovery.revoked_at is not None
