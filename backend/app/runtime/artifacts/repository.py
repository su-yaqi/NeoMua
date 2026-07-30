import uuid

from sqlmodel import Session

from app.runtime.models import RuntimeArtifact


class ArtifactImmutable(ValueError):
    pass


class ArtifactRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def assert_immutable(
        self, artifact_id: uuid.UUID, content_sha256: str, storage_key: str
    ) -> RuntimeArtifact:
        artifact = self.session.get(RuntimeArtifact, artifact_id)
        if artifact is None:
            raise KeyError(artifact_id)
        if (
            artifact.content_sha256 != content_sha256
            or artifact.storage_key != storage_key
        ):
            raise ArtifactImmutable("persisted artifact content cannot be replaced")
        return artifact
