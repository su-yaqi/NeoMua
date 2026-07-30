import uuid

import pytest
from sqlmodel import Session

from app.runtime.artifacts.manifest import ArtifactKind, LogicalTarget
from app.runtime.artifacts.repository import ArtifactImmutable, ArtifactRepository
from app.runtime.models import RuntimeArtifact
from tests.api.routes.test_namespaces import create_namespace


def test_artifact_content_cannot_be_replaced(db: Session) -> None:
    namespace = create_namespace(db)
    artifact = RuntimeArtifact(
        namespace_id=namespace.id,
        kind=ArtifactKind.SKILL,
        logical_target=LogicalTarget.SKILLS,
        version="1.0.0",
        content_sha256="a" * 64,
        storage_key="sha256/aa/" + "a" * 64,
        size=10,
        manifest={},
        signature="signature",
        signing_public_key="public",
    )
    db.add(artifact)
    db.commit()
    repository = ArtifactRepository(db)
    repository.assert_immutable(artifact.id, "a" * 64, artifact.storage_key)
    with pytest.raises(ArtifactImmutable):
        repository.assert_immutable(artifact.id, "b" * 64, artifact.storage_key)


def test_missing_artifact_is_not_replaceable(db: Session) -> None:
    with pytest.raises(KeyError):
        ArtifactRepository(db).assert_immutable(uuid.uuid4(), "a" * 64, "key")
