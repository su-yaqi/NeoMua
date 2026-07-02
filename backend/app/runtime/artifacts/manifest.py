import json
from datetime import datetime
from enum import Enum
from pathlib import PurePosixPath

from pydantic import BaseModel, Field, model_validator

MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_FILES = 10_000


class UnsafeArtifactPath(ValueError):
    pass


class ArtifactKind(str, Enum):
    AGENT = "agent"
    SKILL = "skill"
    MCP = "mcp"
    CLI_CONFIG = "cli_config"
    WORKSPACE_CONTENT = "workspace_content"


class LogicalTarget(str, Enum):
    AGENTS = "agents"
    SKILLS = "skills"
    MCP = "mcp"
    CLI = "cli"
    WORKSPACE = "workspace"


def validate_relative_path(value: str) -> str:
    if not value or "\\" in value or "//" in value:
        raise UnsafeArtifactPath("artifact path is not normalized POSIX")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise UnsafeArtifactPath("artifact path must be a safe relative path")
    if str(path) != value:
        raise UnsafeArtifactPath("artifact path is not canonical")
    return value


class ArtifactFile(BaseModel):
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0, le=MAX_FILE_BYTES)
    symlink: bool = False

    @model_validator(mode="after")
    def validate_entry(self) -> "ArtifactFile":
        self.path = validate_relative_path(self.path)
        if self.symlink:
            raise ValueError("artifact symlink entries are forbidden")
        return self


class ArtifactManifest(BaseModel):
    schema_version: str = "1"
    artifact_id: str
    version: str = Field(min_length=1, max_length=128)
    kind: ArtifactKind
    logical_target: LogicalTarget
    files: list[ArtifactFile] = Field(max_length=MAX_FILES)

    @model_validator(mode="after")
    def validate_limits_and_target(self) -> "ArtifactManifest":
        if sum(item.size for item in self.files) > MAX_TOTAL_BYTES:
            raise ValueError("artifact total size exceeds limit")
        paths = [item.path for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("artifact manifest contains duplicate paths")
        allowed = {
            ArtifactKind.AGENT: LogicalTarget.AGENTS,
            ArtifactKind.SKILL: LogicalTarget.SKILLS,
            ArtifactKind.MCP: LogicalTarget.MCP,
            ArtifactKind.CLI_CONFIG: LogicalTarget.CLI,
            ArtifactKind.WORKSPACE_CONTENT: LogicalTarget.WORKSPACE,
        }
        if allowed[self.kind] != self.logical_target:
            raise ValueError("artifact kind does not match logical target")
        return self

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()


class DeploymentManifest(BaseModel):
    schema_version: str = "1"
    namespace_id: str
    node_id: str
    release_id: str
    deployment_id: str
    artifact_id: str
    logical_target: LogicalTarget
    artifact_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    valid_until: datetime

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
