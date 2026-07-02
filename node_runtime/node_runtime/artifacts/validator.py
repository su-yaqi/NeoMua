import base64
import hashlib
import stat
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from node_runtime.artifacts.protocol import ArtifactManifest, DeploymentManifest


class ArtifactValidationError(ValueError):
    pass


def _verify(public_key: str, payload: bytes, signature: str) -> None:
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key)).verify(
            base64.b64decode(signature), payload
        )
    except (ValueError, InvalidSignature) as exc:
        raise ArtifactValidationError("artifact signature is invalid") from exc


def _safe_path(value: str) -> PurePosixPath:
    if not value or "\\" in value or "//" in value:
        raise ArtifactValidationError("artifact path is unsafe")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or "." in path.parts
        or str(path) != value
    ):
        raise ArtifactValidationError("artifact path is unsafe")
    return path


def validate_instruction(
    command: dict, *, node_id: str, namespace_id: str
) -> tuple[ArtifactManifest, DeploymentManifest]:
    artifact = ArtifactManifest.model_validate(command["manifest"])
    deployment = DeploymentManifest.model_validate(command["deployment_manifest"])
    if deployment.node_id != node_id:
        raise ArtifactValidationError("deployment node scope mismatch")
    if deployment.namespace_id != namespace_id:
        raise ArtifactValidationError("deployment namespace scope mismatch")
    if deployment.valid_until <= datetime.now(timezone.utc):
        raise ArtifactValidationError("deployment expired")
    if (
        deployment.artifact_id != artifact.artifact_id
        or deployment.logical_target != artifact.logical_target
        or command.get("artifact_id") != artifact.artifact_id
        or command.get("deployment_id") != deployment.deployment_id
    ):
        raise ArtifactValidationError("deployment artifact scope mismatch")
    manifest_hash = hashlib.sha256(artifact.canonical_bytes()).hexdigest()
    if deployment.artifact_manifest_sha256 != manifest_hash:
        raise ArtifactValidationError("artifact manifest hash mismatch")
    _verify(
        command["signing_public_key"], artifact.canonical_bytes(), command["signature"]
    )
    _verify(
        command["signing_public_key"],
        deployment.canonical_bytes(),
        command["deployment_signature"],
    )
    if any(item.symlink for item in artifact.files):
        raise ArtifactValidationError("artifact symlinks are forbidden")
    for item in artifact.files:
        _safe_path(item.path)
    return artifact, deployment


def validate_archive(
    archive_path: Path, manifest: ArtifactManifest, expected_content_sha256: str
) -> None:
    digest = hashlib.sha256()
    with archive_path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != expected_content_sha256:
        raise ArtifactValidationError("artifact archive hash mismatch")
    expected = {item.path: item for item in manifest.files}
    seen: set[str] = set()
    total_size = 0
    try:
        archive = zipfile.ZipFile(archive_path)
    except zipfile.BadZipFile as exc:
        raise ArtifactValidationError("artifact archive is invalid") from exc
    with archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            _safe_path(info.filename)
            if (info.external_attr >> 16) & 0o170000 == stat.S_IFLNK:
                raise ArtifactValidationError("artifact archive contains a symlink")
            item = expected.get(info.filename)
            if item is None or info.filename in seen:
                raise ArtifactValidationError("archive content differs from manifest")
            file_hash = hashlib.sha256()
            size = 0
            with archive.open(info) as source:
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    total_size += len(chunk)
                    if size > 100 * 1024 * 1024 or total_size > 1024 * 1024 * 1024:
                        raise ArtifactValidationError(
                            "artifact expanded size exceeds limit"
                        )
                    file_hash.update(chunk)
            if size != item.size or file_hash.hexdigest() != item.sha256:
                raise ArtifactValidationError("artifact file hash or size mismatch")
            seen.add(info.filename)
    if seen != set(expected):
        raise ArtifactValidationError("artifact archive is missing manifest files")


def validate_directory(root: Path, manifest: ArtifactManifest) -> None:
    expected = {item.path: item for item in manifest.files}
    actual: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ArtifactValidationError("installed artifact contains a symlink")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        item = expected.get(relative)
        if item is None:
            raise ArtifactValidationError("installed artifact differs from manifest")
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                digest.update(chunk)
        if size != item.size or digest.hexdigest() != item.sha256:
            raise ArtifactValidationError("installed artifact hash mismatch")
        actual.add(relative)
    if actual != set(expected):
        raise ArtifactValidationError("installed artifact is incomplete")
