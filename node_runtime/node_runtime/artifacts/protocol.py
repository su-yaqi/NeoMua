import json
from datetime import datetime

from pydantic import BaseModel, Field


class ArtifactFile(BaseModel):
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0, le=100 * 1024 * 1024)
    symlink: bool = False


class ArtifactManifest(BaseModel):
    schema_version: str
    artifact_id: str
    version: str
    kind: str
    logical_target: str
    files: list[ArtifactFile]

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()


class DeploymentManifest(BaseModel):
    schema_version: str
    namespace_id: str
    node_id: str
    release_id: str
    deployment_id: str
    artifact_id: str
    logical_target: str
    artifact_manifest_sha256: str
    valid_until: datetime

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
