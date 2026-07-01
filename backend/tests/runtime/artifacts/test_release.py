from datetime import datetime, timedelta, timezone

from sqlmodel import Session

from app.runtime.artifacts.manifest import ArtifactKind, LogicalTarget
from app.runtime.artifacts.service import ArtifactReleaseService
from app.runtime.models import (
    ArtifactDeployment,
    ArtifactRelease,
    DeploymentStatus,
    RuntimeArtifact,
    RuntimeNode,
)
from tests.api.routes.test_namespaces import create_namespace


def test_offline_node_does_not_apply_expired_release(db: Session) -> None:
    namespace = create_namespace(db)
    artifact = RuntimeArtifact(
        namespace_id=namespace.id, kind=ArtifactKind.SKILL,
        logical_target=LogicalTarget.SKILLS, version="1", content_sha256="a" * 64,
        storage_key="key", size=1, manifest={}, signature="s", signing_public_key="p",
    )
    node = RuntimeNode(
        namespace_id=namespace.id, name="n", hostname="h", os_name="linux",
        architecture="arm64", agent_version="1", public_key="k",
        key_fingerprint="a" * 63 + "b",
    )
    db.add(artifact)
    db.add(node)
    db.flush()
    release = ArtifactRelease(
        namespace_id=namespace.id, artifact_id=artifact.id,
        valid_until=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    db.add(release)
    db.flush()
    deployment = ArtifactDeployment(
        namespace_id=namespace.id, release_id=release.id,
        node_id=node.id, artifact_id=artifact.id,
    )
    db.add(deployment)
    db.commit()
    assert ArtifactReleaseService(db).pending_for_node(node.id) == []
    db.refresh(deployment)
    assert deployment.status == DeploymentStatus.EXPIRED
